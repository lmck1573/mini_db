"""cache（缓存管理器）：内存页缓存，LRU / FIFO 替换，命中统计与替换日志。

在存储系统三层架构里，cache 是**对外统一入口**（上层只与 cache 打交道）：
    cache（缓存，本模块）  →  page（分配/回收）  →  disk（文件 IO）

它用内存里的 OrderedDict 做缓冲池，实现 LRU 和 FIFO 两种替换策略，同时维护命中统计和替换日志。缓存层是对
外统一入口，上层只跟缓存层打交道。



"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from ..constants import PAGE_SIZE
from ..exceptions import CacheError
from ..page.page_manager import PageManager

_VALID_POLICIES = ("LRU", "FIFO")   # 支持的替换策略（大写，set_policy 会统一转大写）


@dataclass
class _CachePage:
    """缓存中的一页：页号 + 数据 + 脏标记。

    用 dataclass 把"一页缓存项"的元数据打包：
      page_id  —— 页号（同时是 OrderedDict 的 key）
      data     —— 页内容（定长 PAGE_SIZE 字节）
      is_dirty —— 是否为脏页（被改过未落盘）
    """

    page_id: int
    data: bytes
    is_dirty: bool


class CacheManager:
    """固定容量页缓存，按策略替换，维护统计并输出 [_CACHE] 日志。
    用 OrderedDict 做缓冲池。缓存中的每一项是一个 _CachePage，包含三个字段：page_id、data 和
    is_dirty。is_dirty 表示这一页是否被修改过但还没写回磁盘。
    CacheManager 初始化时会检查容量必须为正，然后建立 OrderedDict，初始化命中、未命中、淘汰、回写四个计
    数器，
    最后调用 set_policy 校验并设置策略。set_policy 用 str(policy).upper() 统一转大写，所以 "lru"、
    "fifo" 也能接受；非法策略抛 CacheError。
    LRU 和 FIFO 两种策略都用同一个 OrderedDict 实现。它们唯一的行为差异点在 _touch 方法里：如果当前策
    略是 LRU，命中后调用 self._pages.move_to_end(page_id)，把该页移到队尾，标记为最近使用，于是队首
    永远是最久未使用的页；如果是 FIFO，命中后不做任何事，插入顺序就是淘汰顺序，队首永远是最早进入的页。

    对外接口（见 interface-contract.md 3.3）：
        get_page(page_id) -> bytes      读页（命中/未命中统计 + 按需加载/替换）
        put_page(page_id, data)         写页（置脏）
        flush_page(page_id)             单页回写
        flush_all()                     全部脏页回写
        evict() -> int                  淘汰一页，返回被淘汰页号
        set_policy(policy)              切换策略 "LRU" / "FIFO"
        stats() -> dict                 命中统计
    """

    def __init__(self, page_mgr: PageManager, capacity: int = 64, policy: str = "LRU"):
        # 容量必须为正，否则缓存无法成立（capacity 决定缓存能装几页）。
        if capacity <= 0:
            raise CacheError(f"缓存容量必须为正整数，得到 {capacity}")
        self.page_mgr = page_mgr
        self.capacity = capacity
        self._policy = ""                              # 先占位，下面用 set_policy 统一初始化
        self._pages: OrderedDict[int, _CachePage] = OrderedDict()   # 缓存主体：页号 -> 缓存项

        # 统计计数器
        self._hits = 0          # 命中次数
        self._misses = 0        # 未命中次数
        self._evictions = 0     # 淘汰次数
        self._flushes = 0       # 回写（落盘）次数

        self.set_policy(policy)   # 校验并设置策略（非法策略在此抛 CacheError）

    # ------------------------------------------------------------------ #
    # 策略
    # ------------------------------------------------------------------ #
    def set_policy(self, policy: str) -> None:
        """切换替换策略，"LRU" 或 "FIFO"（大小写不敏感）。

        用 str.upper() 统一转大写，所以 "lru"/"fifo" 也能被接受；
        非法策略抛 CacheError，防止后续用未知策略产生不可预期行为。


        LRU 和 FIFO 共用一套数据结构
        普通实现是两套代码，我用同一个 OrderedDict，两种策略唯一的差别就是命中时要不要 move_to_end。
        LRU 要移，FIFO 不移，淘汰统一弹队首。所以 set_policy 一行就能热切换，而且用同一个访问序列就能
        直观看出两种策略淘汰的是不同的页。比如读 1、2、3 填满容量 3，再读 1，再读 4 触发淘汰：LRU 淘汰
         2，因为 1 被 touch 过变成最新，2 最久未用；FIFO 淘汰 1，因为 1 最早进入，命中也不改变它的位置。
         这个测试就是 test_lru_and_fifo_evict_different_pages。

        """
        p = str(policy).upper()
        if p not in _VALID_POLICIES:
            raise CacheError(f"非法替换策略 {policy!r}，仅支持 LRU / FIFO")
        self._policy = p

    def _touch(self, page_id: int) -> None:
        """LRU 命中后把该页移到队尾（标记为最近使用）；FIFO 不做任何事。

        这是 LRU 与 FIFO 唯一的行为差异点：
        - LRU：move_to_end 让队首始终是"最久未使用"，实现 LRU；
        - FIFO：不移动，插入顺序即淘汰顺序，实现 FIFO。
        """
        if self._policy == "LRU":
            self._pages.move_to_end(page_id)

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    @staticmethod
    def _log(msg: str) -> None:
        """输出 [_CACHE] 前缀日志。

        日志格式（见 interface-contract.md，便于实验报告/现场演示截图）：
            [_CACHE] HIT  page=1
            [_CACHE] MISS page=3 policy=LRU action=load
            [_CACHE] EVICT page=1 (dirty, flushed) policy=LRU
        """
        print(f"[_CACHE] {msg}")

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def get_page(self, page_id: int) -> bytes:
        """返回页数据；命中 hits+1，未命中 misses+1 并按需替换 / 加载。
        读页的核心内容
        流程：
        1. 命中（page_id 在缓存中）：hits+1，LRU 时 touch（移到队尾），打印 HIT，返回数据。
        2. 未命中：misses+1，打印 MISS；
           - 若缓存已满（size >= capacity），先 evict() 腾出空间；
           - 从 page_mgr 读盘加载该页进缓存（此时是干净页 is_dirty=False），返回数据。
        """
        page = self._pages.get(page_id)
        if page is not None:
            self._hits += 1
            self._touch(page_id)
            self._log(f"HIT  page={page_id}")
            return page.data

        self._misses += 1
        self._log(f"MISS page={page_id} policy={self._policy} action=load")

        # 容量检查：缓存已满则先淘汰一页，保证 size 永远 <= capacity
        if len(self._pages) >= self.capacity:
            self.evict()

        # 从磁盘读页并放入缓存（刚加载的页是干净的，没有被修改过）
        data = self.page_mgr.read_page(page_id)
        self._pages[page_id] = _CachePage(page_id=page_id, data=data, is_dirty=False)
        return data

    def put_page(self, page_id: int, data: bytes) -> None:
        """写入（置脏）一页；未命中时先确保容量，再载入并标脏。

        注意：put_page 只改内存并标脏，**不立即落盘**（这就是 write-back 策略）。
        数据要到 flush_page/flush_all 或淘汰时才写回磁盘。
        """
        if len(data) != PAGE_SIZE:
            raise CacheError(f"页数据长度 {len(data)} 不等于 PAGE_SIZE {PAGE_SIZE}")

        if page_id in self._pages:
            # 已在缓存：覆盖数据 + 标脏；LRU 时 touch。
            self._pages[page_id].data = data
            self._pages[page_id].is_dirty = True
            self._touch(page_id)
            return

        # 不在缓存：若已满先淘汰，再把新数据载入并标脏
        if len(self._pages) >= self.capacity:
            self.evict()

        self._pages[page_id] = _CachePage(page_id=page_id, data=data, is_dirty=True)

    def flush_page(self, page_id: int) -> None:
        """若该页在缓存且为脏，立即写回磁盘并清脏标记。

        只有脏页才需要写回（干净页内存和磁盘一致，写回是浪费）；写回后清掉脏标记，
        并让 flushes 计数 +1。
        """
        page = self._pages.get(page_id)
        if page is not None and page.is_dirty:
            self.page_mgr.write_page(page_id, page.data)
            page.is_dirty = False
            self._flushes += 1

    def flush_all(self) -> None:
        """将所有脏页写回磁盘并清脏标记（不主动清空缓存）。

        遍历所有缓存页逐一 flush_page；list(keys()) 是快照，避免遍历中修改集合。
        通常在 close() / 显式提交时调用，保证落盘。
        """
        for page_id in list(self._pages.keys()):
            self.flush_page(page_id)

    def evict(self) -> int:
        """淘汰队首一页（脏页先写回），返回被淘汰页号。

        淘汰对象永远是 OrderedDict 的**队首**（第一个元素）：
        - LRU 下队首 = 最久未使用；
        - FIFO 下队首 = 最早进入。

        步骤：取队首 → 若是脏页先写回磁盘（保证数据不丢，并打印 (dirty, flushed) 日志）
              → 从缓存删除 → evictions 计数 +1 → 返回被淘汰页号。
        """
        if not self._pages:
            raise CacheError("缓存为空，无可淘汰页")

        page_id, page = next(iter(self._pages.items()))   # 队首元素
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
        （与 flush 的区别：flush 是"先落盘再保留"，invalidate 是"直接丢弃不落盘"。）
        """
        if page_id in self._pages:
            del self._pages[page_id]

    def stats(self) -> dict:
        """返回命中统计与缓存状态。

        返回字段（见 interface-contract.md 3.4）：
            hits / misses / hit_rate（4 位小数）/ evictions / flushes / capacity / size
        """
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
