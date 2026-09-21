"""系统目录子模块：元数据持久化。"""

from .catalog import CATALOG_TABLE, SystemCatalog

__all__ = ["SystemCatalog", "CATALOG_TABLE"]
