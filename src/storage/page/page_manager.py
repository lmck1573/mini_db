"""page（页管理器）：页的分配与回收，维护空闲页链表，屏蔽文件细节。
负责页的分配和回收。它维护一个空闲页链表，对上给缓存层提供“页”这个抽象，对下调用磁盘层做真正的文件 IO。哪些页被
占用哪些页空闲、分配时给哪个页号、释放后怎么复用，都是这一层在管。
"""

from __future__ import annotations

import struct

from ..constants import PAGE_SIZE
from ..disk.disk_manager import DiskManager
from ..exceptions import PageError

# 元数据页（第 0 页）布局
_MAGIC = b"MDB1"           # 4 字节魔数，用于校验文件格式
_OFF_FREE_HEAD = 4         # 4 字节：空闲链表头页号
_OFF_PAGE_COUNT = 8        # 4 字节：逻辑页总数

_FREE_END = -1             # 空闲链表尾标记（表示"没有下一页"）


class PageManager:
    """维护空闲页链表，提供页分配 / 释放与读写。

    链表式空闲页”方案
    1.第 0 页保留为元数据页。因为页分配状态必须持久化到磁盘，否则程序重启后不知道哪些页空闲、一共多少页。放
    在第 0 页是因为位置固定，启动时直接读第 0 页就能恢复状态，不需要额外索引文件。元数据页的布局是：offset
     0 存 4 字节魔数 b"MDB1"，用来校验文件格式；offset 4 存 4 字节小端的 free_head，也就是空闲链表头
     页号，-1 表示链表为空；offset 8 存 4 字节小端的 page_count，也就是逻辑页总数。其余字节保留为 0。
    2.空闲页本身用链表组织。每个空闲页自己的前 4 字节存“下一个空闲页号”，-1 表示链尾；元数据页的
    free_head 存链表头页号。这样空闲页本身就是链表节点，不需要额外的内存结构，而且能随页一起持久化。释放页时
    插入链表头，分配页时从链表头取，都是 O(1)。因为每次都插头、取头，自然形成 LIFO，也就是后进先出复用：最后
    释放的页最先被复用。

    对外接口（供 cache 层调用）：
        alloc_page() -> int           分配一页，返回页号
        free_page(page_id)            释放一页
        read_page(page_id) -> bytes   读一页
        write_page(page_id, data)     写一页
        page_count() -> int           页总数
        free_list() -> list[int]      调试用：打印空闲链表
    """

    def __init__(self, disk: DiskManager):
        self.disk = disk

        # 打开时根据磁盘现状初始化元数据页，分两种情况：
        if disk.page_count() == 0:
            # 情况 1：全新文件 —— 先向 disk 追加第 0 页作为元数据页，并写入初始头部。
            #   allocate_page() 会返回 0（第 0 页），随后 _write_meta 写入：
            #   魔数 MDB1、free_head=-1（空链表）、page_count=1（只有第 0 页）。
            disk.allocate_page()
            self._write_meta(free_head=_FREE_END, page_count=1)
        else:
            # 情况 2：已有文件（重启恢复）—— 先读元数据页校验魔数（损坏则抛 PageError），
            #   再把 page_count 对齐到磁盘实际页数（磁盘文件大小才是页数的最终事实来源）。
            free_head, _ = self._read_meta()
            self._write_meta(free_head=free_head, page_count=disk.page_count())

    # ------------------------------------------------------------------ #
    # 元数据页读写
    # ------------------------------------------------------------------ #
    def _read_meta(self) -> tuple[int, int]:
        """读取元数据页，返回 (free_head, page_count)。


        链表式空闲页加元数据页持久化
        传统做法页是顺序分配，删除的页没法回收。page_manager.py 里让每个空闲页的前四个字节存下一个空
        闲页号，把空闲页串成链表。释放就插链表头，分配就从链表头取，实现后进先出复用。同时把空闲链表头和页总数
        写进第 0 页落盘，所以程序重启以后分配状态不丢，释放过的页还能继续复用。这样既不需要额外的索引文件，又
        保证了分配和释放都是 O(1)。而且 free_list() 可以直接打印链表，便于调试和现场演示。
        """
        raw = self.disk.read_page(0)
        if raw[0:4] != _MAGIC:
            # 魔数对不上说明文件不是本系统格式、或被损坏 → 抛 PageError 拒绝继续，
            # 防止把错误数据当有效页管理。
            raise PageError("元数据页魔数校验失败：文件可能已损坏或非本系统格式")
        free_head = struct.unpack("<i", raw[_OFF_FREE_HEAD:_OFF_FREE_HEAD + 4])[0]
        page_count = struct.unpack("<i", raw[_OFF_PAGE_COUNT:_OFF_PAGE_COUNT + 4])[0]
        return free_head, page_count

    def _write_meta(self, free_head: int, page_count: int) -> None:
        """把 (free_head, page_count) 写入元数据页，其余字节保持 0。

        每次分配/释放后都要调用它把最新状态落盘，保证"崩溃/重启后分配状态不丢"。
        """
        raw = bytearray(PAGE_SIZE)                        # 先构造全 0 的一页
        raw[0:4] = _MAGIC                                 # 写入魔数
        raw[_OFF_FREE_HEAD:_OFF_FREE_HEAD + 4] = struct.pack("<i", free_head)
        raw[_OFF_PAGE_COUNT:_OFF_PAGE_COUNT + 4] = struct.pack("<i", page_count)
        self.disk.write_page(0, bytes(raw))               # 整页写回第 0 页

    # ------------------------------------------------------------------ #
    # 页分配 / 释放
    # ------------------------------------------------------------------ #
    def alloc_page(self) -> int:
        """
        分配一页：优先复用空闲链表头，否则向 disk 申请新页。

        算法：
        1. 读元数据页拿到 (free_head, page_count)；
        2. 若 free_head != -1：说明有空闲页，复用链表头这一页：
             - 读该页前 4 字节，得到"下一个空闲页号" next_free；
             - 把该页内容清零（旧数据不残留，也清掉旧的 next 指针）；
             - 更新元数据页 free_head = next_free（链表头后移一位）；
             - 返回被复用的页号 free_head。
        3. 若 free_head == -1：空闲链表为空，向 disk 追加新页：
             - disk.allocate_page() 返回"追加前的页总数"即新页号；
             - page_count + 1 写回元数据页；
             - 返回新页号。
        """
        free_head, page_count = self._read_meta()

        if free_head != _FREE_END:
            # 复用空闲页：该页前 4 字节记录了下一个空闲页号
            raw = self.disk.read_page(free_head)
            next_free = struct.unpack("<i", raw[0:4])[0]
            # 复用时清零整页，保证"初始内容全零"（这也是测试 test_reused_page_is_zeroed 的依据）
            self.disk.write_page(free_head, b"\x00" * PAGE_SIZE)
            self._write_meta(free_head=next_free, page_count=page_count)
            return free_head

        # 空闲链表为空：向 disk 追加新页，并更新元数据页总数
        page_id = self.disk.allocate_page()
        self._write_meta(free_head=_FREE_END, page_count=page_count + 1)
        return page_id

    def free_page(self, page_id: int) -> None:
        """释放指定页，将其插入空闲链表头。


        算法（LIFO 复用的关键）：
        1. 合法性检查：第 0 页受保护不能释放；page_id 不能越界；
        2. 把"当前链表头 free_head"写入待释放页的前 4 字节（即让该页指向原链表头），
           其余字节清零；
        3. 更新元数据页 free_head = 该页号（该页成为新的链表头）。

        因为每次释放都插到表头，分配又总是从表头取，所以"最后释放的页最先被复用"，
        即 LIFO（后进先出）。
        """
        if page_id <= 0:
            raise PageError(f"无法释放第 {page_id} 页：第 0 页为元数据页，受保护")

        free_head, page_count = self._read_meta()
        if page_id >= page_count:
            raise PageError(f"page_id {page_id} 越界：页总数 {page_count}")

        # 把当前链表头写入待释放页的前 4 字节，其余清零
        raw = bytearray(PAGE_SIZE)
        raw[0:4] = struct.pack("<i", free_head)
        self.disk.write_page(page_id, bytes(raw))

        # 待释放页成为新的链表头
        self._write_meta(free_head=page_id, page_count=page_count)

    # ------------------------------------------------------------------ #
    # 页读写（向上层 cache 提供）
    # ------------------------------------------------------------------ #
    def read_page(self, page_id: int) -> bytes:
        """读一页：直接透传给 disk。"""
        return self.disk.read_page(page_id)

    def write_page(self, page_id: int, data: bytes) -> None:
        """写一页：先校验长度必须等于 PAGE_SIZE，再透传给 disk。

        长度校验放在这里（而非只在 disk），是 page 层对"整页写"约定的再次兜底，
        保证上层传入的字节流不会因长度错误破坏页的定长对齐。
        """
        if len(data) != PAGE_SIZE:
            raise PageError(f"页数据长度 {len(data)} 不等于 PAGE_SIZE {PAGE_SIZE}")
        self.disk.write_page(page_id, data)

    def page_count(self) -> int:
        """返回当前页总数（直接来自 disk）。"""
        return self.disk.page_count()

    def free_list(self) -> list[int]:
        """调试用：返回当前空闲链表中的所有页号（不修改状态）。

        从链表头 free_head 出发，逐页读前 4 字节的 next 指针遍历，
        直到 -1 链尾；用 seen 集合检测链表是否成环（防御性检查）。
        这个函数专门用于调试和测试，方便现场打印空闲页状态。
        """
        free_head, _ = self._read_meta()
        result: list[int] = []
        seen: set[int] = set()
        cur = free_head
        while cur != _FREE_END:
            if cur in seen:
                raise PageError(f"空闲链表成环：页 {cur} 重复出现")
            seen.add(cur)
            result.append(cur)
            raw = self.disk.read_page(cur)
            cur = struct.unpack("<i", raw[0:4])[0]
        return result
