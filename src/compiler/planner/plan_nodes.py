"""执行计划节点定义。

算子集合：CreateTable / Insert / SeqScan / Filter / Project / Delete
          + Join（多表连接）/ Aggregate（分组聚合）/ Sort（排序）。

输出形式：树形结构 / JSON / S 表达式，三者内容一致，
并可通过 `to_html()` 渲染为可视化的计划树（见 visualize.py）。
计划中只保存纯数据（可 JSON 序列化），不引用 AST 对象。
谓词支持任意表达式树（比较 / 逻辑 / 算术 / 一元 / 聚合函数）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from ..parser.ast_nodes import (BinaryOp, ColumnRef, Expr, FunctionCall,
                                Literal, UnaryOp)
from ..treefmt import TreeNode, render_tree


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
        data: Dict[str, Any] = {"type": "ColumnRef", "name": expr.name}
        if expr.table:
            data["table"] = expr.table
        return data
    if isinstance(expr, BinaryOp):
        return {
            "type": "BinaryOp",
            "op": expr.op,
            "left": expr_to_dict(expr.left),
            "right": expr_to_dict(expr.right),
        }
    if isinstance(expr, UnaryOp):
        return {
            "type": "UnaryOp",
            "op": expr.op,
            "operand": expr_to_dict(expr.operand),
        }
    if isinstance(expr, FunctionCall):
        return {
            "type": "FunctionCall",
            "name": expr.name,
            "star": expr.star,
            "arg": expr_to_dict(expr.arg),
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
        return ColumnRef(data["name"], table=data.get("table"))
    if kind == "BinaryOp":
        return BinaryOp(data["op"], expr_from_dict(data["left"]), expr_from_dict(data["right"]))
    if kind == "UnaryOp":
        return UnaryOp(data["op"], expr_from_dict(data["operand"]))
    if kind == "FunctionCall":
        return FunctionCall(data["name"], expr_from_dict(data.get("arg")),
                            data.get("star", False))
    raise ValueError(f"未知的表达式类型：{kind}")


def _sexpr_predicate(pred: Dict[str, Any]) -> str:
    """把谓词 dict 渲染成 S 表达式片段，如 (> age 18) / (COUNT *)。"""
    if pred["type"] == "BinaryOp":
        return (f"({pred['op']} {_sexpr_predicate(pred['left'])} "
                f"{_sexpr_predicate(pred['right'])})")
    if pred["type"] == "UnaryOp":
        return f"({pred['op'].upper()} {_sexpr_predicate(pred['operand'])})"
    if pred["type"] == "FunctionCall":
        inner = "*" if pred.get("star") else _sexpr_predicate(pred["arg"])
        return f"({pred['name'].upper()} {inner})"
    if pred["type"] == "ColumnRef":
        table = pred.get("table")
        return f"{table}.{pred['name']}" if table else pred["name"]
    value = pred["value"]
    if pred["value_type"] == "STRING":
        return f"'{value}'"
    if pred["value_type"] == "NULL":
        return "NULL"
    if pred["value_type"] == "BOOLEAN":
        return "TRUE" if value else "FALSE"
    return str(value)


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

    def to_html(self, title: str = "执行计划") -> str:
        """渲染为自包含 HTML 计划树（延迟导入，避免循环依赖）。"""
        from .visualize import plan_to_html
        return plan_to_html(self, title=title)

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
    source: Optional[str] = None  # 多表查询中该扫描的来源名（别名/表名），单表为 None

    op = "SeqScan"

    def to_dict(self):
        data = {"op": self.op, "table_name": self.table_name}
        if self.source is not None:
            data["source"] = self.source
        return data

    def _tree(self):
        suffix = f"  as={self.source}" if self.source else ""
        return TreeNode(f"SeqScan  table={self.table_name}{suffix}")

    def to_sexpr(self):
        suffix = f" :{self.source}" if self.source else ""
        return f"(SeqScan {self.table_name}{suffix})"

    @classmethod
    def _from_dict(cls, data):
        return cls(data["table_name"], data.get("source"))


@dataclass
class FilterPlan(PlanNode):
    predicate: Expr
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
    """投影算子。

    - 常规路径：`columns` 为 "*" 或列名列表，按名取列；
    - 表达式路径：`exprs` + `labels` 非空，对行求值后按 label 命名输出。
    """

    columns: Union[str, List[str]]
    child: PlanNode
    exprs: Optional[List[Expr]] = None
    labels: Optional[List[str]] = None

    op = "Project"

    def to_dict(self):
        data = {"op": self.op, "columns": self.columns, "child": self.child.to_dict()}
        if self.exprs is not None:
            data["exprs"] = [expr_to_dict(e) for e in self.exprs]
            data["labels"] = list(self.labels or [])
        return data

    def _tree(self):
        if self.exprs is not None:
            pairs = []
            for expr, label in zip(self.exprs, self.labels or []):
                text = str(expr)
                pairs.append(label if text == label else f"{text} AS {label}")
            label_text = str(pairs)
        else:
            label_text = "*" if self.columns == "*" else str(self.columns)
        root = TreeNode(f"Project  columns={label_text}")
        root.add(self.child._tree())
        return root

    def to_sexpr(self):
        if self.exprs is not None:
            cols = " ".join(self.labels or [])
        elif self.columns == "*":
            cols = "*"
        else:
            cols = " ".join(self.columns)
        return f"(Project [{cols}] {self.child.to_sexpr()})"

    @classmethod
    def _from_dict(cls, data):
        exprs = data.get("exprs")
        return cls(
            data["columns"],
            PlanNode.from_dict(data["child"]),
            [expr_from_dict(e) for e in exprs] if exprs is not None else None,
            data.get("labels"),
        )


@dataclass
class DeletePlan(PlanNode):
    table_name: str
    predicate: Optional[Expr]

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


@dataclass
class SortKey:
    """ORDER BY 的一个排序键。"""

    expr: Expr
    desc: bool = False

    def to_dict(self):
        return {"expr": expr_to_dict(self.expr), "desc": self.desc}

    @staticmethod
    def from_dict(data):
        return SortKey(expr_from_dict(data["expr"]), data.get("desc", False))

    def __str__(self):
        return f"{self.expr}{' DESC' if self.desc else ''}"


@dataclass
class AggregateSpec:
    """一个聚合输出项：SUM(age) AS total 之类。"""

    func: str
    label: str
    star: bool = False
    arg: Optional[Expr] = None

    def to_dict(self):
        return {"func": self.func, "label": self.label,
                "star": self.star, "arg": expr_to_dict(self.arg)}

    @staticmethod
    def from_dict(data):
        return AggregateSpec(data["func"], data["label"], data.get("star", False),
                             expr_from_dict(data.get("arg")))

    def __str__(self):
        inner = "*" if self.star else str(self.arg)
        return f"{self.func}({inner})"


@dataclass
class JoinPlan(PlanNode):
    """连接算子：嵌套循环实现，支持 INNER / LEFT / CROSS。"""

    join_type: str
    left: PlanNode
    right: PlanNode
    on: Optional[Expr] = None
    right_columns: List[str] = field(default_factory=list)  # 右侧限定列（LEFT JOIN 补 NULL）

    op = "Join"

    def to_dict(self):
        return {
            "op": self.op,
            "join_type": self.join_type,
            "on": expr_to_dict(self.on),
            "right_columns": list(self.right_columns),
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
        }

    def _tree(self):
        text = f"Join  {self.join_type}"
        if self.on is not None:
            text += f"  on {self.on}"
        root = TreeNode(text)
        root.add(self.left._tree())
        root.add(self.right._tree())
        return root

    def to_sexpr(self):
        cond = f" {_sexpr_predicate(expr_to_dict(self.on))}" if self.on is not None else ""
        return f"(Join {self.join_type}{cond} {self.left.to_sexpr()} {self.right.to_sexpr()})"

    @classmethod
    def _from_dict(cls, data):
        return cls(
            data["join_type"],
            PlanNode.from_dict(data["left"]),
            PlanNode.from_dict(data["right"]),
            expr_from_dict(data.get("on")),
            list(data.get("right_columns", [])),
        )


@dataclass
class AggregatePlan(PlanNode):
    """分组聚合算子：先按 group_by 分组，再计算 aggregates。"""

    group_by: List[Expr]
    group_labels: List[str]
    aggregates: List[AggregateSpec]
    child: PlanNode

    op = "Aggregate"

    def to_dict(self):
        return {
            "op": self.op,
            "group_by": [expr_to_dict(g) for g in self.group_by],
            "group_labels": list(self.group_labels),
            "aggregates": [a.to_dict() for a in self.aggregates],
            "child": self.child.to_dict(),
        }

    def _tree(self):
        group = ", ".join(self.group_labels) if self.group_labels else "（无，全局聚合）"
        aggs = ", ".join(str(a) for a in self.aggregates)
        root = TreeNode(f"Aggregate  group_by=[{group}]  aggs=[{aggs}]")
        root.add(self.child._tree())
        return root

    def to_sexpr(self):
        group = " ".join(self.group_labels)
        aggs = " ".join(str(a) for a in self.aggregates)
        return f"(Aggregate [{group}] [{aggs}] {self.child.to_sexpr()})"

    @classmethod
    def _from_dict(cls, data):
        return cls(
            [expr_from_dict(g) for g in data["group_by"]],
            list(data["group_labels"]),
            [AggregateSpec.from_dict(a) for a in data["aggregates"]],
            PlanNode.from_dict(data["child"]),
        )


@dataclass
class SortPlan(PlanNode):
    """排序算子：对子算子输出全量收集后按 keys 排序。"""

    keys: List[SortKey]
    child: PlanNode

    op = "Sort"

    def to_dict(self):
        return {"op": self.op, "keys": [k.to_dict() for k in self.keys],
                "child": self.child.to_dict()}

    def _tree(self):
        keys = ", ".join(str(k) for k in self.keys)
        root = TreeNode(f"Sort  keys=[{keys}]")
        root.add(self.child._tree())
        return root

    def to_sexpr(self):
        keys = " ".join(str(k) for k in self.keys)
        return f"(Sort [{keys}] {self.child.to_sexpr()})"

    @classmethod
    def _from_dict(cls, data):
        return cls([SortKey.from_dict(k) for k in data["keys"]],
                   PlanNode.from_dict(data["child"]))


_PLAN_TYPES = {
    "CreateTable": CreateTablePlan,
    "Insert": InsertPlan,
    "SeqScan": SeqScanPlan,
    "Filter": FilterPlan,
    "Project": ProjectPlan,
    "Delete": DeletePlan,
    "Join": JoinPlan,
    "Aggregate": AggregatePlan,
    "Sort": SortPlan,
}
