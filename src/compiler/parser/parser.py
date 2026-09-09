"""语法分析器：递归下降，Token 流 -> AST。

P0 范围：CREATE TABLE / INSERT / SELECT / DELETE，WHERE 仅支持单条件比较。
遇到 AND/OR/NOT、算术运算、括号表达式等 P1 特性时，按语法错误报出。
"""

from __future__ import annotations

from typing import List, Tuple

from ..errors import SyntaxErr
from ..lexer.token import ConstType, Token, TokenType
from .ast_nodes import (DEFAULT_VARCHAR_LENGTH, DATA_TYPES, BinaryOp, ColumnDef,
                        ColumnRef, CreateTable, Delete, Insert, Literal, Select,
                        Statement)

# P0 支持的比较运算符
COMPARISON_OPS = ("=", "<>", "!=", "<", "<=", ">", ">=")

# P1 保留字：词法层能识别，语法层明确不支持
UNSUPPORTED_LOGICAL = ("AND", "OR", "NOT")


def _describe(token: Token) -> str:
    """把 Token 描述成可读形式，用于错误信息。"""
    return f"{token.type.value}({token.lexeme})"


class Parser:
    """递归下降语法分析器。"""

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        # 每条语句占用的 Token 区间 [start, end)，用于分语句展示 Token 流
        self.token_ranges: List[Tuple[int, int]] = []

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    def _peek(self, offset: int = 0) -> Token:
        idx = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[idx]

    def _advance(self) -> Token:
        token = self._peek()
        if token.type is not TokenType.EOF:
            self.pos += 1
        return token

    def _at_end(self) -> bool:
        return self._peek().type is TokenType.EOF

    def _is_keyword(self, keyword: str, offset: int = 0) -> bool:
        token = self._peek(offset)
        return token.type is TokenType.KEYWORD and token.lexeme.upper() == keyword

    def _is_delimiter(self, text: str, offset: int = 0) -> bool:
        token = self._peek(offset)
        return token.type is TokenType.DELIMITER and token.lexeme == text

    def _expect_keyword(self, keyword: str) -> Token:
        if not self._is_keyword(keyword):
            token = self._peek()
            raise SyntaxErr(f"期望 {keyword}，实际得到 {_describe(token)}", token.line, token.col)
        return self._advance()

    def _expect_delimiter(self, text: str) -> Token:
        if not self._is_delimiter(text):
            token = self._peek()
            raise SyntaxErr(f"期望 '{text}'，实际得到 {_describe(token)}", token.line, token.col)
        return self._advance()

    def _expect_identifier(self, what: str) -> Token:
        token = self._peek()
        if token.type is not TokenType.IDENTIFIER:
            raise SyntaxErr(f"期望{what}，实际得到 {_describe(token)}", token.line, token.col)
        return self._advance()

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def parse(self) -> List[Statement]:
        """解析全部语句（以 ';' 分隔）。"""
        statements: List[Statement] = []
        while not self._at_end():
            start = self.pos
            statements.append(self._parse_statement())
            self._expect_delimiter(";")
            self.token_ranges.append((start, self.pos))
        return statements

    def _parse_statement(self) -> Statement:
        if self._is_keyword("CREATE"):
            return self._parse_create_table()
        if self._is_keyword("INSERT"):
            return self._parse_insert()
        if self._is_keyword("SELECT"):
            return self._parse_select()
        if self._is_keyword("DELETE"):
            return self._parse_delete()
        token = self._peek()
        raise SyntaxErr("期望 CREATE / INSERT / SELECT / DELETE，实际得到 "
                        f"{_describe(token)}", token.line, token.col)

    # ------------------------------------------------------------------
    # CREATE TABLE
    # ------------------------------------------------------------------
    def _parse_create_table(self) -> CreateTable:
        kw = self._expect_keyword("CREATE")
        self._expect_keyword("TABLE")
        name_token = self._expect_identifier("表名")
        self._expect_delimiter("(")

        columns: List[ColumnDef] = [self._parse_column_def()]
        while self._is_delimiter(","):
            self._advance()
            columns.append(self._parse_column_def())

        self._expect_delimiter(")")
        return CreateTable(name_token.lexeme, columns, kw.line, kw.col)

    def _parse_column_def(self) -> ColumnDef:
        name_token = self._expect_identifier("列名")
        type_token = self._peek()
        if type_token.type is not TokenType.KEYWORD or type_token.lexeme.upper() not in DATA_TYPES:
            raise SyntaxErr(f"期望列类型（{' / '.join(DATA_TYPES)}），实际得到 "
                            f"{_describe(type_token)}", type_token.line, type_token.col)
        self._advance()
        col_type = type_token.lexeme.upper()

        length = None
        if col_type == "VARCHAR" and self._is_delimiter("("):
            self._advance()
            num = self._peek()
            if num.type is not TokenType.CONST or num.value_type != ConstType.INT:
                raise SyntaxErr(f"期望 VARCHAR 长度（正整数），实际得到 {_describe(num)}",
                                num.line, num.col)
            self._advance()
            length = num.value
            self._expect_delimiter(")")
        elif col_type == "VARCHAR":
            length = DEFAULT_VARCHAR_LENGTH

        return ColumnDef(name_token.lexeme, col_type, length, name_token.line, name_token.col)

    # ------------------------------------------------------------------
    # INSERT
    # ------------------------------------------------------------------
    def _parse_insert(self) -> Insert:
        kw = self._expect_keyword("INSERT")
        self._expect_keyword("INTO")
        table_token = self._expect_identifier("表名")

        columns: List[str] | None = None
        if self._is_delimiter("("):
            self._advance()
            columns = [self._expect_identifier("列名").lexeme]
            while self._is_delimiter(","):
                self._advance()
                columns.append(self._expect_identifier("列名").lexeme)
            self._expect_delimiter(")")

        self._expect_keyword("VALUES")
        rows: List[List[Literal]] = [self._parse_value_tuple()]
        while self._is_delimiter(","):
            self._advance()
            rows.append(self._parse_value_tuple())

        return Insert(table_token.lexeme, columns, rows, kw.line, kw.col)

    def _parse_value_tuple(self) -> List[Literal]:
        self._expect_delimiter("(")
        values: List[Literal] = [self._parse_literal()]
        while self._is_delimiter(","):
            self._advance()
            values.append(self._parse_literal())
        self._expect_delimiter(")")
        return values

    def _parse_literal(self) -> Literal:
        """P0 的值只允许常量或 NULL。"""
        token = self._peek()
        if token.type is TokenType.CONST:
            self._advance()
            return Literal(token.value, token.value_type, token.line, token.col)
        if token.type is TokenType.KEYWORD and token.lexeme.upper() == "NULL":
            self._advance()
            return Literal(None, ConstType.NULL, token.line, token.col)
        if token.type is TokenType.IDENTIFIER:
            raise SyntaxErr("INSERT 的值必须是常量或 NULL（P0 不支持表达式）",
                            token.line, token.col)
        raise SyntaxErr(f"期望常量或 NULL，实际得到 {_describe(token)}", token.line, token.col)

    # ------------------------------------------------------------------
    # SELECT / DELETE 公共部分
    # ------------------------------------------------------------------
    def _parse_where(self) -> BinaryOp:
        self._expect_keyword("WHERE")
        condition = self._parse_condition()
        self._reject_logical_continuation()
        return condition

    def _parse_condition(self) -> BinaryOp:
        left = self._parse_operand()
        token = self._peek()
        if token.type is not TokenType.OPERATOR or token.lexeme not in COMPARISON_OPS:
            raise SyntaxErr(f"期望比较运算符（{' '.join(COMPARISON_OPS)}），实际得到 "
                            f"{_describe(token)}", token.line, token.col)
        self._advance()
        right = self._parse_operand()
        return BinaryOp(token.lexeme, left, right, left.line, left.col)

    def _parse_operand(self):
        token = self._peek()
        if token.type is TokenType.IDENTIFIER:
            self._advance()
            return ColumnRef(token.lexeme, token.line, token.col)
        if token.type is TokenType.CONST:
            self._advance()
            return Literal(token.value, token.value_type, token.line, token.col)
        if token.type is TokenType.KEYWORD and token.lexeme.upper() == "NULL":
            self._advance()
            return Literal(None, ConstType.NULL, token.line, token.col)
        if token.type is TokenType.KEYWORD and token.lexeme.upper() in UNSUPPORTED_LOGICAL:
            raise SyntaxErr(f"P0 不支持 {token.lexeme.upper()} 逻辑组合（进阶特性）",
                            token.line, token.col)
        if token.type is TokenType.DELIMITER and token.lexeme == "(":
            raise SyntaxErr("P0 不支持括号表达式（进阶特性）", token.line, token.col)
        raise SyntaxErr(f"期望列名或常量，实际得到 {_describe(token)}", token.line, token.col)

    def _reject_logical_continuation(self) -> None:
        """条件之后若紧跟 AND/OR，给出明确的不支持提示。"""
        if any(self._is_keyword(kw) for kw in UNSUPPORTED_LOGICAL):
            token = self._peek()
            raise SyntaxErr(f"P0 不支持 {token.lexeme.upper()} 逻辑组合（进阶特性）",
                            token.line, token.col)

    # ------------------------------------------------------------------
    # SELECT / DELETE
    # ------------------------------------------------------------------
    def _parse_select(self) -> Select:
        kw = self._expect_keyword("SELECT")

        star = False
        columns: List[ColumnRef] = []
        if self._peek().type is TokenType.OPERATOR and self._peek().lexeme == "*":
            self._advance()
            star = True
        else:
            token = self._expect_identifier("列名")
            columns.append(ColumnRef(token.lexeme, token.line, token.col))
            while self._is_delimiter(","):
                self._advance()
                token = self._expect_identifier("列名")
                columns.append(ColumnRef(token.lexeme, token.line, token.col))

        self._expect_keyword("FROM")
        table_token = self._expect_identifier("表名")

        where = self._parse_where() if self._is_keyword("WHERE") else None
        return Select(table_token.lexeme, star, columns, where, kw.line, kw.col)

    def _parse_delete(self) -> Delete:
        kw = self._expect_keyword("DELETE")
        self._expect_keyword("FROM")
        table_token = self._expect_identifier("表名")
        where = self._parse_where() if self._is_keyword("WHERE") else None
        return Delete(table_token.lexeme, where, kw.line, kw.col)


def parse(tokens: List[Token]) -> List[Statement]:
    """语法分析入口：Token 列表 -> 语句列表。"""
    return Parser(tokens).parse()
