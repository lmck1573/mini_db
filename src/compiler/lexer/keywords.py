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
    "EXPLAIN",
}

# 数据类型关键字
TYPE_KEYWORDS = {
    "INT",
    "FLOAT",
    "VARCHAR",
    "TEXT",
}

# 多表连接关键字（JOIN / INNER JOIN / LEFT OUTER JOIN / CROSS JOIN / ON / AS）
JOIN_KEYWORDS = {
    "JOIN",
    "INNER",
    "LEFT",
    "RIGHT",
    "FULL",
    "OUTER",
    "CROSS",
    "ON",
    "AS",
}

# 分组 / 排序关键字
GROUP_KEYWORDS = {
    "GROUP",
    "BY",
    "HAVING",
    "ORDER",
    "ASC",
    "DESC",
}

# 其他保留字（NULL 与逻辑运算符）
OTHER_KEYWORDS = {
    "NULL",
    "AND",
    "OR",
    "NOT",
}

KEYWORDS = (STATEMENT_KEYWORDS | TYPE_KEYWORDS | JOIN_KEYWORDS
            | GROUP_KEYWORDS | OTHER_KEYWORDS)

# 聚合函数名：词法层**不**设为保留字，避免列名 count/sum 等被误判；
# parser 只在 IDENTIFIER 紧跟 '(' 时按函数调用解析，语义层再校验函数名。
AGGREGATE_FUNCTIONS = ("COUNT", "SUM", "AVG", "MIN", "MAX")


def is_keyword(word: str) -> bool:
    """判断单词是否为关键字（大小写不敏感）。"""
    return word.upper() in KEYWORDS


def is_aggregate_function(word: str) -> bool:
    """判断单词是否为聚合函数名（大小写不敏感）。"""
    return word.upper() in AGGREGATE_FUNCTIONS
