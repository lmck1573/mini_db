"""storage（模块二）：页式存储系统。

对外统一入口为 StorageManager（见 storage_manager.py），engine 只与本包的
StorageManager 交互，不直接接触 disk / page / cache 子层。
"""

from .cache.cache_manager import CacheManager
from .constants import PAGE_SIZE
from .disk.disk_manager import DiskManager
from .exceptions import CacheError, DiskError, PageError, StorageError
from .page.page_manager import PageManager
from .storage_manager import StorageManager

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
