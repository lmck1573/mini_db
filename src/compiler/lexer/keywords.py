"""SQL 关键字表（集中维护，禁止在别处硬编码）。"""

from __future__ import annotations

# 语句关键字
STATEMENT_KEYWORDS = {
    "CREATE",
    "TABLE",
    "INSERT",
    "INTO",
    "VALUES",
    "SELECT",
    "FROM",
    "WHERE",
    "DELETE",
}

# 数据类型关键字
TYPE_KEYWORDS = {
    "INT",
    "FLOAT",
    "VARCHAR",
    "TEXT",
}

# 其他保留字
OTHER_KEYWORDS = {
    "NULL",
    # 以下为 P1 进阶保留字，词法层先认出来，语法层当前不支持（会报语法错误）
    "AND",
    "OR",
    "NOT",
}

KEYWORDS = STATEMENT_KEYWORDS | TYPE_KEYWORDS | OTHER_KEYWORDS


def is_keyword(word: str) -> bool:
    """判断单词是否为关键字（大小写不敏感）。"""
    return word.upper() in KEYWORDS
