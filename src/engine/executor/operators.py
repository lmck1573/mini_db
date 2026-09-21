"""六个执行算子（火山模型）。

CreateTable / Insert / SeqScan / Filter / Project / Delete。
Row 在算子间以 dict[str, Any] 传递，列顺序由 Project 决定。
"""

from __future__ import annotations

from typing import Any, Iterator, Optional

from src.compiler.planner.plan_nodes import (CreateTablePlan, DeletePlan,
                                             FilterPlan, InsertPlan, PlanNode,
                                             ProjectPlan, SeqScanPlan)

from ..errors import ExecutionError
from ..expr import eval_expr, resolve_column
from .operator import ExecutionContext, Operator

Row = dict[str, Any]


class CreateTableOperator(Operator):
    """建表：通过 catalog 注册表结构（并持久化），不产生输出行。"""

    def __init__(self, plan: CreateTablePlan, ctx: ExecutionContext):
        self.plan = plan
        self.ctx = ctx

    def open(self) -> None:
        self.ctx.catalog.create_table(self.plan.table_name, self.plan.columns)
        self.affected_rows = 0

    def next(self) -> Optional[Row]:
        return None


class InsertOperator(Operator):
    """插入：把部分列补齐为全列顺序后逐行写入表堆，返回插入行数。"""

    def __init__(self, plan: InsertPlan, ctx: ExecutionContext):
        self.plan = plan
        self.ctx = ctx

    def open(self) -> None:
        schema = self.ctx.storage_engine.get_table_schema(self.plan.table_name)
        for row in self.plan.rows:
            value_by_col = {c: lit.value for c, lit in zip(self.plan.columns, row)}
            full_values = [value_by_col.get(col.name) for col in schema]
            self.ctx.storage_engine.insert_row(self.plan.table_name, full_values)
        self.affected_rows = len(self.plan.rows)

    def next(self) -> Optional[Row]:
        return None


class SeqScanOperator(Operator):
    """顺序扫描：遍历表的所有数据页，逐行反序列化（跳过删除行）。"""

    def __init__(self, plan: SeqScanPlan, ctx: ExecutionContext):
        self.plan = plan
        self.ctx = ctx
        self._iter: Optional[Iterator[Row]] = None

    def open(self) -> None:
        self._iter = self.ctx.storage_engine.scan_table(self.plan.table_name)

    def next(self) -> Optional[Row]:
        return next(self._iter, None)

    def close(self) -> None:
        self._iter = None


class FilterOperator(Operator):
    """过滤：用 eval_expr 求谓词，仅放行满足条件的行。"""

    def __init__(self, child: Operator, predicate):
        self.child = child
        self.predicate = predicate

    def open(self) -> None:
        self.child.open()

    def next(self) -> Optional[Row]:
        while True:
            row = self.child.next()
            if row is None:
                return None
            if eval_expr(row, self.predicate):
                return row

    def close(self) -> None:
        self.child.close()


class ProjectOperator(Operator):
    """投影：按列名裁剪；"*" 原样返回整行。"""

    def __init__(self, child: Operator, columns):
        self.child = child
        self.columns = columns

    def open(self) -> None:
        self.child.open()

    def next(self) -> Optional[Row]:
        row = self.child.next()
        if row is None:
            return None
        if self.columns == "*":
            return row
        out = {}
        for c in self.columns:
            key = resolve_column(row, c)
            out[key] = row[key]
        return out

    def close(self) -> None:
        self.child.close()


class DeleteOperator(Operator):
    """删除：对命中行置删除标记，返回删除行数。"""

    def __init__(self, plan: DeletePlan, ctx: ExecutionContext):
        self.plan = plan
        self.ctx = ctx

    def open(self) -> None:
        self.affected_rows = self.ctx.storage_engine.delete_rows(
            self.plan.table_name, self.plan.predicate
        )

    def next(self) -> Optional[Row]:
        return None


def build(plan: PlanNode, ctx: ExecutionContext) -> Operator:
    """把逻辑计划递归构建为算子树。"""
    if isinstance(plan, CreateTablePlan):
        return CreateTableOperator(plan, ctx)
    if isinstance(plan, InsertPlan):
        return InsertOperator(plan, ctx)
    if isinstance(plan, SeqScanPlan):
        return SeqScanOperator(plan, ctx)
    if isinstance(plan, FilterPlan):
        return FilterOperator(build(plan.child, ctx), plan.predicate)
    if isinstance(plan, ProjectPlan):
        return ProjectOperator(build(plan.child, ctx), plan.columns)
    if isinstance(plan, DeletePlan):
        return DeleteOperator(plan, ctx)
    raise ExecutionError(f"不支持的算子类型：{type(plan).__name__}")
