"""Token 定义：种别码 + 词素值 + 位置信息。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TokenType(Enum):
    """单词种别码。"""

    KEYWORD = "KEYWORD"
    IDENTIFIER = "IDENTIFIER"
    CONST = "CONST"
    OPERATOR = "OPERATOR"
    DELIMITER = "DELIMITER"
    EOF = "EOF"


class ConstType:
    """常量的值类型（仅 CONST Token 使用）。"""

    INT = "INT"
    FLOAT = "FLOAT"
    STRING = "STRING"
    NULL = "NULL"


@dataclass
class Token:
    """一个单词符号。

    规范输出为四元式：[种别码，词素值，行号，列号]
    value_type / value 是常量额外携带的解析结果，供后续阶段使用。
    """

    type: TokenType
    lexeme: str
    line: int
    col: int
    value_type: str | None = None
    value: Any | None = None

    def to_tuple(self) -> tuple[str, str, int, int]:
        """返回四元式 (种别码, 词素值, 行号, 列号)。"""
        return (self.type.value, self.lexeme, self.line, self.col)

    def __str__(self) -> str:
        return f"[{self.type.value}, {self.lexeme}, {self.line}, {self.col}]"

    def __repr__(self) -> str:  # 便于单测失败时阅读
        return f"Token({self.type.value}, {self.lexeme!r}, line={self.line}, col={self.col})"
