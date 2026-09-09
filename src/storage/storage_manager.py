"""storage 统一入口（StorageManager）：engine 只通过本类访问存储系统。

整合 disk → page → cache 三层，暴露页级读写、页分配 / 释放、缓存、统计与关闭。
分层关系：cache（缓存）→ page（分配 / 回收）→ disk（文件 IO），
上层（engine/storage_engine）只与本类交互，不直接接触子层。
"""

from __future__ import annotations

from .cache.cache_manager import CacheManager
from .constants import PAGE_SIZE
from .disk.disk_manager import DiskManager
from .page.page_manager import PageManager

__all__ = ["StorageManager", "PAGE_SIZE"]


class StorageManager:
    """页式存储系统门面，供 engine/storage_engine 调用。"""

    def __init__(self, db_path: str = "data/mini.db", capacity: int = 64, policy: str = "LRU"):
        self.disk = DiskManager(db_path)
        try:
            self.page_mgr = PageManager(self.disk)
            self.cache = CacheManager(self.page_mgr, capacity=capacity, policy=policy)
        except Exception:
            # 构造失败（如非法策略、元数据损坏）时释放已打开的文件句柄，避免泄漏
            self.disk.close()
            raise

    # ---------- 缓存接口（engine 主要调用） ----------
    def get_page(self, page_id: int) -> bytes:
        """从缓存获取页数据，未命中自动加载并替换（维护命中统计）。"""
        return self.cache.get_page(page_id)

    def put_page(self, page_id: int, data: bytes) -> None:
        """写入（置脏）一页。"""
        self.cache.put_page(page_id, data)

    # ---------- 页管理 ----------
    def alloc_page(self) -> int:
        """分配一个新页并返回 page_id（优先复用空闲页）。"""
        page_id = self.page_mgr.alloc_page()
        # 复用的旧页可能在缓存里留有过期副本，安全起见丢弃
        self.cache.invalidate(page_id)
        return page_id

    def free_page(self, page_id: int) -> None:
        """释放指定页，将其 id 加入空闲链表。"""
        # 先丢弃缓存副本（不回写），再归还到空闲链表，避免脏数据污染
        self.cache.invalidate(page_id)
        self.page_mgr.free_page(page_id)

    # ---------- 落盘与关闭 ----------
    def flush_page(self, page_id: int) -> None:
        """将缓存中的脏页立即写回磁盘。"""
        self.cache.flush_page(page_id)

    def flush_all(self) -> None:
        """遍历缓存，将所有脏页写回磁盘。"""
        self.cache.flush_all()

    def set_policy(self, policy: str) -> None:
        """切换替换策略（"LRU" | "FIFO"）。"""
        self.cache.set_policy(policy)

    def stats(self) -> dict:
        """返回命中统计：hits / misses / hit_rate 等。"""
        return self.cache.stats()

    def close(self) -> None:
        """关闭前刷出所有脏页并释放文件句柄。"""
        self.cache.flush_all()
        self.disk.close()
