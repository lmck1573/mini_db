"""disk（磁盘管理器）：把单个文件模拟为"磁盘"，按固定页大小做定长块读写。

在存储系统的三层架构里，disk 是**最底层**，职责最单一：
    cache（缓存）  →  page（分配/回收）  →  disk（文件 IO）

职责边界（见 disk/AGENTS.md）：
- 只负责：文件创建、页追加（allocate）、整页读（read）、整页写（write）、落盘（flush）。

它把一个文件当成磁盘，按 4096 字节切成定长页，页号从 0 开始。第 i 页对应文件里的字节区间是 [i * PAGE_SIZE,
 (i+1) * PAGE_SIZE)，所以“读第 i 页”就是 seek(i * PAGE_SIZE) 之后 read(PAGE_SIZE)，“写第 i 页”
 同理。用定长页的好处是，定位任意页都是 O(1) 的 seek，不需要额外索引表。磁盘层只负责文件创建、页追加、整页读、
 整页写和落盘，不做缓存，也不做空闲页管理，职责非常单一。
"""

from __future__ import annotations

import os

from ..constants import PAGE_SIZE
from ..exceptions import DiskError


class DiskManager:
    """初始化时会区分文件是否存在。如果文件已存在，用 "r+b" 以读写方式打开，并且不截断，保留上次落盘的已有页；
    如果文件不存在，用 "w+b" 新建空文件。这里特别要注意，不能用 "w+b" 打开已存在的文件，否则会把历史数据全部
    清空。

    """

    def __init__(self, path: str, page_size: int = PAGE_SIZE):
        # 文件路径与页大小：路径由上层传入（禁止硬编码），页大小默认 4KB。
        self.path = path
        self.page_size = page_size

        # 自动创建父目录（例如 data/），避免文件不存在时因目录缺失而报错。
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # 打开策略（关键：区分"新建"和"已存在"）：
        #   - 不存在 → "w+b" 新建空文件；
        #   - 已存在 → "r+b" 以读写方式打开且**不截断**，保留上次落盘的已有页（持久化）。
        #     注意不能用 "w+b" 打开已存在的文件，否则会把历史数据全部清空。
        if os.path.exists(path):
            self._file = open(path, "r+b")
        else:
            self._file = open(path, "w+b")

    # ------------------------------------------------------------------ #
    # 私有工具
    # ------------------------------------------------------------------ #
    def _file_size(self) -> int:
        """返回当前文件字节数（不改变读写指针）。

        实现：先记住当前指针位置 tell()，再 seek 到文件末尾取大小，最后把指针还原。
        这样调用方不受副作用影响。
        """
        pos = self._file.tell()
        self._file.seek(0, os.SEEK_END)
        size = self._file.tell()
        self._file.seek(pos)
        return size

    def _check_page_id(self, page_id: int) -> None:
        """校验页号在合法范围 [0, page_count)，越界抛 DiskError。

        这是"越界保护"：读/写一个不存在的页应当显式报错，而不是静默读脏数据或写坏文件。
        """
        count = self.page_count()
        if page_id < 0 or page_id >= count:
            raise DiskError(f"page_id {page_id} 越界：合法范围 [0, {count - 1}]")

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    def read_page(self, page_id: int) -> bytes:
        """读取指定页，返回定长 page_size 字节；越界抛 DiskError。

        步骤：越界校验 → seek 到 page_id*page_size → 读 page_size 字节。
        若读到的字节不足 page_size（正常不会发生），用 0 补齐以保证"定长"约定。
        """
        self._check_page_id(page_id)
        self._file.seek(page_id * self.page_size)
        data = self._file.read(self.page_size)
        # 页内不足 page_size（正常不会发生）时以 0 补齐，保证定长
        if len(data) < self.page_size:
            data += b"\x00" * (self.page_size - len(data))
        return data

    def write_page(self, page_id: int, data: bytes) -> None:
        """将定长 page_size 字节写回指定页；长度不符或越界抛 DiskError。

        注意点：
        - 先校验 data 长度必须等于 page_size，保证"整页写"，不会出现半页导致页错位；
        - 写完立即 flush()，把数据从用户态缓冲区刷到 OS 文件，保证落盘语义。
        """
        if len(data) != self.page_size:
            raise DiskError(
                f"写入数据长度 {len(data)} 不等于 PAGE_SIZE {self.page_size}"
            )
        self._check_page_id(page_id)
        self._file.seek(page_id * self.page_size)
        self._file.write(data)
        self._file.flush()

    def allocate_page(self) -> int:
        """在文件末尾追加一页全零数据，返回新页号（即追加前的页总数）。

        这是"分配新页"的物理动作：向文件尾部追加 PAGE_SIZE 个 0 字节，文件变长。
        新页号 = 追加前的 page_count()，天然是"当前最大页号 + 1"，保证页号单调递增。
        注意：disk 层只负责"物理上多出一页"，不关心这一页是否已被逻辑释放，
        页的"复用/回收"由上层 page 管理（见 page_manager）。
        """
        page_id = self.page_count()
        self._file.seek(0, os.SEEK_END)
        self._file.write(b"\x00" * self.page_size)
        self._file.flush()
        return page_id

    def page_count(self) -> int:
        """返回当前页总数（文件大小 // 页大小）。

        这就是"从文件大小推算页数"：因为每页定长 page_size，文件总字节数整除 page_size
        就是页数。重启后 page 层靠它恢复"磁盘上一共多少页"。
        """
        return self._file_size() // self.page_size

    def close(self) -> None:
        """刷盘并释放文件句柄。

        释放句柄前先 flush 一次，确保缓冲区数据落盘，避免进程退出丢数据。
        """
        if not self._file.closed:
            self._file.flush()
            self._file.close()
