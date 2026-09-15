"""语义分析器：存在性 / 类型一致性 / 列数列序检查，并维护 Catalog。

错误格式：[错误类型，位置，原因说明]

作用域模型（Scope）：
    单表查询只有一个来源；多表查询（JOIN / 逗号连接）每个来源有独立的
    名字（表名或别名）。列引用分两种写法：
      - 限定列 `a.id` —— 先定位来源，再在该表内找列；
      - 非限定列 `id` —— 在所有来源中查找，若命中多个则报"列名有歧义"。
    解析结果写入 ColumnRef.resolved（行字典中的实际键名），供 planner 使用。

聚合校验：
    - 聚合函数只允许出现在投影列表、HAVING、ORDER BY 中，禁止出现在
      WHERE / GROUP BY / JOIN ON 里；
    - 一旦使用聚合或 GROUP BY，投影中的非聚合列必须全部出现在 GROUP BY 中。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..errors import SemanticError
from ..parser.ast_nodes import (AGG_FUNCTIONS, ARITHMETIC_OPS, COMPARISON_OPS,
                                LOGICAL_OPS, BinaryOp, ColumnRef, CreateTable,
                                Delete, Explain, Expr, FunctionCall, Insert,
                                Join, Literal, Select, Statement, UnaryOp)
from .catalog import Catalog, Column, TableSchema

NUMERIC_TYPES = ("INT", "FLOAT")
# 逻辑表达式（比较 / AND / OR / NOT）的推导结果类型
BOOLEAN = "BOOLEAN"

# 列类型 -> 值的类型类别
_COLUMN_VALUE_TYPE = {
    "INT": "INT",
    "FLOAT": "FLOAT",
    "VARCHAR": "STRING",
    "TEXT": "STRING",
}


# ----------------------------------------------------------------------
# 作用域
# ----------------------------------------------------------------------
class Scope:
    """本次查询可用的表来源集合（来源名 -> 表结构）。"""

    def __init__(self) -> None:
        self.sources: List[Tuple[str, TableSchema]] = []

    def add(self, source: str, schema: TableSchema, stmt_line: int,
            stmt_col: int) -> None:
        lowered = source.lower()
        for name, _ in self.sources:
            if name.lower() == lowered:
                raise SemanticError(
                    f"表名/别名 '{source}' 重复（多表查询中每个来源名必须唯一）",
                    stmt_line, stmt_col)
        self.sources.append((source, schema))

    @property
    def names(self) -> List[str]:
        return [name for name, _ in self.sources]

    def __len__(self) -> int:
        return len(self.sources)

    def get_schema(self, source: str) -> Optional[TableSchema]:
        lowered = source.lower()
        for name, schema in self.sources:
            if name.lower() == lowered:
                return schema
        return None

    def resolve(self, ref: ColumnRef) -> Tuple[Column, str]:
        """解析列引用，返回（列元数据，行字典中的键名）。"""
        if ref.table:
            schema = self.get_schema(ref.table)
            if schema is None:
                raise SemanticError(
                    f"表名/别名 '{ref.table}' 不存在（本次查询涉及："
                    f"{', '.join(self.names)}）", ref.line, ref.col)
            col = schema.get_column(ref.name)
            if col is None:
                raise SemanticError(
                    f"表 '{ref.table}' 中不存在列 '{ref.name}'", ref.line, ref.col)
            return col, _qualified_key(ref.table, col.name)

        matches: List[Tuple[str, Column]] = []
        for source, schema in self.sources:
            col = schema.get_column(ref.name)
            if col is not None:
                matches.append((source, col))

        if not matches:
            if len(self.sources) == 1:
                raise SemanticError(
                    f"表 '{self.sources[0][0]}' 中不存在列 '{ref.name}'",
                    ref.line, ref.col)
            raise SemanticError(
                f"涉及的表中不存在列 '{ref.name}'"
                f"（已检查：{', '.join(self.names)}）", ref.line, ref.col)

        if len(matches) > 1:
            candidates = ", ".join(_qualified_key(src, col.name)
                                   for src, col in matches)
            raise SemanticError(
                f"列名 '{ref.name}' 存在歧义（候选：{candidates}），"
                f"请使用 表名.列名 限定", ref.line, ref.col)

        source, col = matches[0]
        key = col.name if len(self.sources) == 1 else _qualified_key(source, col.name)
        return col, key


def _qualified_key(source: str, column: str) -> str:
    return f"{source}.{column}"


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def analyze(statements: List[Statement], catalog: Catalog) -> List[str]:
    """逐条语句做语义检查，返回每条语句的结论文本；出错抛 SemanticError。"""
    return [_analyze_one(stmt, catalog) for stmt in statements]


def _analyze_one(stmt: Statement, catalog: Catalog) -> str:
    if isinstance(stmt, Explain):
        return _analyze_explain(stmt, catalog)
    if isinstance(stmt, CreateTable):
        return _analyze_create_table(stmt, catalog)
    if isinstance(stmt, Insert):
        return _analyze_insert(stmt, catalog)
    if isinstance(stmt, Select):
        return _analyze_select(stmt, catalog)
    if isinstance(stmt, Delete):
        return _analyze_delete(stmt, catalog)
    raise SemanticError(f"不支持的语句类型：{type(stmt).__name__}", stmt.line, stmt.col)


def _analyze_explain(stmt: Explain, catalog: Catalog) -> str:
    """EXPLAIN 只编译不执行：用 Catalog 副本干跑，避免产生任何副作用。"""
    dry_catalog = Catalog.from_dict(catalog.to_dict())
    message = _analyze_one(stmt.statement, dry_catalog)
    return f"EXPLAIN：仅编译并输出执行计划（不执行）。{message}"


# ----------------------------------------------------------------------
# CREATE TABLE
# ----------------------------------------------------------------------
def _analyze_create_table(stmt: CreateTable, catalog: Catalog) -> str:
    if catalog.has_table(stmt.table_name):
        raise SemanticError(f"表 '{stmt.table_name}' 已存在", stmt.line, stmt.col)

    seen = set()
    for col in stmt.columns:
        key = col.name.lower()
        if key in seen:
            raise SemanticError(f"列名重复：'{col.name}'", col.line, col.col)
        seen.add(key)
        if col.type == "VARCHAR" and (col.length is None or col.length <= 0):
            raise SemanticError(f"VARCHAR 长度必须为正整数：'{col.name}'", col.line, col.col)

    catalog.create_table(
        TableSchema(
            name=stmt.table_name,
            columns=[Column(c.name, c.type, c.length) for c in stmt.columns],
        )
    )
    return f"语义检查通过：表 '{stmt.table_name}' 创建成功（{len(stmt.columns)} 列）"


# ----------------------------------------------------------------------
# INSERT
# ----------------------------------------------------------------------
def _analyze_insert(stmt: Insert, catalog: Catalog) -> str:
    schema = catalog.get_table(stmt.table_name)
    if schema is None:
        raise SemanticError(f"表 '{stmt.table_name}' 不存在", stmt.line, stmt.col)

    if stmt.columns is None:
        target_columns = list(schema.columns)
    else:
        seen = set()
        target_columns = []
        for name in stmt.columns:
            col = schema.get_column(name)
            if col is None:
                # INSERT 列名未保存位置，退化为语句起始位置
                raise SemanticError(f"表 '{stmt.table_name}' 中不存在列 '{name}'",
                                    stmt.line, stmt.col)
            key = col.name.lower()
            if key in seen:
                raise SemanticError(f"列名重复：'{name}'", stmt.line, stmt.col)
            seen.add(key)
            target_columns.append(col)

    expected = len(target_columns)
    for row in stmt.rows:
        if len(row) != expected:
            raise SemanticError(
                f"列数不一致：期望 {expected} 个值，实际 {len(row)} 个",
                row[0].line if row else stmt.line,
                row[0].col if row else stmt.col,
            )
        for value, col in zip(row, target_columns):
            if not _assignable(col.type, value.value_type):
                raise SemanticError(
                    f"类型不匹配：列 '{col.name}' 为 {col.type}，"
                    f"无法接受 {_display_type(value.value_type)} 值 {value}",
                    value.line, value.col,
                )

    # 注解：解析后的目标列顺序，供 planner 使用
    stmt.resolved_columns = [c.name for c in target_columns]
    return f"语义检查通过：向表 '{stmt.table_name}' 插入 {len(stmt.rows)} 行"


# ----------------------------------------------------------------------
# SELECT
# ----------------------------------------------------------------------
def _analyze_select(stmt: Select, catalog: Catalog) -> str:
    scope = _build_scope(stmt, catalog)

    # 投影列表：解析每个表达式（允许聚合）
    if not stmt.star:
        for item in stmt.columns:
            _infer_type(item, scope, allow_aggregate=True)

    # WHERE：不允许聚合
    if stmt.where is not None:
        where_type = _infer_type(stmt.where, scope, allow_aggregate=False,
                                 clause="WHERE")
        _require_boolean(where_type, stmt.where, "WHERE 条件")
        stmt.resolved_types = (where_type,)

    # GROUP BY：解析分组键（不允许聚合），别名可引用投影别名
    for group in stmt.group_by:
        resolved = _resolve_alias(group, stmt)
        _infer_type(resolved, scope, allow_aggregate=False, clause="GROUP BY")

    # HAVING：必须为逻辑表达式，允许聚合
    if stmt.having is not None:
        having_type = _infer_type(_resolve_alias(stmt.having, stmt), scope,
                                  allow_aggregate=True)
        _require_boolean(having_type, stmt.having, "HAVING 条件")

    # ORDER BY：允许聚合与投影别名
    for order in stmt.order_by:
        _infer_type(_resolve_alias(order.expr, stmt), scope, allow_aggregate=True)

    # 分组一致性
    if stmt.has_aggregate:
        _check_grouping(stmt, scope)

    if not stmt.star:
        for ref in stmt.columns:
            if isinstance(ref, ColumnRef):
                pass
    return _select_message(stmt)


def _select_message(stmt: Select) -> str:
    if stmt.is_multi_table:
        tables = ", ".join(stmt.sources)
        base = f"语义检查通过：多表查询（{tables}）"
    else:
        base = f"语义检查通过：查询表 '{stmt.table_name}'"
    if stmt.has_aggregate:
        base += "，含聚合"
    if stmt.order_by:
        base += "，含排序"
    return base


def _build_scope(stmt: Select, catalog: Catalog) -> Scope:
    """构造多表作用域，并逐个校验 JOIN 的 ON 条件。"""
    scope = Scope()

    main_schema = catalog.get_table(stmt.table_name)
    if main_schema is None:
        raise SemanticError(f"表 '{stmt.table_name}' 不存在", stmt.line, stmt.col)
    scope.add(stmt.from_alias or stmt.table_name, main_schema, stmt.line, stmt.col)

    for join in stmt.joins:
        left_names = list(scope.names)
        schema = catalog.get_table(join.table.name)
        if schema is None:
            raise SemanticError(f"表 '{join.table.name}' 不存在", join.line, join.col)
        scope.add(join.table.source, schema, join.line, join.col)

        if join.on is None:
            if join.join_type != "CROSS":
                raise SemanticError(
                    f"{join.join_type} JOIN 必须提供 ON 条件", join.line, join.col)
            continue

        on_type = _infer_type(join.on, scope, allow_aggregate=False,
                              clause="JOIN ON")
        _require_boolean(on_type, join.on, "JOIN ON 条件")
        _check_on_covers_both_sides(join, left_names, join.table.source)

    return scope


def _check_on_covers_both_sides(join: Join, left_names: List[str],
                                right_name: str) -> None:
    """ON 条件应同时引用左右两侧的列，否则很可能是写错了。"""
    touched = {name.lower() for name in _referenced_sources(join.on)}
    left = {n.lower() for n in left_names}
    if not (touched & left) or right_name.lower() not in touched:
        raise SemanticError(
            "JOIN ON 条件必须同时引用连接两侧的列（如 a.id = b.aid）",
            join.on.line, join.on.col)


def _referenced_sources(expr: Optional[Expr]) -> List[str]:
    """收集表达式中出现过的限定符（表名/别名）。"""
    if expr is None:
        return []
    if isinstance(expr, ColumnRef):
        return [expr.table] if expr.table else []
    if isinstance(expr, BinaryOp):
        return _referenced_sources(expr.left) + _referenced_sources(expr.right)
    if isinstance(expr, UnaryOp):
        return _referenced_sources(expr.operand)
    if isinstance(expr, FunctionCall):
        return _referenced_sources(expr.arg)
    return []


# ----------------------------------------------------------------------
# DELETE
# ----------------------------------------------------------------------
def _analyze_delete(stmt: Delete, catalog: Catalog) -> str:
    schema = catalog.get_table(stmt.table_name)
    if schema is None:
        raise SemanticError(f"表 '{stmt.table_name}' 不存在", stmt.line, stmt.col)

    scope = Scope()
    scope.add(stmt.table_name, schema, stmt.line, stmt.col)

    if stmt.where is not None:
        where_type = _infer_type(stmt.where, scope, allow_aggregate=False,
                                 clause="WHERE")
        _require_boolean(where_type, stmt.where, "WHERE 条件")
        stmt.resolved_types = (where_type,)
    return f"语义检查通过：删除表 '{stmt.table_name}' 的记录"


# ----------------------------------------------------------------------
# 表达式类型推导
# ----------------------------------------------------------------------
def _infer_type(expr: Expr, scope: Scope, allow_aggregate: bool = True,
                clause: str = "") -> Optional[str]:
    """递归推导表达式类型；列不存在或类型非法时抛 SemanticError。

    推导结果取值：INT / FLOAT / STRING / NULL / BOOLEAN。
    """
    if isinstance(expr, Literal):
        return expr.value_type

    if isinstance(expr, ColumnRef):
        col, key = scope.resolve(expr)
        expr.resolved = key  # 注解：行字典中的实际键名
        return _COLUMN_VALUE_TYPE.get(col.type)

    if isinstance(expr, FunctionCall):
        return _infer_aggregate(expr, scope, allow_aggregate, clause)

    if isinstance(expr, UnaryOp):
        operand_type = _infer_type(expr.operand, scope, allow_aggregate, clause)
        if expr.op.upper() == "NOT":
            if operand_type != BOOLEAN:
                raise SemanticError(
                    "类型不匹配：NOT 需要逻辑表达式，实际为 "
                    f"{_display_type(operand_type)}", expr.line, expr.col)
            return BOOLEAN
        if operand_type not in NUMERIC_TYPES:
            raise SemanticError(
                f"类型不匹配：一元 '{expr.op}' 需要数值，实际为 "
                f"{_display_type(operand_type)}", expr.line, expr.col)
        return operand_type

    if isinstance(expr, BinaryOp):
        op = expr.op.upper()
        left_type = _infer_type(expr.left, scope, allow_aggregate, clause)
        right_type = _infer_type(expr.right, scope, allow_aggregate, clause)

        if op in LOGICAL_OPS:
            for side_type in (left_type, right_type):
                if side_type != BOOLEAN:
                    raise SemanticError(
                        f"类型不匹配：'{op}' 需要逻辑表达式，实际为 "
                        f"{_display_type(side_type)}", expr.line, expr.col)
            return BOOLEAN

        if op in ARITHMETIC_OPS:
            for side_type in (left_type, right_type):
                if side_type not in NUMERIC_TYPES:
                    raise SemanticError(
                        f"类型不匹配：算术运算 '{op}' 需要数值，实际为 "
                        f"{_display_type(side_type)}", expr.line, expr.col)
            return "FLOAT" if "FLOAT" in (left_type, right_type) else "INT"

        if op in COMPARISON_OPS:
            if not _comparable(left_type, right_type):
                raise SemanticError(
                    f"类型不匹配：无法比较 {_display_type(left_type)} 与 "
                    f"{_display_type(right_type)}", expr.line, expr.col)
            return BOOLEAN

        raise SemanticError(f"未知的运算符：'{expr.op}'", expr.line, expr.col)

    raise SemanticError(f"无法解析的表达式节点：{type(expr).__name__}",
                        getattr(expr, "line", 0), getattr(expr, "col", 0))


def _infer_aggregate(expr: FunctionCall, scope: Scope, allow_aggregate: bool,
                     clause: str) -> str:
    """聚合函数类型推导与合法性检查。"""
    func = expr.name.upper()
    if func not in AGG_FUNCTIONS:
        raise SemanticError(
            f"不支持的函数 '{expr.name}'（仅支持聚合函数 "
            f"{' / '.join(AGG_FUNCTIONS)}）", expr.line, expr.col)

    if not allow_aggregate:
        raise SemanticError(
            f"聚合函数 {func} 不能出现在 {clause or '该位置'} 中"
            f"（只能用于投影列表 / HAVING / ORDER BY）", expr.line, expr.col)

    if expr.star:
        if func != "COUNT":
            raise SemanticError(f"{func}(*) 不合法：只有 COUNT 支持 '*' 参数",
                                expr.line, expr.col)
        return "INT"

    if expr.arg is None:
        raise SemanticError(f"{func} 缺少参数", expr.line, expr.col)

    arg_type = _infer_type(expr.arg, scope, allow_aggregate=False,
                           clause="聚合函数参数")
    if func == "COUNT":
        return "INT"
    if func in ("SUM", "AVG"):
        if arg_type not in NUMERIC_TYPES:
            raise SemanticError(
                f"聚合函数 {func} 需要数值参数，实际为 {_display_type(arg_type)}",
                expr.line, expr.col)
        return "FLOAT" if func == "AVG" else arg_type
    # MIN / MAX：参数类型即结果类型
    return arg_type


# ----------------------------------------------------------------------
# 分组一致性
# ----------------------------------------------------------------------
def _check_grouping(stmt: Select, scope: Scope) -> None:
    """使用聚合时，投影中的裸列必须出现在 GROUP BY 中。"""
    group_keys = {_expr_key(g) for g in stmt.group_by}
    group_names = {g.name.lower() for g in stmt.group_by
                   if isinstance(g, ColumnRef)}
    alias_names = {a.lower() for a in stmt.aliases if a}

    def check(expr: Optional[Expr], where: str) -> None:
        for ref in _bare_columns(expr):
            if _expr_key(ref) in group_keys or ref.name.lower() in group_names:
                continue
            if ref.name.lower() in alias_names:
                continue
            raise SemanticError(
                f"列 '{ref}' 必须出现在 GROUP BY 中或用于聚合函数（{where}）",
                ref.line, ref.col)

    if not stmt.star:
        for item in stmt.columns:
            check(item, "投影列表")
    check(stmt.having, "HAVING")
    for order in stmt.order_by:
        check(order.expr, "ORDER BY")


def _bare_columns(expr: Optional[Expr]) -> List[ColumnRef]:
    """收集表达式中的列引用，但**不进入**聚合函数内部。"""
    if expr is None:
        return []
    if isinstance(expr, ColumnRef):
        return [expr]
    if isinstance(expr, FunctionCall):
        return []
    if isinstance(expr, BinaryOp):
        return _bare_columns(expr.left) + _bare_columns(expr.right)
    if isinstance(expr, UnaryOp):
        return _bare_columns(expr.operand)
    return []


def _expr_key(expr: Expr) -> str:
    return str(expr).lower()


def _resolve_alias(expr: Expr, stmt: Select) -> Expr:
    """投影别名可以出现在 GROUP BY / HAVING / ORDER BY 中，这里替换回原表达式。"""
    if not isinstance(expr, ColumnRef) or expr.table:
        return expr
    lowered = expr.name.lower()
    for item, alias in zip(stmt.columns, stmt.aliases):
        if alias and alias.lower() == lowered:
            return item
    return expr


# ----------------------------------------------------------------------
# 类型规则
# ----------------------------------------------------------------------
def _require_boolean(value_type: Optional[str], expr: Expr, what: str) -> None:
    if value_type != BOOLEAN:
        raise SemanticError(
            f"{what}必须是逻辑表达式（比较 / AND / OR / NOT / 聚合比较），"
            f"实际为 {_display_type(value_type)}", expr.line, expr.col)


def _assignable(column_type: str, value_type: str) -> bool:
    """列类型能否接受该常量类型。"""
    if value_type == "NULL":
        return True
    if column_type in ("VARCHAR", "TEXT"):
        return value_type == "STRING"
    if column_type == "INT":
        return value_type == "INT"
    if column_type == "FLOAT":
        return value_type in NUMERIC_TYPES
    return False


def _comparable(left_type: Optional[str], right_type: Optional[str]) -> bool:
    """两个操作数能否比较。"""
    if left_type is None or right_type is None:
        return False
    if "NULL" in (left_type, right_type):
        return True
    if left_type in NUMERIC_TYPES and right_type in NUMERIC_TYPES:
        return True
    return left_type == right_type and left_type in ("STRING", BOOLEAN)


def _display_type(value_type: Optional[str]) -> str:
    return {
        "INT": "INT", "FLOAT": "FLOAT", "STRING": "STRING",
        "NULL": "NULL", "BOOLEAN": "BOOLEAN",
    }.get(value_type, str(value_type))
