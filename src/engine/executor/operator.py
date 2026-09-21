"""执行算子基类与运行环境。

火山模型：每个算子实现 open() -> next() -> close()，next() 返回一行（dict）
或 None 表示结束。构建入口 build() 位于 operators.py。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

Row = dict[str, Any]


@dataclass
class ExecutionContext:
    """算子运行环境：系统目录 + 存储引擎。"""

    catalog: Any
    storage_engine: Any


class Operator:
    """执行算子基类。"""

    affected_rows: Optional[int] = None  # DDL/DML 算子用：影响行数

    def open(self) -> None:
        raise NotImplementedError

    def next(self) -> Optional[Row]:
        raise NotImplementedError

    def close(self) -> None:
        pass
