"""cache（缓存管理器）：内存页缓存，LRU / FIFO 替换，命中统计与替换日志。

替换策略实现思路（两种都用 collections.OrderedDict）：
- LRU  ：get_page 命中时 `move_to_end`，把该页移到队尾（最近使用），
         队首（最左）即最久未使用，淘汰时弹队首；
- FIFO ：命中时**不**移动顺序，插入顺序即淘汰顺序，淘汰同样弹队首。

队首统一是"下一个被淘汰者"，区别只在命中时是否 move_to_end。

缓存页元数据：page_id / data(bytes) / is_dirty(bool)。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from ..constants import PAGE_SIZE
from ..exceptions import CacheError
from ..page.page_manager import PageManager

_VALID_POLICIES = ("LRU", "FIFO")


@dataclass
class _CachePage:
    """缓存中的一页：页号 + 数据 + 脏标记。"""

    page_id: int
    data: bytes
    is_dirty: bool


class CacheManager:
    """固定容量页缓存，按策略替换，维护统计并输出 [_CACHE] 日志。"""

    def __init__(self, page_mgr: PageManager, capacity: int = 64, policy: str = "LRU"):
        if capacity <= 0:
            raise CacheError(f"缓存容量必须为正整数，得到 {capacity}")
        self.page_mgr = page_mgr
        self.capacity = capacity
        self._policy = ""
        self._pages: OrderedDict[int, _CachePage] = OrderedDict()

        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._flushes = 0

        self.set_policy(policy)

    # ------------------------------------------------------------------ #
    # 策略
    # ------------------------------------------------------------------ #
    def set_policy(self, policy: str) -> None:
        """切换替换策略，"LRU" 或 "FIFO"（大小写不敏感）。"""
        p = str(policy).upper()
        if p not in _VALID_POLICIES:
            raise CacheError(f"非法替换策略 {policy!r}，仅支持 LRU / FIFO")
        self._policy = p

    def _touch(self, page_id: int) -> None:
        """LRU 命中后把该页移到队尾（标记为最近使用）；FIFO 不做任何事。"""
        if self._policy == "LRU":
            self._pages.move_to_end(page_id)

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    @staticmethod
    def _log(msg: str) -> None:
        print(f"[_CACHE] {msg}")

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def get_page(self, page_id: int) -> bytes:
        """返回页数据；命中 hits+1，未命中 misses+1 并按需替换 / 加载。"""
        page = self._pages.get(page_id)
        if page is not None:
            self._hits += 1
            self._touch(page_id)
            self._log(f"HIT  page={page_id}")
            return page.data

        self._misses += 1
        self._log(f"MISS page={page_id} policy={self._policy} action=load")

        if len(self._pages) >= self.capacity:
            self.evict()

        data = self.page_mgr.read_page(page_id)
        self._pages[page_id] = _CachePage(page_id=page_id, data=data, is_dirty=False)
        return data

    def put_page(self, page_id: int, data: bytes) -> None:
        """写入（置脏）一页；未命中时先确保容量，再载入并标脏。"""
        if len(data) != PAGE_SIZE:
            raise CacheError(f"页数据长度 {len(data)} 不等于 PAGE_SIZE {PAGE_SIZE}")

        if page_id in self._pages:
            self._pages[page_id].data = data
            self._pages[page_id].is_dirty = True
            self._touch(page_id)
            return

        if len(self._pages) >= self.capacity:
            self.evict()

        self._pages[page_id] = _CachePage(page_id=page_id, data=data, is_dirty=True)

    def flush_page(self, page_id: int) -> None:
        """若该页在缓存且为脏，立即写回磁盘并清脏标记。"""
        page = self._pages.get(page_id)
        if page is not None and page.is_dirty:
            self.page_mgr.write_page(page_id, page.data)
            page.is_dirty = False
            self._flushes += 1

    def flush_all(self) -> None:
        """将所有脏页写回磁盘并清脏标记（不主动清空缓存）。"""
        for page_id in list(self._pages.keys()):
            self.flush_page(page_id)

    def evict(self) -> int:
        """淘汰队首一页（脏页先写回），返回被淘汰页号。"""
        if not self._pages:
            raise CacheError("缓存为空，无可淘汰页")

        page_id, page = next(iter(self._pages.items()))
        if page.is_dirty:
            self.page_mgr.write_page(page_id, page.data)
            self._flushes += 1
            self._log(f"EVICT page={page_id} (dirty, flushed) policy={self._policy}")
        else:
            self._log(f"EVICT page={page_id} policy={self._policy}")

        del self._pages[page_id]
        self._evictions += 1
        return page_id

    def invalidate(self, page_id: int) -> None:
        """从缓存中移除指定页且**不回写**。

        仅供 StorageManager.free_page / alloc_page 使用：释放页后其旧数据已失效，
        直接丢弃缓存副本，避免脏数据回写污染空闲链表。
        """
        if page_id in self._pages:
            del self._pages[page_id]

    def stats(self) -> dict:
        """返回命中统计与缓存状态。"""
        total = self._hits + self._misses
        hit_rate = round(self._hits / total, 4) if total else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "evictions": self._evictions,
            "flushes": self._flushes,
            "capacity": self.capacity,
            "size": len(self._pages),
        }
