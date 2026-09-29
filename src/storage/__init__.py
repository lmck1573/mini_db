"""storage（模块二）：页式存储系统。

对外统一入口为 StorageManager（见 storage_manager.py），engine 只与本包的
StorageManager 交互，不直接接触 disk / page / cache 子层。

────────────────────────────────────────────────────────────
答辩要点（接口与集成）：本包对外暴露哪些符号？为什么？
────────────────────────────────────────────────────────────
engine 要对接存储层，只需要 `from src.storage import StorageManager, PAGE_SIZE`。
本包通过 __all__ 明确导出两类符号：

1. 供上层直接使用的"门面 + 常量"：
   - StorageManager —— engine 唯一需要实例化的入口类（门面）；
   - PAGE_SIZE       —— 页大小常量，engine 序列化行数据时也需要知道页多大。

2. 供测试/调试/精细使用而导出的子层类与异常：
   - DiskManager / PageManager / CacheManager —— 三个子层类，测试可以单独构造某层验证
     （例如预设测试 test_evict_empty_cache_raises 就单独 new 了这三层来测空缓存淘汰）；
   - StorageError 及其子类 DiskError / PageError / CacheError —— engine 用
     `except StorageError` 统一兜底所有存储层错误。

模块边界约束（见根 AGENTS.md）：storage 只允许依赖标准库，**禁止 import src.compiler /
src.engine**，也不理解表/行/SQL 语义——它只是一块"按 4KB 分页的磁盘"。
"""

from .cache.cache_manager import CacheManager
from .constants import PAGE_SIZE
from .disk.disk_manager import DiskManager
from .exceptions import CacheError, DiskError, PageError, StorageError
from .page.page_manager import PageManager
from .storage_manager import StorageManager

# 显式声明导出清单：让 `from src.storage import *` 的行为可控，
# 也作为"接口契约"的可读文档——答辩时可直接指给老师看。
__all__ = [
    "StorageManager",
    "DiskManager",
    "PageManager",
    "CacheManager",
    "StorageError",
    "DiskError",
    "PageError",
    "CacheError",
    "PAGE_SIZE",
]
