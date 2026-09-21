"""engine 模块自定义异常体系。

执行期错误统一抛 ExecutionError，由 CLI 层捕获打印。
存储层错误（StorageError）在 engine 边界被捕获并转换为执行错误，
保证上层只需处理一种异常类型。
"""

from __future__ import annotations


class ExecutionError(Exception):
    """执行引擎错误基类（表不存在、记录过大、数据损坏等）。"""
