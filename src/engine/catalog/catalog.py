"""系统目录（SystemCatalog）：维护数据库元数据并持久化到 __catalog__ 表。

__catalog__ 本身是一张特殊表（固定列结构），通过 storage_engine 落盘；
它的 root 页固定为第 1 页（bootstrap 时首个分配）。用户表元数据以行的形式
存入 __catalog__，重启时据此恢复。靠存储引擎持久化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.compiler.parser.ast_nodes import BinaryOp, ColumnRef, Literal
from src.compiler.semantic import Catalog, Column, TableSchema

from ..errors import ExecutionError
from ..storage_engine import ColumnDef, StorageEngine

CATALOG_TABLE = "__catalog__"
CATALOG_ROOT_PAGE = 1

# 目录表列定义（与 interface-contract 第 4 节一致）
_CATALOG_COLUMNS = [
    ColumnDef("table_name", "TEXT"),
    ColumnDef("column_name", "TEXT"),
    ColumnDef("col_type", "TEXT"),
    ColumnDef("col_length", "INT"),
    ColumnDef("ordinal", "INT"),
    ColumnDef("root_page_id", "INT"),
]


@dataclass
class _TableMeta:
    """一张表的元数据：列定义 + 数据页链表头。"""

    name: str
    columns: list[ColumnDef]
    root_page_id: int


class SystemCatalog:
    """数据库模式目录，持久化元数据并提供给编译器 / 执行器使用。"""

    def __init__(self, storage_engine: StorageEngine):
        self.se = storage_engine
        self._tables: dict[str, _TableMeta] = {}  # key = 小写表名

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def bootstrap(self) -> None:
        """首次运行创建 __catalog__ 表，否则从磁盘加载全部元数据。"""
        if self.se.page_exists(CATALOG_ROOT_PAGE):#判断是否首次运行
            self.se.open_table(CATALOG_TABLE, _CATALOG_COLUMNS, CATALOG_ROOT_PAGE)
            self._load()
        else:
            self.se.create_table(CATALOG_TABLE, _CATALOG_COLUMNS)#首次，建目录表

    def _load(self) -> None:#否则打开目录表并load

        """从 __catalog__ 表恢复所有用户表元数据，并注册到 storage_engine。"""
        groups: dict[str, list[dict]] = {}
        for row in self.se.scan_table(CATALOG_TABLE):
            groups.setdefault(row["table_name"], []).append(row)
        for tname, rows in groups.items():
            rows.sort(key=lambda r: r["ordinal"])#按 排序重建每张表的列定义
            columns = [
                ColumnDef(r["column_name"], r["col_type"], r["col_length"])
                for r in rows
            ]
            root = rows[0]["root_page_id"]
            self._tables[tname.lower()] = _TableMeta(tname, columns, root)
            self.se.open_table(tname, columns, root)#再 注册回存储引擎

    # ------------------------------------------------------------------ #
    # 元数据操作
    # ------------------------------------------------------------------ #
    def create_table(self, name: str, columns: list) -> int:
        """建表：写 storage_engine + 持久化元数据，返回 root_page_id。"""
        key = name.lower()
        if key == CATALOG_TABLE:
            raise ExecutionError(f"表名 '{name}' 为系统保留名")
        if key in self._tables:
            raise ExecutionError(f"表 '{name}' 已存在")
        cols = [ColumnDef.from_any(c) for c in columns]
        root = self.se.create_table(name, cols)
        for i, col in enumerate(cols):
            self.se.insert_row(
                CATALOG_TABLE, [name, col.name, col.type, col.length, i, root]
            )
        self._tables[key] = _TableMeta(name, cols, root)
        return root

    def get_table(self, name: str) -> Optional[TableSchema]:
        """返回 compiler 的 TableSchema（供语义 / 执行使用）。"""
        meta = self._tables.get(name.lower())
        if meta is None:
            return None
        return TableSchema(
            name=meta.name,
            columns=[Column(c.name, c.type, c.length) for c in meta.columns],
        )

    def list_tables(self) -> list[str]:
        """返回所有用户表名。"""
        return [meta.name for meta in self._tables.values()]

    def drop_table(self, name: str) -> None:
        """删除表：移除内存元数据并标记 __catalog__ 元数据行为已删除。

        说明：DROP TABLE 不在 P0 范围内（无对应 SQL 语句），此方法仅为接口
        完整性提供；数据页回收（归还空闲链表）留待后续阶段。
        """
        key = name.lower()
        if key not in self._tables:
            raise ExecutionError(f"表 '{name}' 不存在")
        del self._tables[key]
        self.se.delete_rows(
            CATALOG_TABLE,
            BinaryOp("=", ColumnRef("table_name"), Literal(name, "STRING")),
        )

    def to_compiler_catalog(self) -> Catalog:
        """转为 compiler 语义分析使用的内存 Catalog。"""
        catalog = Catalog()
        for meta in self._tables.values():
            catalog.create_table(
                TableSchema(
                    name=meta.name,
                    columns=[Column(c.name, c.type, c.length) for c in meta.columns],
                )
            )
        return catalog
