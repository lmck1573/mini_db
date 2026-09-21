"""语义分析器：存在性 / 类型一致性 / 列数列序检查，并维护 Catalog。

错误格式：[错误类型，位置，原因说明]
"""

from __future__ import annotations

from typing import List, Optional

from ..errors import SemanticError
from ..parser.ast_nodes import (BinaryOp, ColumnRef, CreateTable, Delete,
                                Insert, Literal, Select, Statement)
from .catalog import Catalog, Column, TableSchema

NUMERIC_TYPES = ("INT", "FLOAT")

# 列类型 -> 值的类型类别
_COLUMN_VALUE_TYPE = {
    "INT": "INT",
    "FLOAT": "FLOAT",
    "VARCHAR": "STRING",
    "TEXT": "STRING",
}


def analyze(statements: List[Statement], catalog: Catalog) -> List[str]:
    """逐条语句做语义检查，返回每条语句的结论文本；出错抛 SemanticError。"""
    return [_analyze_one(stmt, catalog) for stmt in statements]


def _analyze_one(stmt: Statement, catalog: Catalog) -> str:
    if isinstance(stmt, CreateTable):
        return _analyze_create_table(stmt, catalog)
    if isinstance(stmt, Insert):
        return _analyze_insert(stmt, catalog)
    if isinstance(stmt, Select):
        return _analyze_select(stmt, catalog)
    if isinstance(stmt, Delete):
        return _analyze_delete(stmt, catalog)
    raise SemanticError(f"不支持的语句类型：{type(stmt).__name__}", stmt.line, stmt.col)


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
# SELECT / DELETE
# ----------------------------------------------------------------------
def _analyze_select(stmt: Select, catalog: Catalog) -> str:
    schema = _require_table(stmt.table_name, stmt, catalog)

    if not stmt.star:
        for ref in stmt.columns:
            if schema.get_column(ref.name) is None:
                raise SemanticError(f"表 '{stmt.table_name}' 中不存在列 '{ref.name}'",
                                    ref.line, ref.col)

    if stmt.where is not None:
        _check_condition(stmt.where, schema, stmt.table_name)
        stmt.resolved_types = _condition_types(stmt.where, schema)

    return f"语义检查通过：查询表 '{stmt.table_name}'"


def _analyze_delete(stmt: Delete, catalog: Catalog) -> str:
    schema = _require_table(stmt.table_name, stmt, catalog)
    if stmt.where is not None:
        _check_condition(stmt.where, schema, stmt.table_name)
        stmt.resolved_types = _condition_types(stmt.where, schema)
    return f"语义检查通过：删除表 '{stmt.table_name}' 的记录"


def _require_table(table_name: str, stmt: Statement, catalog: Catalog) -> TableSchema:
    schema = catalog.get_table(table_name)
    if schema is None:
        raise SemanticError(f"表 '{table_name}' 不存在", stmt.line, stmt.col)
    return schema


def _check_condition(cond: BinaryOp, schema: TableSchema, table_name: str) -> None:
    """检查 WHERE 单条件中的列存在性与类型兼容性。"""
    for operand in (cond.left, cond.right):
        if isinstance(operand, ColumnRef) and schema.get_column(operand.name) is None:
            raise SemanticError(f"表 '{table_name}' 中不存在列 '{operand.name}'",
                                operand.line, operand.col)

    left_type = _operand_type(cond.left, schema)
    right_type = _operand_type(cond.right, schema)
    if left_type is None or right_type is None:
        raise SemanticError("无法解析比较运算的操作数类型", cond.line, cond.col)
    if not _comparable(left_type, right_type):
        raise SemanticError(
            f"类型不匹配：无法比较 {_display_type(left_type)} 与 {_display_type(right_type)}",
            cond.line, cond.col,
        )


def _condition_types(cond: BinaryOp, schema: TableSchema) -> tuple:
    return (_operand_type(cond.left, schema), _operand_type(cond.right, schema))


def _operand_type(operand, schema: TableSchema) -> Optional[str]:
    if isinstance(operand, Literal):
        return operand.value_type
    if isinstance(operand, ColumnRef):
        col = schema.get_column(operand.name)
        return _COLUMN_VALUE_TYPE.get(col.type) if col else None
    return None


# ----------------------------------------------------------------------
# 类型规则
# ----------------------------------------------------------------------
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


def _comparable(left_type: str, right_type: str) -> bool:
    """两个操作数能否比较。"""
    if "NULL" in (left_type, right_type):
        return True
    if left_type in NUMERIC_TYPES and right_type in NUMERIC_TYPES:
        return True
    return left_type == "STRING" and right_type == "STRING"


def _display_type(value_type: str) -> str:
    return {"INT": "INT", "FLOAT": "FLOAT", "STRING": "STRING", "NULL": "NULL"}.get(
        value_type, value_type)
