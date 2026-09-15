"""逻辑执行计划优化器（基于规则的等价改写）。

在 planner 产出"原始计划"之后运行，对谓词表达式做等价改写：
**不改变查询语义**，只让计划更简洁、更接近可直接执行的形式。

已实现规则（命中即记录，供 CLI 展示优化过程、供测试断言）：

1. constant_folding    常量折叠
       age > 1 + 2            ->  age > 3
       age + 1 * 2 > 40       ->  age + 2 > 40
2. constant_condition  恒真 / 恒假条件简化（短路）
       1 = 1 AND age > 18      ->  age > 18
       1 = 0 OR  age > 18      ->  age > 18
3. not_elimination     NOT 消除：双重否定 / 比较取反 / 德摩根律
       NOT NOT (a = 1)        ->  a = 1
       NOT (a > 1)            ->  a <= 1
       NOT (a = 1 AND b = 2)  ->  a <> 1 OR b <> 2
4. filter_removal      恒真谓词使 Filter 算子整体消除
       WHERE 1 = 1            ->  计划中不再出现 Filter

设计说明：优化器只做"语义等价"的改写，不重排算子树、不做代价估算，
因此任何改写都不会改变查询结果——这是规则优化可安全离线验证的前提。
"""

from __future__ import annotations

import operator
from typing import List, Optional, Tuple

from ..parser.ast_nodes import (ARITHMETIC_OPS, COMPARISON_OPS, LOGICAL_OPS,
                                BinaryOp, Expr, FunctionCall, Literal, UnaryOp)
from .plan_nodes import (AggregatePlan, AggregateSpec, DeletePlan, FilterPlan,
                         JoinPlan, PlanNode, ProjectPlan, SortKey, SortPlan)

_NUMERIC = ("INT", "FLOAT")

# 运算符 -> 求值函数
_ARITHMETIC = {
    "+": operator.add, "-": operator.sub,
    "*": operator.mul, "/": operator.truediv,
}
_COMPARISON = {
    "=": operator.eq, "<>": operator.ne, "!=": operator.ne,
    "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
}
# 比较运算符取反表（NOT 消除用）
_NEGATED = {
    "=": "<>", "<>": "=", "!=": "=",
    "<": ">=", "<=": ">", ">": "<=", ">=": "<",
}


def _bool_value(expr: Expr) -> Optional[bool]:
    """若表达式是布尔常量则返回其真值，否则返回 None。"""
    if isinstance(expr, Literal) and expr.value_type == "BOOLEAN":
        return bool(expr.value)
    return None


def _comparable_values(left: Literal, right: Literal) -> bool:
    """两个常量是否可比较：数值之间、字符串之间、布尔之间。"""
    lt, rt = left.value_type, right.value_type
    if lt in _NUMERIC and rt in _NUMERIC:
        return True
    return lt == rt and lt in ("STRING", "BOOLEAN")


