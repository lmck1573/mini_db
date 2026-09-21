"""执行计划节点定义（P0）。

输出形式：树形结构 / JSON / S 表达式，三者内容一致。
计划中只保存纯数据（可 JSON 序列化），不引用 AST 对象。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from ..parser.ast_nodes import BinaryOp, ColumnRef, Literal
from ..treefmt import TreeNode, render_tree

Expr = Union[Literal, ColumnRef, BinaryOp]


# ----------------------------------------------------------------------
# 表达式序列化
# ----------------------------------------------------------------------
def expr_to_dict(expr: Optional[Expr]) -> Optional[Dict[str, Any]]:
    """表达式 -> 可 JSON 序列化的 dict。"""
    if expr is None:
        return None
    if isinstance(expr, Literal):
        return {"type": "Literal", "value": expr.value, "value_type": expr.value_type}
    if isinstance(expr, ColumnRef):
        return {"type": "ColumnRef", "name": expr.name}
    if isinstance(expr, BinaryOp):
        return {
            "type": "BinaryOp",
            "op": expr.op,
            "left": expr_to_dict(expr.left),
            "right": expr_to_dict(expr.right),
        }
    raise TypeError(f"无法序列化的表达式节点：{type(expr).__name__}")


def expr_from_dict(data: Optional[Dict[str, Any]]) -> Optional[Expr]:
    """dict -> 表达式节点。"""
    if data is None:
        return None
    kind = data["type"]
    if kind == "Literal":
        return Literal(data["value"], data["value_type"])
    if kind == "ColumnRef":
        return ColumnRef(data["name"])
    if kind == "BinaryOp":
        return BinaryOp(data["op"], expr_from_dict(data["left"]), expr_from_dict(data["right"]))
    raise ValueError(f"未知的表达式类型：{kind}")


# ----------------------------------------------------------------------
# 计划节点
# ----------------------------------------------------------------------
class PlanNode:
    """计划节点基类。"""

    op: str = ""

    def to_dict(self) -> Dict[str, Any]:
        raise NotImplementedError

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_tree(self) -> str:
        return render_tree(self._tree())

    def _tree(self) -> TreeNode:
        raise NotImplementedError

    def to_sexpr(self) -> str:
        raise NotImplementedError

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "PlanNode":
        op = data["op"]
        if op not in _PLAN_TYPES:
            raise ValueError(f"未知的算子类型：{op}")
        return _PLAN_TYPES[op]._from_dict(data)

    @classmethod
    def _from_dict(cls, data: Dict[str, Any]) -> "PlanNode":
        raise NotImplementedError


@dataclass
class CreateTablePlan(PlanNode):
    table_name: str
    columns: List[Dict[str, Any]]  # [{"name","type","length"}]

    op = "CreateTable"

    def to_dict(self):
        return {"op": self.op, "table_name": self.table_name, "columns": self.columns}

    def _tree(self):
        root = TreeNode(f"CreateTable  table={self.table_name}")
        for col in self.columns:
            length = col.get("length")
            root.add(TreeNode(col["name"] + " " + col["type"] +
                              (f"({length})" if length else "")))
        return root

    def to_sexpr(self):
        cols = " ".join(c["name"] + " " + c["type"] for c in self.columns)
        return f"(CreateTable {self.table_name} [{cols}])"

    @classmethod
    def _from_dict(cls, data):
        return cls(data["table_name"], data["columns"])


@dataclass
class InsertPlan(PlanNode):
    table_name: str
    columns: List[str]
    rows: List[List[Literal]]

    op = "Insert"

    def to_dict(self):
        return {
            "op": self.op,
            "table_name": self.table_name,
            "columns": self.columns,
            "rows": [[expr_to_dict(v) for v in row] for row in self.rows],
        }

    def _tree(self):
        root = TreeNode(f"Insert  table={self.table_name}")
        root.add(TreeNode("columns=" + str(self.columns)))
        rows = TreeNode(f"rows({len(self.rows)})")
        for row in self.rows:
            rows.add(TreeNode("(" + ", ".join(str(v) for v in row) + ")"))
        root.add(rows)
        return root

    def to_sexpr(self):
        cols = " ".join(self.columns)
        rows = " ".join("(" + " ".join(str(v) for v in row) + ")" for row in self.rows)
        return f"(Insert {self.table_name} [{cols}] [{rows}])"

    @classmethod
    def _from_dict(cls, data):
        return cls(
            data["table_name"],
            data["columns"],
            [[expr_from_dict(v) for v in row] for row in data["rows"]],
        )


@dataclass
class SeqScanPlan(PlanNode):
    table_name: str

    op = "SeqScan"

    def to_dict(self):
        return {"op": self.op, "table_name": self.table_name}

    def _tree(self):
        return TreeNode(f"SeqScan  table={self.table_name}")

    def to_sexpr(self):
        return f"(SeqScan {self.table_name})"

    @classmethod
    def _from_dict(cls, data):
        return cls(data["table_name"])


@dataclass
class FilterPlan(PlanNode):
    predicate: BinaryOp
    child: PlanNode

    op = "Filter"

    def to_dict(self):
        return {"op": self.op, "predicate": expr_to_dict(self.predicate),
                "child": self.child.to_dict()}

    def _tree(self):
        root = TreeNode(f"Filter  {self.predicate}")
        root.add(self.child._tree())
        return root

    def to_sexpr(self):
        left = expr_to_dict(self.predicate)
        return f"(Filter {_sexpr_predicate(left)} {self.child.to_sexpr()})"

    @classmethod
    def _from_dict(cls, data):
        return cls(expr_from_dict(data["predicate"]), PlanNode.from_dict(data["child"]))


@dataclass
class ProjectPlan(PlanNode):
    columns: Union[str, List[str]]  # "*" 或列名列表
    child: PlanNode

    op = "Project"

    def to_dict(self):
        return {"op": self.op, "columns": self.columns, "child": self.child.to_dict()}

    def _tree(self):
        label = "*" if self.columns == "*" else str(self.columns)
        root = TreeNode(f"Project  columns={label}")
        root.add(self.child._tree())
        return root

    def to_sexpr(self):
        cols = "*" if self.columns == "*" else " ".join(self.columns)
        return f"(Project [{cols}] {self.child.to_sexpr()})"

    @classmethod
    def _from_dict(cls, data):
        return cls(data["columns"], PlanNode.from_dict(data["child"]))


@dataclass
class DeletePlan(PlanNode):
    table_name: str
    predicate: Optional[BinaryOp]

    op = "Delete"

    def to_dict(self):
        return {"op": self.op, "table_name": self.table_name,
                "predicate": expr_to_dict(self.predicate)}

    def _tree(self):
        root = TreeNode(f"Delete  table={self.table_name}")
        root.add(TreeNode("predicate=" + ("ALL" if self.predicate is None
                                          else str(self.predicate))))
        return root

    def to_sexpr(self):
        if self.predicate is None:
            return f"(Delete {self.table_name})"
        return f"(Delete {self.table_name} {_sexpr_predicate(expr_to_dict(self.predicate))})"

    @classmethod
    def _from_dict(cls, data):
        return cls(data["table_name"], expr_from_dict(data["predicate"]))


def _sexpr_predicate(pred: Dict[str, Any]) -> str:
    """把谓词 dict 渲染成 S 表达式片段，如 (> age 18)。"""
    if pred["type"] == "BinaryOp":
        return (f"({pred['op']} {_sexpr_predicate(pred['left'])} "
                f"{_sexpr_predicate(pred['right'])})")
    if pred["type"] == "ColumnRef":
        return pred["name"]
    value = pred["value"]
    if pred["value_type"] == "STRING":
        return f"'{value}'"
    if pred["value_type"] == "NULL":
        return "NULL"
    return str(value)


_PLAN_TYPES = {
    "CreateTable": CreateTablePlan,
    "Insert": InsertPlan,
    "SeqScan": SeqScanPlan,
    "Filter": FilterPlan,
    "Project": ProjectPlan,
    "Delete": DeletePlan,
}
