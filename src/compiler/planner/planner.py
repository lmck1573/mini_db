"""执行计划生成器：AST -> 逻辑执行计划（P0）。

计划形状固定（Project -> Filter -> SeqScan），不做任何优化：
不重排、不下推、不合并，也不涉及物理信息。
"""

from __future__ import annotations

from ..errors import PlannerError
from ..parser.ast_nodes import CreateTable, Delete, Insert, Select, Statement
from ..semantic.catalog import Catalog
from .plan_nodes import (CreateTablePlan, DeletePlan, FilterPlan, InsertPlan,
                         PlanNode, ProjectPlan, SeqScanPlan)


def plan(statement: Statement, catalog: Catalog) -> PlanNode:
    """把单条语句翻译为逻辑执行计划。"""
    if isinstance(statement, CreateTable):
        return _plan_create_table(statement)
    if isinstance(statement, Insert):
        return _plan_insert(statement, catalog)
    if isinstance(statement, Select):
        return _plan_select(statement)
    if isinstance(statement, Delete):
        return _plan_delete(statement)
    raise PlannerError(f"不支持的语句类型：{type(statement).__name__}",
                       statement.line, statement.col)


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


def _plan_select(stmt: Select) -> ProjectPlan:
    scan: PlanNode = SeqScanPlan(stmt.table_name)
    if stmt.where is not None:
        scan = FilterPlan(stmt.where, scan)
    columns = "*" if stmt.star else [c.name for c in stmt.columns]
    return ProjectPlan(columns, scan)


def _plan_delete(stmt: Delete) -> DeletePlan:
    return DeletePlan(stmt.table_name, stmt.where)