class Optimizer:
    """规则优化器：对计划做等价改写，并记录命中的规则。"""

    def __init__(self) -> None:
        self.applied: List[str] = []
        self._recorded = set()

    # ------------------------------------------------------------------
    # 规则记录
    # ------------------------------------------------------------------
    def _record(self, rule: str, detail: str) -> None:
        """记录一条规则命中（按内容去重，避免重复刷屏）。"""
        if detail not in self._recorded:
            self._recorded.add(detail)
            self.applied.append(f"[{rule}] {detail}")

    # ------------------------------------------------------------------
    # 表达式改写
    # ------------------------------------------------------------------
    def optimize_expr(self, expr: Expr) -> Expr:
        """递归改写表达式，返回等价但更简洁的表达式。"""
        if isinstance(expr, BinaryOp):
            return self._optimize_binary(expr)
        if isinstance(expr, UnaryOp):
            return self._optimize_unary(expr)
        if isinstance(expr, FunctionCall):
            # 聚合/函数调用：只递归其参数（函数本身不可折叠）
            return FunctionCall(expr.name,
                                self.optimize_expr(expr.arg) if expr.arg is not None else None,
                                expr.star, expr.line, expr.col)
        return expr

    def _optimize_binary(self, expr: BinaryOp) -> Expr:
        op = expr.op.upper() if expr.op.upper() in LOGICAL_OPS else expr.op
        left = self.optimize_expr(expr.left)
        right = self.optimize_expr(expr.right)

        # 规则 1：常量折叠（两侧均为常量时直接算出来）
        folded = self._fold_constants(op, left, right, expr)
        if folded is not None:
            return folded

        # 规则 2：逻辑短路简化
        if op in LOGICAL_OPS:
            simplified = self._short_circuit(op, left, right, expr)
            if simplified is not None:
                return simplified

        return BinaryOp(op, left, right, expr.line, expr.col)

    def _fold_constants(self, op: str, left: Expr, right: Expr,
                        expr: BinaryOp) -> Optional[Expr]:
        """常量折叠：两侧都是常量时求值。不适用则返回 None。"""
        if not (isinstance(left, Literal) and isinstance(right, Literal)):
            return None
        # NULL 参与运算结果未定义，保留原表达式交执行期处理
        if "NULL" in (left.value_type, right.value_type):
            return None

        if op in ARITHMETIC_OPS:
            if left.value_type not in _NUMERIC or right.value_type not in _NUMERIC:
                return None
            if op == "/" and right.value == 0:
                return None  # 除零保留原式，交由运行期报错
            try:
                value = _ARITHMETIC[op](left.value, right.value)
            except (TypeError, ZeroDivisionError):
                return None
            result_type = ("FLOAT" if op == "/" or "FLOAT" in
                           (left.value_type, right.value_type) else "INT")
            self._record("constant_folding", f"{left} {op} {right} -> {value}")
            return Literal(value, result_type, expr.line, expr.col)

        if op in COMPARISON_OPS:
            if not _comparable_values(left, right):
                return None
            value = bool(_COMPARISON[op](left.value, right.value))
            self._record("constant_folding",
                         f"{left} {op} {right} -> {'TRUE' if value else 'FALSE'}")
            return Literal(value, "BOOLEAN", expr.line, expr.col)

        if op in LOGICAL_OPS:
            lv, rv = _bool_value(left), _bool_value(right)
            if lv is None or rv is None:
                return None
            value = (lv and rv) if op == "AND" else (lv or rv)
            self._record("constant_folding",
                         f"{left} {op} {right} -> {'TRUE' if value else 'FALSE'}")
            return Literal(value, "BOOLEAN", expr.line, expr.col)

        return None

    def _short_circuit(self, op: str, left: Expr, right: Expr,
                       expr: BinaryOp) -> Optional[Expr]:
        """恒真/恒假条件简化。不适用则返回 None。"""
        lv, rv = _bool_value(left), _bool_value(right)

        if op == "AND":
            if lv is False or rv is False:
                self._record("constant_condition", "AND 含恒假分支 -> FALSE")
                return Literal(False, "BOOLEAN", expr.line, expr.col)
            if lv is True:
                self._record("constant_condition", "TRUE AND x -> x")
                return right
            if rv is True:
                self._record("constant_condition", "x AND TRUE -> x")
                return left
        else:  # OR
            if lv is True or rv is True:
                self._record("constant_condition", "OR 含恒真分支 -> TRUE")
                return Literal(True, "BOOLEAN", expr.line, expr.col)
            if lv is False:
                self._record("constant_condition", "FALSE OR x -> x")
                return right
            if rv is False:
                self._record("constant_condition", "x OR FALSE -> x")
                return left
        return None

    def _optimize_unary(self, expr: UnaryOp) -> Expr:
        op = expr.op.upper()
        operand = self.optimize_expr(expr.operand)

        if op != "NOT":
            # 一元正负号：常量直接折叠
            if isinstance(operand, Literal) and operand.value_type in _NUMERIC:
                value = operand.value if op == "+" else -operand.value
                self._record("constant_folding", f"{op}{operand} -> {value}")
                return Literal(value, operand.value_type, expr.line, expr.col)
            return UnaryOp(expr.op, operand, expr.line, expr.col)

        # NOT 常量取反
        bv = _bool_value(operand)
        if bv is not None:
            self._record("not_elimination",
                         f"NOT {'TRUE' if bv else 'FALSE'} -> "
                         f"{'FALSE' if bv else 'TRUE'}")
            return Literal(not bv, "BOOLEAN", expr.line, expr.col)

        # 双重否定：NOT NOT x -> x
        if isinstance(operand, UnaryOp) and operand.op.upper() == "NOT":
            self._record("not_elimination", "NOT NOT x -> x")
            return operand.operand

        # 比较取反：NOT (a > 1) -> a <= 1
        if isinstance(operand, BinaryOp) and operand.op in COMPARISON_OPS:
            negated = _NEGATED[operand.op]
            self._record("not_elimination", f"NOT ({operand}) -> {negated}")
            return BinaryOp(negated, operand.left, operand.right,
                            operand.line, operand.col)

        # 德摩根律：NOT (A AND B) -> NOT A OR NOT B
        if isinstance(operand, BinaryOp) and operand.op.upper() in LOGICAL_OPS:
            flipped = "OR" if operand.op.upper() == "AND" else "AND"
            self._record("not_elimination", f"德摩根律：NOT(A {operand.op} B) -> "
                                            f"NOT A {flipped} NOT B")
            return self.optimize_expr(BinaryOp(
                flipped,
                UnaryOp("NOT", operand.left, operand.line, operand.col),
                UnaryOp("NOT", operand.right, operand.line, operand.col),
                expr.line, expr.col))

        return UnaryOp("NOT", operand, expr.line, expr.col)

    # ------------------------------------------------------------------
    # 计划改写
    # ------------------------------------------------------------------
    def optimize_plan(self, plan: PlanNode) -> PlanNode:
        """自底向上改写整棵计划树。"""
        if isinstance(plan, FilterPlan):
            child = self.optimize_plan(plan.child)
            predicate = self.optimize_expr(plan.predicate)
            if _bool_value(predicate) is True:
                self._record("filter_removal", "谓词恒真，Filter 算子整体消除")
                return child
            return FilterPlan(predicate, child)

        if isinstance(plan, ProjectPlan):
            return ProjectPlan(plan.columns, self.optimize_plan(plan.child),
                               plan.exprs, plan.labels)

        if isinstance(plan, SortPlan):
            keys = [SortKey(self.optimize_expr(k.expr), k.desc) for k in plan.keys]
            return SortPlan(keys, self.optimize_plan(plan.child))

        if isinstance(plan, AggregatePlan):
            specs = [
                AggregateSpec(a.func, a.label, a.star,
                              self.optimize_expr(a.arg) if a.arg is not None else None)
                for a in plan.aggregates
            ]
            groups = [self.optimize_expr(g) for g in plan.group_by]
            return AggregatePlan(groups, plan.group_labels, specs,
                                 self.optimize_plan(plan.child))

        if isinstance(plan, JoinPlan):
            return JoinPlan(
                plan.join_type,
                self.optimize_plan(plan.left),
                self.optimize_plan(plan.right),
                self.optimize_expr(plan.on) if plan.on is not None else None,
                plan.right_columns,
            )

        if isinstance(plan, DeletePlan):
            predicate = (self.optimize_expr(plan.predicate)
                         if plan.predicate is not None else None)
            return DeletePlan(plan.table_name, predicate)

        return plan


def optimize_plan(plan: PlanNode) -> Tuple[PlanNode, List[str]]:
    """对外入口：应用全部规则，返回（优化后的计划，规则命中说明列表）。"""
    optimizer = Optimizer()
    return optimizer.optimize_plan(plan), optimizer.applied
