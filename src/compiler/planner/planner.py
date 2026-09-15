"""执行计划生成器：AST -> 逻辑执行计划。

两段式流水：
  1. build_plan —— 结构翻译，产出"原始计划"；
  2. optimizer  —— 规则改写，产出"优化后计划"（常量折叠 / 恒真消除 / NOT 消除等）。

计划形状：
    单表     Project ← [Sort] ← [Filter(HAVING)] ← [Aggregate] ← [Filter] ← SeqScan
    多表     Project ← [Sort] ← [Filter(HAVING)] ← [Aggregate] ← [Filter] ← Join(…)
    EXPLAIN  直接产出被解释语句的计划（由编译器门面标记为"只展示不执行"）。

对外入口 plan() 返回优化后的计划；plan_with_optimization() 同时返回原始计划
与命中的规则说明，便于展示优化前后差异。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..errors import PlannerError
from ..parser.ast_nodes import (BinaryOp, ColumnRef, CreateTable, Delete, Expr,
                                Explain, FunctionCall, Insert, Literal, Select,
                                Statement, UnaryOp, iter_aggregates)
from ..semantic.catalog import Catalog
from .optimizer import optimize_plan
from .plan_nodes import (AggregatePlan, AggregateSpec, CreateTablePlan,
                         DeletePlan, FilterPlan, InsertPlan, JoinPlan,
                         PlanNode, ProjectPlan, SeqScanPlan, SortKey, SortPlan)


def build_plan(statement: Statement, catalog: Catalog) -> PlanNode:
    """把单条语句翻译为**未经优化**的逻辑执行计划（纯结构翻译）。"""
    if isinstance(statement, Explain):
        return build_plan(statement.statement, catalog)
    if isinstance(statement, CreateTable):
        return _plan_create_table(statement)
    if isinstance(statement, Insert):
        return _plan_insert(statement, catalog)
    if isinstance(statement, Select):
        return _plan_select(statement, catalog)
    if isinstance(statement, Delete):
        return _plan_delete(statement)
    raise PlannerError(f"不支持的语句类型：{type(statement).__name__}",
                       statement.line, statement.col)


def plan(statement: Statement, catalog: Catalog, optimize: bool = True) -> PlanNode:
    """生成逻辑执行计划（默认应用规则优化）。"""
    raw = build_plan(statement, catalog)
    if not optimize:
        return raw
    return optimize_plan(raw)[0]


def plan_with_optimization(statement: Statement, catalog: Catalog
                           ) -> Tuple[PlanNode, PlanNode, List[str]]:
    """生成计划，并返回（原始计划，优化后计划，命中的优化规则说明列表）。"""
    raw = build_plan(statement, catalog)
    optimized, applied = optimize_plan(raw)
    return raw, optimized, applied


# ----------------------------------------------------------------------
# DDL / DML
# ----------------------------------------------------------------------
def _plan_create_table(stmt: CreateTable) -> CreateTablePlan:
    columns = [
        {"name": c.name, "type": c.type, "length": c.length}
        for c in stmt.columns
    ]
    return CreateTablePlan(stmt.table_name, columns)


def _plan_insert(stmt: Insert, catalog: Catalog) -> InsertPlan:
    if stmt.resolved_columns is None:
        schema = catalog.get_table(stmt.table_name)
        if schema is None:
            raise PlannerError(f"缺失语义信息：表 '{stmt.table_name}' 未在 Catalog 中注册",
                               stmt.line, stmt.col)
        columns = [c.name for c in schema.columns]
    else:
        columns = list(stmt.resolved_columns)
    return InsertPlan(stmt.table_name, columns, stmt.rows)


def _plan_delete(stmt: Delete) -> DeletePlan:
    return DeletePlan(stmt.table_name, stmt.where)


# ----------------------------------------------------------------------
# SELECT
# ----------------------------------------------------------------------
def _plan_select(stmt: Select, catalog: Catalog) -> PlanNode:
    node = _plan_sources(stmt, catalog)

    if stmt.where is not None:
        node = FilterPlan(stmt.where, node)

    rewrite: Optional[Dict[str, str]] = None
    if stmt.has_aggregate:
        node, rewrite = _build_aggregate(stmt, node)
        if stmt.having is not None:
            node = FilterPlan(_apply_aggregate_rewrite(stmt.having, rewrite), node)
        if stmt.order_by:
            keys = [SortKey(_apply_aggregate_rewrite(_order_expr(o, stmt), rewrite),
                            o.desc) for o in stmt.order_by]
            node = SortPlan(keys, node)
    elif stmt.order_by:
        keys = [SortKey(_order_expr(o, stmt), o.desc) for o in stmt.order_by]
        node = SortPlan(keys, node)

    return _build_project(stmt, node, rewrite)


def _plan_sources(stmt: Select, catalog: Catalog) -> PlanNode:
    """构造数据源：单表 SeqScan，或多表 Join 链。"""
    if not stmt.is_multi_table:
        return SeqScanPlan(stmt.table_name)

    node: PlanNode = SeqScanPlan(stmt.table_name,
                                 source=stmt.from_alias or stmt.table_name)
    for join in stmt.joins:
        right = SeqScanPlan(join.table.name, source=join.table.source)
        right_columns = _qualified_columns(join.table.name, join.table.source,
                                           catalog, join.line, join.col)
        node = JoinPlan(join.join_type, node, right, join.on, right_columns)
    return node


def _qualified_columns(table: str, source: str, catalog: Catalog,
                       line: int, col: int) -> List[str]:
    schema = catalog.get_table(table)
    if schema is None:
        raise PlannerError(f"缺失语义信息：表 '{table}' 未在 Catalog 中注册", line, col)
    return [f"{source}.{c.name}" for c in schema.columns]


# ----------------------------------------------------------------------
# 聚合
# ----------------------------------------------------------------------
def _build_aggregate(stmt: Select, node: PlanNode) -> Tuple[PlanNode, Dict[str, str]]:
    """构造 AggregatePlan，并返回"聚合改写表"供上层（HAVING / ORDER BY / Project）复用。

    改写表把 <表达式文本> 映射到聚合输出行中的列名（label），
    这样 HAVING / ORDER BY / 投影都能直接按列名在聚合结果上求值。
    """
    group_labels = [_key_label(g) for g in stmt.group_by]

    specs: List[AggregateSpec] = []
    seen = set()
    sources: List[Optional[Expr]] = list(stmt.columns)
    if stmt.having is not None:
        sources.append(stmt.having)
    sources.extend(o.expr for o in stmt.order_by)
    for src in sources:
        for call in iter_aggregates(src):
            label = call.label()
            if label in seen:
                continue
            seen.add(label)
            specs.append(AggregateSpec(call.name.upper(), label, call.star, call.arg))

    rewrite: Dict[str, str] = {}
    for group, label in zip(stmt.group_by, group_labels):
        rewrite[str(group).lower()] = label
    for spec in specs:
        rewrite[spec.label.lower()] = spec.label

    plan = AggregatePlan(list(stmt.group_by), group_labels, specs, node)
    return plan, rewrite


def _apply_aggregate_rewrite(expr: Optional[Expr], rewrite: Dict[str, str]) -> Optional[Expr]:
    """把聚合查询中的表达式改写成"对聚合结果行的列引用"。"""
    if expr is None:
        return None

    key = str(expr).lower()
    if key in rewrite:
        return ColumnRef(rewrite[key], getattr(expr, "line", 0), getattr(expr, "col", 0))

    if isinstance(expr, FunctionCall):
        label = expr.label()
        if label.lower() in rewrite:
            return ColumnRef(rewrite[label.lower()], expr.line, expr.col)
        return expr
    if isinstance(expr, BinaryOp):
        return BinaryOp(expr.op,
                        _apply_aggregate_rewrite(expr.left, rewrite),
                        _apply_aggregate_rewrite(expr.right, rewrite),
                        expr.line, expr.col)
    if isinstance(expr, UnaryOp):
        return UnaryOp(expr.op, _apply_aggregate_rewrite(expr.operand, rewrite),
                       expr.line, expr.col)
    return expr


def _key_label(expr: Expr) -> str:
    if isinstance(expr, ColumnRef):
        return expr.resolved or str(expr)
    if isinstance(expr, FunctionCall):
        return expr.label()
    return str(expr)


# ----------------------------------------------------------------------
# 排序 / 投影
# ----------------------------------------------------------------------
def _order_expr(order, stmt: Select) -> Expr:
    """ORDER BY 中可以直接写投影别名或列序号（1 起），这里替换回对应表达式。"""
    expr = order.expr
    if isinstance(expr, Literal) and expr.value_type == "INT":
        position = int(expr.value)
        if 1 <= position <= len(stmt.columns):
            return stmt.columns[position - 1]
        raise PlannerError(f"ORDER BY 序号 {position} 超出投影列范围"
                           f"（共 {len(stmt.columns)} 列）", expr.line, expr.col)
    if isinstance(expr, ColumnRef) and not expr.table:
        lowered = expr.name.lower()
        for item, alias in zip(stmt.columns, stmt.aliases):
            if alias and alias.lower() == lowered:
                return item
    return expr


def _build_project(stmt: Select, node: PlanNode,
                   rewrite: Optional[Dict[str, str]] = None) -> PlanNode:
    if stmt.star:
        return ProjectPlan("*", node)

    plain = _plain_column_names(stmt)
    if plain is not None:
        return ProjectPlan(plain, node)

    labels = [_label_for(stmt, i, item) for i, item in enumerate(stmt.columns)]
    if rewrite:
        exprs = [_apply_aggregate_rewrite(item, rewrite) for item in stmt.columns]
    else:
        exprs = list(stmt.columns)
    return ProjectPlan(labels, node, exprs=exprs, labels=labels)


def _plain_column_names(stmt: Select) -> Optional[List[str]]:
    """若投影全部是无别名的普通列，返回列名列表（走兼容的按名投影路径）。"""
    if stmt.has_aggregate:
        return None
    names: List[str] = []
    for i, item in enumerate(stmt.columns):
        if not isinstance(item, ColumnRef):
            return None
        if i < len(stmt.aliases) and stmt.aliases[i]:
            return None
        names.append(item.resolved or str(item))
    return names


def _label_for(stmt: Select, index: int, expr: Expr) -> str:
    alias = stmt.aliases[index] if index < len(stmt.aliases) else None
    if alias:
        return alias
    return _key_label(expr)
