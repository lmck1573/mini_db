"""page（页管理器）：页的分配与回收，维护空闲页链表，屏蔽文件细节。

空闲页管理方案：
- 第 0 页保留为**元数据页**，记录空闲链表头与页总数，持久化到磁盘头部，
  程序重启后分配状态不丢失；
- 空闲页采用"链表式"：每个空闲页的前 4 字节存下一个空闲页号，-1 表示链尾；
- 释放页时插入链表头，分配时优先从链表头取用，实现"优先复用空闲页"。

元数据页（第 0 页）字节布局：
    offset 0 : 魔数 b"MDB1"（4 字节，校验文件格式）
    offset 4 : free_head（4 字节小端，空闲链表头页号，-1 表示空）
    offset 8 : page_count（4 字节小端，逻辑页总数，含第 0 页）
    其余字节保留为 0
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

_FREE_END = -1             # 空闲链表尾标记


class PageManager:
    """维护空闲页链表，提供页分配 / 释放与读写。"""

    def __init__(self, disk: DiskManager):
        self.disk = disk

        if disk.page_count() == 0:
            # 全新文件：先追加第 0 页作为元数据页，并初始化头部
            disk.allocate_page()
            self._write_meta(free_head=_FREE_END, page_count=1)
        else:
            # 已有文件：校验魔数（文件损坏时抛 PageError），并把页总数对齐到磁盘实际值
            free_head, _ = self._read_meta()
            self._write_meta(free_head=free_head, page_count=disk.page_count())

    # ------------------------------------------------------------------ #
    # 元数据页读写
    # ------------------------------------------------------------------ #
    def _read_meta(self) -> tuple[int, int]:
        """读取元数据页，返回 (free_head, page_count)。"""
        raw = self.disk.read_page(0)
        if raw[0:4] != _MAGIC:
            raise PageError("元数据页魔数校验失败：文件可能已损坏或非本系统格式")
        free_head = struct.unpack("<i", raw[_OFF_FREE_HEAD:_OFF_FREE_HEAD + 4])[0]
        page_count = struct.unpack("<i", raw[_OFF_PAGE_COUNT:_OFF_PAGE_COUNT + 4])[0]
        return free_head, page_count

    def _write_meta(self, free_head: int, page_count: int) -> None:
        """把 (free_head, page_count) 写入元数据页，其余字节保持 0。"""
        raw = bytearray(PAGE_SIZE)
        raw[0:4] = _MAGIC
        raw[_OFF_FREE_HEAD:_OFF_FREE_HEAD + 4] = struct.pack("<i", free_head)
        raw[_OFF_PAGE_COUNT:_OFF_PAGE_COUNT + 4] = struct.pack("<i", page_count)
        self.disk.write_page(0, bytes(raw))

    # ------------------------------------------------------------------ #
    # 页分配 / 释放
    # ------------------------------------------------------------------ #
    def alloc_page(self) -> int:
        """分配一页：优先复用空闲链表头，否则向 disk 申请新页。

        复用空闲页时同时清零该页（含旧 next 指针），保证"初始内容全零"。
        """
        free_head, page_count = self._read_meta()

        if free_head != _FREE_END:
            # 复用空闲页：该页前 4 字节记录了下一个空闲页号
            raw = self.disk.read_page(free_head)
            next_free = struct.unpack("<i", raw[0:4])[0]
            self.disk.write_page(free_head, b"\x00" * PAGE_SIZE)
            self._write_meta(free_head=next_free, page_count=page_count)
            return free_head

        # 空闲链表为空：向 disk 追加新页，并更新元数据页总数
        page_id = self.disk.allocate_page()
        self._write_meta(free_head=_FREE_END, page_count=page_count + 1)
        return page_id

    def free_page(self, page_id: int) -> None:
        """释放指定页，将其插入空闲链表头。"""
        if page_id <= 0:
            raise PageError(f"无法释放第 {page_id} 页：第 0 页为元数据页，受保护")

        free_head, page_count = self._read_meta()
        if page_id >= page_count:
            raise PageError(f"page_id {page_id} 越界：页总数 {page_count}")

        # 把当前链表头写入待释放页的前 4 字节，其余清零
        raw = bytearray(PAGE_SIZE)
        raw[0:4] = struct.pack("<i", free_head)
        self.disk.write_page(page_id, bytes(raw))

        self._write_meta(free_head=page_id, page_count=page_count)

    # ------------------------------------------------------------------ #
    # 页读写（向上层 cache 提供）
    # ------------------------------------------------------------------ #
    def read_page(self, page_id: int) -> bytes:
        return self.disk.read_page(page_id)

    def write_page(self, page_id: int, data: bytes) -> None:
        if len(data) != PAGE_SIZE:
            raise PageError(f"页数据长度 {len(data)} 不等于 PAGE_SIZE {PAGE_SIZE}")
        self.disk.write_page(page_id, data)

    def page_count(self) -> int:
        return self.disk.page_count()

    def free_list(self) -> list[int]:
        """调试用：返回当前空闲链表中的所有页号（不修改状态）。"""
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
