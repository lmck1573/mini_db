"""disk（磁盘管理器）：把单个文件模拟为"磁盘"，按固定页大小做定长块读写。

职责边界（见 disk/AGENTS.md）：
- 只负责文件创建、页追加、整页读写、落盘；
- 不做缓存、不做空闲页管理、不解释页内字节含义。
"""

from __future__ import annotations

import os

from ..constants import PAGE_SIZE
from ..exceptions import DiskError


class DiskManager:
    """以单个文件模拟磁盘，按 PAGE_SIZE 切分为定长页。

    页号从 0 开始，页 `i` 对应文件字节区间 `[i*page_size, (i+1)*page_size)`。
    """

    def __init__(self, path: str, page_size: int = PAGE_SIZE):
        self.path = path
        self.page_size = page_size

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # 不存在则创建；已存在则以可读写方式打开（不截断，保留已有页）
        if os.path.exists(path):
            self._file = open(path, "r+b")
        else:
            self._file = open(path, "w+b")

    # ------------------------------------------------------------------ #
    # 私有工具
    # ------------------------------------------------------------------ #
    def _file_size(self) -> int:
        """返回当前文件字节数（不改变读写指针）。"""
        pos = self._file.tell()
        self._file.seek(0, os.SEEK_END)
        size = self._file.tell()
        self._file.seek(pos)
        return size

    def _check_page_id(self, page_id: int) -> None:
        """校验页号在合法范围 [0, page_count)。"""
        count = self.page_count()
        if page_id < 0 or page_id >= count:
            raise DiskError(f"page_id {page_id} 越界：合法范围 [0, {count - 1}]")

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def read_page(self, page_id: int) -> bytes:
        """读取指定页，返回定长 page_size 字节；越界抛 DiskError。"""
        self._check_page_id(page_id)
        self._file.seek(page_id * self.page_size)
        data = self._file.read(self.page_size)
        # 页内不足 page_size（正常不会发生）时以 0 补齐，保证定长
        if len(data) < self.page_size:
            data += b"\x00" * (self.page_size - len(data))
        return data

    def write_page(self, page_id: int, data: bytes) -> None:
        """将定长 page_size 字节写回指定页；长度不符或越界抛 DiskError。"""
        if len(data) != self.page_size:
            raise DiskError(
                f"写入数据长度 {len(data)} 不等于 PAGE_SIZE {self.page_size}"
            )
        self._check_page_id(page_id)
        self._file.seek(page_id * self.page_size)
        self._file.write(data)
        self._file.flush()

    def allocate_page(self) -> int:
        """在文件末尾追加一页全零数据，返回新页号（即追加前的页总数）。"""
        page_id = self.page_count()
        self._file.seek(0, os.SEEK_END)
        self._file.write(b"\x00" * self.page_size)
        self._file.flush()
        return page_id

    def page_count(self) -> int:
        """返回当前页总数（文件大小 // 页大小）。"""
        return self._file_size() // self.page_size

    def close(self) -> None:
        """刷盘并释放文件句柄。"""
        if not self._file.closed:
            self._file.flush()
            self._file.close()
