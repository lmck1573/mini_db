"""AST 节点定义（P0 基础语句 + P1 表达式 + P2 多表/聚合/排序/EXPLAIN 扩展）。

语句层：CREATE TABLE / INSERT / SELECT / DELETE（P0），EXPLAIN（扩展）。
表达式层：支持逻辑（AND/OR/NOT）、比较、算术（+ - * /）、聚合函数调用
         （COUNT/SUM/AVG/MIN/MAX）与嵌套括号，由 BinaryOp / UnaryOp /
         FunctionCall / Literal / ColumnRef 递归组成。
SELECT 扩展：多表连接（JOIN ... ON）、GROUP BY / HAVING、ORDER BY。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

from ..treefmt import TreeNode, render_tree

# 支持的列类型
DATA_TYPES = ("INT", "FLOAT", "VARCHAR", "TEXT")
# VARCHAR 默认长度
DEFAULT_VARCHAR_LENGTH = 32

# 表达式运算符分类（比较运算曾定义在 parser.py，统一收敛到这里）
COMPARISON_OPS = ("=", "<>", "!=", "<", "<=", ">", ">=")
LOGICAL_OPS = ("AND", "OR")
ARITHMETIC_OPS = ("+", "-", "*", "/")

# 支持的聚合函数
AGG_FUNCTIONS = ("COUNT", "SUM", "AVG", "MIN", "MAX")
# 支持的连接类型
JOIN_TYPES = ("INNER", "LEFT", "CROSS")


# ----------------------------------------------------------------------
# 表达式节点
# ----------------------------------------------------------------------
@dataclass
class Literal:
    """字面量：整数、浮点、字符串、NULL、布尔。"""

    value: Any
    value_type: str  # INT / FLOAT / STRING / NULL / BOOLEAN
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        if self.value_type == "NULL":
            return "NULL"
        if self.value_type == "BOOLEAN":
            return "TRUE" if self.value else "FALSE"
        if self.value_type == "STRING":
            return f"'{self.value}'"
        return str(self.value)


@dataclass
class ColumnRef:
    """列引用，可带表名/别名限定（`a.id`）。"""

    name: str
    line: int = 0
    col: int = 0
    table: Optional[str] = None  # 限定符：表名或表别名（未限定时为 None）
    resolved: Optional[str] = None  # 语义分析注解：行字典中的实际键名

    def __str__(self) -> str:
        return f"{self.table}.{self.name}" if self.table else self.name


@dataclass
class BinaryOp:
    """二元运算：比较（= <> != < <= > >=）、逻辑（AND / OR）、算术（+ - * /）。"""

    op: str
    left: "Expr"
    right: "Expr"
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        return f"({self.left} {self.op} {self.right})"


@dataclass
class UnaryOp:
    """一元运算：NOT 取反、+/- 取正负。"""

    op: str  # NOT / - / +
    operand: "Expr"
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        if self.op.upper() == "NOT":
            return f"(NOT {self.operand})"
        return f"({self.op}{self.operand})"


@dataclass
class FunctionCall:
    """聚合函数调用：COUNT(*) / SUM(age) / AVG(score) / MIN(x) / MAX(x)。"""

    name: str  # COUNT / SUM / AVG / MIN / MAX
    arg: Optional["Expr"] = None
    star: bool = False  # COUNT(*) 为 True
    line: int = 0
    col: int = 0

    def label(self) -> str:
        """输出列名，如 COUNT(*) / SUM(age)。"""
        inner = "*" if self.star else str(self.arg)
        return f"{self.name.upper()}({inner})"

    def __str__(self) -> str:
        return self.label()


Expr = Union[Literal, ColumnRef, BinaryOp, UnaryOp, FunctionCall]

# 可参与 GROUP BY / ORDER BY / 投影的表达式
Projectable = Union[ColumnRef, FunctionCall, Literal, BinaryOp, UnaryOp]


# ----------------------------------------------------------------------
# SELECT 辅助结构
# ----------------------------------------------------------------------
@dataclass
class TableRef:
    """FROM / JOIN 后的表引用，支持别名。"""

    name: str
    alias: Optional[str] = None
    line: int = 0
    col: int = 0

    @property
    def source(self) -> str:
        """在计划与行字典中使用的来源名：优先别名，其次表名。"""
        return self.alias or self.name

    def __str__(self) -> str:
        return f"{self.name} AS {self.alias}" if self.alias else self.name


@dataclass
class Join:
    """一次连接：JOIN <table> ON <expr>。"""

    table: TableRef
    join_type: str = "INNER"  # INNER / LEFT / CROSS
    on: Optional[Expr] = None
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        text = f"{self.join_type} JOIN {self.table}"
        return f"{text} ON {self.on}" if self.on is not None else text


@dataclass
class OrderItem:
    """ORDER BY 的一项：表达式 + 升降序。"""

    expr: Expr
    desc: bool = False
    line: int = 0
    col: int = 0

    def __str__(self) -> str:
        return f"{self.expr} {'DESC' if self.desc else 'ASC'}"

    @property
    def label(self) -> str:
        return f"{self.expr}{' DESC' if self.desc else ''}"


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
    """SELECT 语句。

    单表查询时 joins 为空；多表查询时 from_table 为主表，
    joins 依次描述后续连接。group_by / having / order_by 为空表示未使用。
    """

    table_name: str
    star: bool
    columns: List[Expr]  # ColumnRef / FunctionCall / 其他表达式
    where: Optional[Expr] = None
    line: int = 0
    col: int = 0
    resolved_types: Optional[tuple] = None  # 语义分析注解：WHERE 类型
    from_alias: Optional[str] = None        # 主表别名
    joins: List[Join] = field(default_factory=list)
    group_by: List[Expr] = field(default_factory=list)
    having: Optional[Expr] = None
    order_by: List[OrderItem] = field(default_factory=list)
    aliases: List[Optional[str]] = field(default_factory=list)  # 与 columns 平行的输出别名

    @property
    def sources(self) -> List[str]:
        """本次查询涉及的所有来源名（主表 + 各 JOIN 表）。"""
        names = [self.from_alias or self.table_name]
        names.extend(j.table.source for j in self.joins)
        return names

    @property
    def is_multi_table(self) -> bool:
        return bool(self.joins)

    @property
    def has_aggregate(self) -> bool:
        return bool(self.group_by) or any(_contains_aggregate(c) for c in self.columns) \
            or _contains_aggregate(self.having) or any(
                _contains_aggregate(o.expr) for o in self.order_by)

    def _tree(self) -> TreeNode:
        head = f"Select  table={self.table_name}"
        if self.from_alias:
            head += f" AS {self.from_alias}"
        root = TreeNode(head)
        if self.joins:
            joins = TreeNode("joins")
            for j in self.joins:
                joins.add(TreeNode(str(j)))
            root.add(joins)
        root.add(TreeNode("columns=" + ("*" if self.star
                                        else str([str(c) for c in self.columns]))))
        if self.where is not None:
            root.add(TreeNode(f"where={self.where}"))
        if self.group_by:
            root.add(TreeNode("group_by=" + str([str(g) for g in self.group_by])))
        if self.having is not None:
            root.add(TreeNode(f"having={self.having}"))
        if self.order_by:
            root.add(TreeNode("order_by=" + str([o.label for o in self.order_by])))
        return root


@dataclass
class Delete(Statement):
    table_name: str
    where: Optional[Expr] = None
    line: int = 0
    col: int = 0
    resolved_types: Optional[tuple] = None  # 语义分析注解：WHERE 类型

    def _tree(self) -> TreeNode:
        root = TreeNode(f"Delete  table={self.table_name}")
        root.add(TreeNode("where=" + ("ALL" if self.where is None else str(self.where))))
        return root


@dataclass
class Explain(Statement):
    """EXPLAIN <语句>：只编译并输出执行计划，不执行。"""

    statement: Statement
    line: int = 0
    col: int = 0

    def _tree(self) -> TreeNode:
        root = TreeNode("Explain（只输出计划，不执行）")
        root.add(self.statement._tree())
        return root


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def _contains_aggregate(expr: Optional[Expr]) -> bool:
    """递归判断表达式树中是否含聚合函数。"""
    if expr is None:
        return False
    if isinstance(expr, FunctionCall):
        return True
    if isinstance(expr, BinaryOp):
        return _contains_aggregate(expr.left) or _contains_aggregate(expr.right)
    if isinstance(expr, UnaryOp):
        return _contains_aggregate(expr.operand)
    return False


def iter_aggregates(expr: Optional[Expr]) -> List[FunctionCall]:
    """收集表达式树中出现的所有聚合函数调用（按出现顺序）。"""
    found: List[FunctionCall] = []
    _collect_aggregates(expr, found)
    return found


def _collect_aggregates(expr: Optional[Expr], out: List[FunctionCall]) -> None:
    if expr is None:
        return
    if isinstance(expr, FunctionCall):
        out.append(expr)
        _collect_aggregates(expr.arg, out)
        return
    if isinstance(expr, BinaryOp):
        _collect_aggregates(expr.left, out)
        _collect_aggregates(expr.right, out)
        return
    if isinstance(expr, UnaryOp):
        _collect_aggregates(expr.operand, out)
        return
