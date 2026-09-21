"""模式目录（Catalog）：维护表/列/类型等元数据。

注意：本 Catalog 是编译器语义分析用的**内存结构**，不落盘；
持久化由 engine/catalog 负责（通过 to_dict / from_dict 交换）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class Column:
    """列元数据。"""

    name: str
    type: str  # INT / FLOAT / VARCHAR / TEXT
    length: Optional[int] = None


@dataclass
class TableSchema:
    """表元数据。"""

    name: str
    columns: List[Column]

    @property
    def column_names(self) -> List[str]:
        return [c.name for c in self.columns]

    def get_column(self, name: str) -> Optional[Column]:
        """按列名查找（大小写不敏感）。"""
        lowered = name.lower()
        for col in self.columns:
            if col.name.lower() == lowered:
                return col
        return None


class Catalog:
    """数据库模式目录：表名 -> 表结构（表名大小写不敏感）。"""

    def __init__(self):
        self._tables: Dict[str, TableSchema] = {}

    def has_table(self, name: str) -> bool:
        return name.lower() in self._tables

    def get_table(self, name: str) -> Optional[TableSchema]:
        return self._tables.get(name.lower())

    def create_table(self, schema: TableSchema) -> None:
        """注册一张表；重名时由调用方（analyzer）先检查并抛出带位置的错误。"""
        self._tables[schema.name.lower()] = schema

    def drop_table(self, name: str) -> None:
        self._tables.pop(name.lower(), None)

    def list_tables(self) -> List[str]:
        return [schema.name for schema in self._tables.values()]

    def get_column(self, table: str, column: str) -> Optional[Column]:
        schema = self.get_table(table)
        return schema.get_column(column) if schema else None

    # ------------------------------------------------------------------
    # 与 engine 交换（用于持久化）
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "tables": [
                {
                    "name": schema.name,
                    "columns": [
                        {"name": c.name, "type": c.type, "length": c.length}
                        for c in schema.columns
                    ],
                }
                for schema in self._tables.values()
            ]
        }

    @staticmethod
    def from_dict(data: dict) -> "Catalog":
        catalog = Catalog()
        for table in data.get("tables", []):
            catalog.create_table(
                TableSchema(
                    name=table["name"],
                    columns=[
                        Column(c["name"], c["type"], c.get("length"))
                        for c in table["columns"]
                    ],
                )
            )
        return catalog
