"""谓词求值（eval_expr）：在行字典上计算表达式树。

P0 仅支持单条件 WHERE（列 比较符 值），表达式节点为 compiler/parser 的
Literal / ColumnRef / BinaryOp。本模块被 executor 的 Filter 算子与
storage_engine 的 delete_rows 共用，保证谓词语义一致。
"""

from __future__ import annotations

from typing import Any

from src.compiler.parser.ast_nodes import BinaryOp, ColumnRef, Literal

from .errors import ExecutionError

_COMPARE_OPS = {"=", "<>", "!=", "<", "<=", ">", ">="}


def resolve_column(row: dict, name: str) -> str:
    """在行字典中按列名（大小写不敏感）查找，返回实际键名。"""
    if name in row:
        return name
    lowered = name.lower()
    for key in row:
        if key.lower() == lowered:
            return key
    raise ExecutionError(f"行中不存在列 '{name}'")


def eval_expr(row: dict, expr: Any) -> Any:
    """在行字典 row 上递归求值表达式，返回 Python 值或布尔值。"""
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, ColumnRef):
        return row[resolve_column(row, expr.name)]
    if isinstance(expr, BinaryOp):
        return _apply_binary(expr.op, eval_expr(row, expr.left), eval_expr(row, expr.right))
    raise ExecutionError(f"不支持的表达式节点：{type(expr).__name__}")


def _apply_binary(op: str, left: Any, right: Any) -> bool:
    """应用比较运算，处理 NULL 与数值类型提升（INT + FLOAT -> FLOAT）。"""
    if op not in _COMPARE_OPS:
        raise ExecutionError(f"不支持的比较运算符：{op}")
    # NULL 参与的任何比较，按 SQL 三值逻辑视为不匹配（False）
    if left is None or right is None:
        return False
    # 数值比较：任一为 float 则提升为 float
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if isinstance(left, float) or isinstance(right, float):
            left, right = float(left), float(right)
        return _compare(op, left, right)
    # 字符串比较
    if isinstance(left, str) and isinstance(right, str):
        return _compare(op, left, right)
    # 其余类型组合（语义检查应已拦截，此处防御性报错）
    raise ExecutionError(f"无法比较 {type(left).__name__} 与 {type(right).__name__}")


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "=":
        return left == right
    if op in ("<>", "!="):
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    raise ExecutionError(f"不支持的比较运算符：{op}")
