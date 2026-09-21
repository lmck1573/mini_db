"""engine（模块三）：数据库系统。

门面类 Database 串联 compiler 与 storage，实现执行算子、存储引擎与系统目录。
"""

from .database import Database, QueryResult
from .errors import ExecutionError

__all__ = ["Database", "QueryResult", "ExecutionError"]
