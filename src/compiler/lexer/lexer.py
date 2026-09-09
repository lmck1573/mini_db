"""词法分析器：把 SQL 字符流切分为有序 Token 序列。

职责边界（P0）：
- 只做切词与定位，不判断语句结构（"缺分号"属语法错误）；
- 遇到非法字符/未闭合字符串立即抛 LexicalError，不做容错跳过。
"""

from __future__ import annotations

from typing import List

from ..errors import LexicalError
from .keywords import is_keyword
from .token import ConstType, Token, TokenType

# 双字符运算符（必须优先于单字符匹配）
OPERATORS_2 = ("<=", ">=", "<>", "!=")
# 单字符运算符
OPERATORS_1 = "=<>+-*/"
# 分隔符
DELIMITERS = "(),;"


class Lexer:
    """SQL 词法分析器，逐字符扫描并记录行号/列号。"""

    def __init__(self, sql: str):
        self.sql = sql
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: List[Token] = []

    # ------------------------------------------------------------------
    # 基础读取辅助
    # ------------------------------------------------------------------
    def _at_end(self) -> bool:
        return self.pos >= len(self.sql)

    def _peek(self, offset: int = 0) -> str:
        """向前看 offset 个字符，越界返回空串。"""
        idx = self.pos + offset
        return self.sql[idx] if idx < len(self.sql) else ""

    def _advance(self) -> str:
        """消费一个字符并维护行号列号。"""
        ch = self.sql[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _make(self, type_: TokenType, lexeme: str, line: int, col: int,
              value_type: str | None = None, value=None) -> Token:
        token = Token(type_, lexeme, line, col, value_type, value)
        self.tokens.append(token)
        return token

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def tokenize(self) -> List[Token]:
        """扫描全文，返回 Token 列表（末尾含 EOF）。"""
        while True:
            self._skip_ignorable()
            if self._at_end():
                break

            start_line, start_col, start_pos = self.line, self.col, self.pos
            ch = self._peek()

            if ch.isalpha() or ch == "_":
                self._scan_word(start_line, start_col, start_pos)
            elif ch.isdigit():
                self._scan_number(start_line, start_col, start_pos, negative=False)
            elif ch == "'":
                self._scan_string(start_line, start_col, start_pos)
            elif ch == "-" and self._peek(1).isdigit() and self._prev_allows_negative():
                self._advance()  # 消费负号，折进数字常量
                self._scan_number(start_line, start_col, start_pos, negative=True)
            elif self.sql[self.pos:self.pos + 2] in OPERATORS_2:
                self._advance()
                self._advance()
                self._make(TokenType.OPERATOR, self.sql[start_pos:self.pos], start_line, start_col)
            elif ch in OPERATORS_1:
                self._advance()
                self._make(TokenType.OPERATOR, ch, start_line, start_col)
            elif ch in DELIMITERS:
                self._advance()
                self._make(TokenType.DELIMITER, ch, start_line, start_col)
            else:
                raise LexicalError(f"非法字符 '{ch}'", start_line, start_col)

        self._make(TokenType.EOF, "<EOF>", self.line, self.col)
        return self.tokens

    def _skip_ignorable(self) -> None:
        """跳过空白与注释（-- 行注释、/* */ 块注释）。"""
        while not self._at_end():
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
            elif self.sql.startswith("--", self.pos):
                while not self._at_end() and self._peek() != "\n":
                    self._advance()
            elif self.sql.startswith("/*", self.pos):
                start_line, start_col = self.line, self.col
                self._advance()
                self._advance()
                while not self._at_end() and not self.sql.startswith("*/", self.pos):
                    self._advance()
                if self._at_end():
                    raise LexicalError("未闭合的块注释", start_line, start_col)
                self._advance()
                self._advance()
            else:
                break

    # ------------------------------------------------------------------
    # 各类单词扫描
    # ------------------------------------------------------------------
    def _scan_word(self, line: int, col: int, start_pos: int) -> None:
        """扫描标识符或关键字。"""
        while not self._at_end() and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        word = self.sql[start_pos:self.pos]
        if is_keyword(word):
            # 词素值保留源码原样，parser 比较时统一转大写
            self._make(TokenType.KEYWORD, word, line, col)
        else:
            self._make(TokenType.IDENTIFIER, word, line, col)

    def _scan_number(self, line: int, col: int, start_pos: int, negative: bool) -> None:
        """扫描整数或浮点常量，negative=True 时结果取负。"""
        while not self._at_end() and self._peek().isdigit():
            self._advance()
        is_float = False
        if self._peek() == "." and self._peek(1).isdigit():
            is_float = True
            self._advance()  # 小数点
            while not self._at_end() and self._peek().isdigit():
                self._advance()
        lexeme = self.sql[start_pos:self.pos]
        if is_float:
            value: float | int = float(lexeme)
            value_type = ConstType.FLOAT
        else:
            value = int(lexeme)
            value_type = ConstType.INT
        self._make(TokenType.CONST, lexeme, line, col, value_type, value)

    def _scan_string(self, line: int, col: int, start_pos: int) -> None:
        """扫描单引号字符串常量，支持 '' 转义。"""
        self._advance()  # 开引号
        buf: List[str] = []
        while True:
            if self._at_end():
                raise LexicalError("未闭合的字符串常量", line, col)
            ch = self._peek()
            if ch == "'":
                if self._peek(1) == "'":  # '' 表示一个字面单引号
                    buf.append("'")
                    self._advance()
                    self._advance()
                    continue
                self._advance()  # 闭引号
                break
            if ch == "\n":
                raise LexicalError("未闭合的字符串常量（不允许跨行）", line, col)
            buf.append(self._advance())
        self._make(TokenType.CONST, self.sql[start_pos:self.pos], line, col,
                   ConstType.STRING, "".join(buf))

    def _prev_allows_negative(self) -> bool:
        """前一个 Token 后是否可能出现负号（用于区分负数字面量与减号）。"""
        if not self.tokens:
            return True
        prev = self.tokens[-1]
        if prev.type in (TokenType.CONST, TokenType.IDENTIFIER):
            return False
        if prev.type == TokenType.DELIMITER and prev.lexeme == ")":
            return False
        return True


def tokenize(sql: str) -> List[Token]:
    """词法分析入口：SQL 文本 -> Token 列表。"""
    return Lexer(sql).tokenize()
