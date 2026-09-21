"""存储引擎（StorageEngine）：在页式存储之上实现表堆（TableHeap）。

负责记录与页的映射、行序列化、页内槽位分配、表的扩展与删除标记。上层
executor 只通过本类读写表数据，不直接接触 storage 的页接口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from src.storage import PAGE_SIZE, StorageError, StorageManager

from ..errors import ExecutionError
from ..expr import eval_expr
from .row_codec import (decode_row, encode_row, get_next_page_id, get_record,
                        is_deleted, mark_deleted, new_page, set_next_page_id,
                        slot_count, try_insert_record)

Row = dict[str, Any]


@dataclass
class ColumnDef:
    """列定义（name / type / length），结构与 compiler 的 Column / plan dict 一致。"""

    name: str
    type: str  # INT / FLOAT / VARCHAR / TEXT
    length: int | None = None

    @classmethod
    def from_any(cls, c) -> "ColumnDef":
        """从 plan dict / compiler Column / ColumnDef 归一化为 ColumnDef。"""
        if isinstance(c, cls):
            return c
        if isinstance(c, dict):
            return cls(c["name"], c["type"], c.get("length"))
        # compiler 的 Column（有 name / type / length 属性）
        if hasattr(c, "name") and hasattr(c, "type"):
            return cls(c.name, c.type, getattr(c, "length", None))
        raise ExecutionError(f"无法识别的列定义：{c!r}")


@dataclass
class _TableHeap:  # 表堆
    """一张表的运行时状态：列定义 + 数据页链表头。"""

    name: str
    columns: list[ColumnDef]
    root_page_id: int


class StorageEngine:
    """表堆集合：管理每张表的页链表与行序列化。"""

    def __init__(self, storage: StorageManager):
        self.storage = storage
        self._tables: dict[str, _TableHeap] = {}  # key = 小写表名

    # ------------------------------------------------------------------ #
    # 表生命周期
    # ------------------------------------------------------------------ #
    def create_table(self, table_name: str, columns: list) -> int:
        """建表：分配首页、注册列定义，返回 root_page_id。"""
        key = table_name.lower()
        if key in self._tables:
            raise ExecutionError(f"表 '{table_name}' 已存在")
        cols = [ColumnDef.from_any(c) for c in columns]
        root = self.storage.alloc_page()
        self.storage.put_page(root, new_page(root))
        self._tables[key] = _TableHeap(table_name, cols, root)
        return root

    def open_table(self, table_name: str, columns: list, root_page_id: int) -> None:
        """重启时注册一张已存在的表（不重新分配页）。"""
        self._tables[table_name.lower()] = _TableHeap(
            table_name, [ColumnDef.from_any(c) for c in columns], root_page_id
        )

    def has_table(self, table_name: str) -> bool:
        """判断表是否已注册（内存态）。"""
        return table_name.lower() in self._tables

    def page_exists(self, page_id: int) -> bool:
        """判断某页是否存在（读不到即视为不存在），用于启动时探测目录是否已建。"""
        try:
            self.storage.get_page(page_id)
            return True
        except StorageError:
            return False

    def get_table_schema(self, table_name: str) -> list[ColumnDef]:
        """返回表的列定义（按物理列序）。"""
        return list(self._require(table_name).columns)

    # ------------------------------------------------------------------ #
    # 行操作
    # ------------------------------------------------------------------ #
    def insert_row(self, table_name: str, values: list) -> int:
        """插入一行（值按全列顺序），返回 row_id（page_id * PAGE_SIZE + slot）。"""
        heap = self._require(table_name)
        record = encode_row(values, heap.columns)#化成字节
        page_id = heap.root_page_id
        while True:
            raw = bytearray(self.storage.get_page(page_id))
            slot = try_insert_record(raw, record)#从首页开始 找空间
            if slot is not None:
                self.storage.put_page(page_id, bytes(raw))
                return page_id * PAGE_SIZE + slot
            nxt = get_next_page_id(raw)  # 多个数据页串成单向链表
            if nxt == -1:
                # 链尾空间不足：申请新页并挂到链表尾
                new_pid = self.storage.alloc_page()
                set_next_page_id(raw, new_pid)
                self.storage.put_page(page_id, bytes(raw))
                new_raw = bytearray(new_page(new_pid))
                slot = try_insert_record(new_raw, record)
                if slot is None:
                    raise ExecutionError("单条记录超过页大小，无法插入")
                self.storage.put_page(new_pid, bytes(new_raw))
                return new_pid * PAGE_SIZE + slot
            page_id = nxt

    def scan_table(self, table_name: str) -> Iterator[Row]:
        """顺序扫描整表，跳过已删除行。"""
        heap = self._require(table_name)
        page_id = heap.root_page_id
        while page_id != -1:
            raw = self.storage.get_page(page_id)
            for slot in range(slot_count(raw)):
                record = get_record(raw, slot)
                if record is not None and not is_deleted(record):
                    yield decode_row(record, heap.columns)
            page_id = get_next_page_id(raw)

    def delete_rows(self, table_name: str, predicate) -> int:
        """按谓词标记删除命中行，返回删除行数；predicate=None 表示清空整表。"""
        heap = self._require(table_name)
        count = 0
        page_id = heap.root_page_id
        while page_id != -1:
            raw = bytearray(self.storage.get_page(page_id))
            changed = False
            for slot in range(slot_count(raw)):
                record = get_record(raw, slot)
                if record is None or is_deleted(record):
                    continue
                if predicate is None or eval_expr(decode_row(record, heap.columns), predicate):
                    mark_deleted(raw, slot)
                    changed = True
                    count += 1
            if changed:
                self.storage.put_page(page_id, bytes(raw))
            page_id = get_next_page_id(raw)
        return count

    def flush(self) -> None:
        """把脏页写回磁盘。"""
        self.storage.flush_all()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _require(self, table_name: str) -> _TableHeap:
        heap = self._tables.get(table_name.lower())
        if heap is None:
            raise ExecutionError(f"表 '{table_name}' 不存在")
        return heap
