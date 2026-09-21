"""Database：engine 门面，串联 compiler 与 storage / executor。

用法：
    db = Database("data")
    result = db.execute("SELECT * FROM t;")
    db.close()
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from src.compiler.compiler import SQLCompiler
from src.compiler.planner.plan_nodes import (CreateTablePlan, DeletePlan,
                                             InsertPlan, ProjectPlan)
from src.storage import StorageError, StorageManager

from .catalog.catalog import SystemCatalog
from .errors import ExecutionError
from .executor.operator import ExecutionContext
from .executor.operators import build
from .storage_engine.storage_engine import StorageEngine


@dataclass
class QueryResult:
    """一次执行的结果：成功与否、结果集（SELECT 用）、提示信息。"""

    success: bool
    rows: Optional[list[dict]] = None
    message: str = ""


class Database:
    """小型数据库系统门面：启动时恢复元数据，对外提供 execute / flush / close。"""

    def __init__(self, data_dir: str = "data", verbose: bool = False):
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "mini.db")
        self.storage = StorageManager(db_path, verbose=verbose)
        self.storage_engine = StorageEngine(self.storage)
        self.catalog = SystemCatalog(self.storage_engine)
        self.catalog.bootstrap()
        # 用持久化目录初始化编译器，保证重启后语义检查仍正确
        self.compiler = SQLCompiler(self.catalog.to_compiler_catalog())
        self._ctx = ExecutionContext(self.catalog, self.storage_engine)

    def execute(self, sql: str) -> QueryResult:
        """执行一段 SQL（可含多条语句），返回最后一条语句的结果。"""
        results = self.execute_script(sql)
        return results[-1] if results else QueryResult(True, None, "OK")

    def execute_script(self, sql: str) -> list[QueryResult]:
        """执行一段 SQL（含多条语句），返回每条语句的结果。"""
        results, error = self.compiler.compile_safe(sql)
        out: list[QueryResult] = []
        try:
            for r in results:
                if r.plan is not None:
                    out.append(self._execute_plan(r.plan))
        except (ExecutionError, StorageError) as err:
            out.append(QueryResult(False, None, _fmt_error(err)))
        if error is not None:
            out.append(QueryResult(False, None, error.format()))
        return out

    def flush(self) -> None:
        """把脏页写回磁盘（不关闭）。"""
        self.storage_engine.flush()

    def close(self) -> None:
        """落盘并释放文件句柄。"""
        self.storage.close()

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _execute_plan(self, plan) -> QueryResult:
        op = build(plan, self._ctx)
        op.open()
        try:
            if isinstance(plan, ProjectPlan):
                rows = []
                while True:
                    row = op.next()
                    if row is None:
                        break
                    rows.append(row)
                return QueryResult(True, rows, f"查询返回 {len(rows)} 行")
            op.next()  # DDL/DML 副作用已在 open 完成
            if isinstance(plan, CreateTablePlan):
                return QueryResult(True, None, f"表 '{plan.table_name}' 创建成功")
            if isinstance(plan, InsertPlan):
                return QueryResult(True, None, f"插入 {op.affected_rows} 行")
            if isinstance(plan, DeletePlan):
                return QueryResult(True, None, f"删除 {op.affected_rows} 行")
            return QueryResult(True, None, "OK")
        finally:
            op.close()


def _fmt_error(err: Exception) -> str:
    if isinstance(err, ExecutionError):
        return str(err)
    return f"存储错误：{err}"
