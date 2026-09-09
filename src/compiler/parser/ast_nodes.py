"""AST 节点定义（P0 范围）。

P0 只支持：四类语句 + 单条件 WHERE（比较运算）。
AND / OR / NOT、算术表达式、嵌套括号属 P1，本阶段不存在对应节点。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Union

from ..treefmt import TreeNode, render_tree

# 支持的列类型
DATA_TYPES = ("INT", "FLOAT", "VARCHAR", "TEXT")
# VARCHAR 默认长度
DEFAULT_VARCHAR_LENGTH = 32


# ----------------------------------------------------------------------
# 表达式节点
# ----------------------------------------------------------------------
@dataclass
class Literal:
    """字面量：整数、浮点、字符串、NULL。"""

    value: Any
    value_type: str  # INT / FLOAT / STRING / NULL
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        if self.value_type == "NULL":
            return "NULL"
        if self.value_type == "STRING":
            return f"'{self.value}'"
        return str(self.value)


@dataclass
class ColumnRef:
    """列引用。"""

    name: str
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        return self.name


@dataclass
class BinaryOp:
    """二元比较运算（P0 仅用于 WHERE 条件）。"""

    op: str  # = <> != < <= > >=
    left: Union[Literal, ColumnRef]
    right: Union[Literal, ColumnRef]
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        return f"({self.left} {self.op} {self.right})"


Expr = Union[Literal, ColumnRef, BinaryOp]


# ----------------------------------------------------------------------
# 语句节点
# ----------------------------------------------------------------------
@dataclass
class ColumnDef:
    """列定义。"""

    name: str
    type: str  # INT / FLOAT / VARCHAR / TEXT
    length: Optional[int] = None
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        return f"{self.name} {self.type}" + (f"({self.length})" if self.length else "")


class Statement:
    """语句基类。"""

    def to_tree(self) -> str:
        return render_tree(self._tree())

    def _tree(self) -> TreeNode:
        raise NotImplementedError


@dataclass
class CreateTable(Statement):
    table_name: str
    columns: List[ColumnDef]
    line: int = 0
    col: int = 0

    def _tree(self) -> TreeNode:
        root = TreeNode(f"CreateTable  table={self.table_name}")
        cols = TreeNode("columns")
        for c in self.columns:
            cols.add(TreeNode(str(c)))
        root.add(cols)
        return root


@dataclass
class Insert(Statement):
    table_name: str
    columns: Optional[List[str]]  # None 表示按表定义全列顺序
    rows: List[List[Literal]]
    line: int = 0
    col: int = 0
    resolved_columns: Optional[List[str]] = None  # 语义分析注解：解析后的目标列顺序

    def _tree(self) -> TreeNode:
        root = TreeNode(f"Insert  table={self.table_name}")
        root.add(TreeNode("columns=" + ("ALL" if self.columns is None else str(self.columns))))
        rows = TreeNode(f"rows({len(self.rows)})")
        for row in self.rows:
            rows.add(TreeNode("(" + ", ".join(str(v) for v in row) + ")"))
        root.add(rows)
        return root


@dataclass
class Select(Statement):
    table_name: str
    star: bool
    columns: List[ColumnRef]
    where: Optional[BinaryOp] = None
    line: int = 0
    col: int = 0
    resolved_types: Optional[tuple] = None  # 语义分析注解：WHERE 两侧类型

    def _tree(self) -> TreeNode:
        root = TreeNode(f"Select  table={self.table_name}")
        root.add(TreeNode("columns=" + ("*" if self.star else str([c.name for c in self.columns]))))
        if self.where is not None:
            root.add(TreeNode(f"where={self.where}"))
        return root


@dataclass
class Delete(Statement):
    table_name: str
    where: Optional[BinaryOp] = None
    line: int = 0
    col: int = 0
    resolved_types: Optional[tuple] = None  # 语义分析注解：WHERE 两侧类型

    def _tree(self) -> TreeNode:
        root = TreeNode(f"Delete  table={self.table_name}")
        root.add(TreeNode("where=" + ("ALL" if self.where is None else str(self.where))))
        return root
