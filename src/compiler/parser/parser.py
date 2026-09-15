"""语法分析器：递归下降，Token 流 -> AST。

语句层：CREATE TABLE / INSERT / SELECT / DELETE / EXPLAIN。
表达式层：按优先级分层解析
    or_expr → and_expr → not_expr → comparison
            → additive → multiplicative → unary → primary
支持 AND / OR / NOT、算术运算（+ - * /）、嵌套括号、聚合函数调用
（COUNT/SUM/AVG/MIN/MAX）与限定列名（`a.id`）。

SELECT 扩展语法：
    SELECT <* | 投影项 , ...>
    FROM <表> [AS 别名] { JOIN | LEFT JOIN | CROSS JOIN | , } <表> [ON 表达式]
    [WHERE 表达式]
    [GROUP BY 表达式 , ...]
    [HAVING 表达式]
    [ORDER BY <表达式> [ASC|DESC] , ...]
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from ..errors import SyntaxErr
from ..lexer.token import ConstType, Token, TokenType
from .ast_nodes import (COMPARISON_OPS, DEFAULT_VARCHAR_LENGTH, DATA_TYPES,
                        BinaryOp, ColumnDef, ColumnRef, CreateTable, Delete,
                        Explain, Expr, FunctionCall, Insert, Join, Literal,
                        OrderItem, Select, Statement, TableRef, UnaryOp)


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

    def _is_operator(self, text: str, offset: int = 0) -> bool:
        token = self._peek(offset)
        return token.type is TokenType.OPERATOR and token.lexeme == text

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
        if self._is_keyword("EXPLAIN"):
            return self._parse_explain()
        token = self._peek()
        raise SyntaxErr("期望 CREATE / INSERT / SELECT / DELETE / EXPLAIN，实际得到 "
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

        columns: Optional[List[str]] = None
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
        """INSERT 的值只允许常量或 NULL。"""
        token = self._peek()
        if token.type is TokenType.CONST:
            self._advance()
            return Literal(token.value, token.value_type, token.line, token.col)
        if token.type is TokenType.KEYWORD and token.lexeme.upper() == "NULL":
            self._advance()
            return Literal(None, ConstType.NULL, token.line, token.col)
        if token.type is TokenType.IDENTIFIER:
            raise SyntaxErr("INSERT 的值必须是常量或 NULL（不支持表达式）",
                            token.line, token.col)
        raise SyntaxErr(f"期望常量或 NULL，实际得到 {_describe(token)}", token.line, token.col)

    # ------------------------------------------------------------------
    # WHERE 表达式（按优先级分层）
    # ------------------------------------------------------------------
    def _parse_where(self) -> Expr:
        self._expect_keyword("WHERE")
        return self._parse_expr()

    def _parse_expr(self) -> Expr:
        """or_expr：优先级最低，最后结合。"""
        left = self._parse_and()
        while self._is_keyword("OR"):
            token = self._advance()
            right = self._parse_and()
            left = BinaryOp(token.lexeme.upper(), left, right, token.line, token.col)
        return left

    def _parse_and(self) -> Expr:
        left = self._parse_not()
        while self._is_keyword("AND"):
            token = self._advance()
            right = self._parse_not()
            left = BinaryOp(token.lexeme.upper(), left, right, token.line, token.col)
        return left

    def _parse_not(self) -> Expr:
        if self._is_keyword("NOT"):
            token = self._advance()
            return UnaryOp("NOT", self._parse_not(), token.line, token.col)
        return self._parse_comparison()

    def _parse_comparison(self) -> Expr:
        """比较运算：左结合、不可连写（a > b > c 属语法错误由上层发现）。"""
        left = self._parse_additive()
        token = self._peek()
        if token.type is TokenType.OPERATOR and token.lexeme in COMPARISON_OPS:
            self._advance()
            right = self._parse_additive()
            return BinaryOp(token.lexeme, left, right, token.line, token.col)
        return left

    def _parse_additive(self) -> Expr:
        """加减：左结合。"""
        left = self._parse_multiplicative()
        while self._is_operator("+") or self._is_operator("-"):
            token = self._advance()
            right = self._parse_multiplicative()
            left = BinaryOp(token.lexeme, left, right, token.line, token.col)
        return left

    def _parse_multiplicative(self) -> Expr:
        """乘除：优先级高于加减。"""
        left = self._parse_unary()
        while self._is_operator("*") or self._is_operator("/"):
            token = self._advance()
            right = self._parse_unary()
            left = BinaryOp(token.lexeme, left, right, token.line, token.col)
        return left

    def _parse_unary(self) -> Expr:
        """一元正负号：右结合，可叠加（--a）。"""
        token = self._peek()
        if token.type is TokenType.OPERATOR and token.lexeme in ("+", "-"):
            self._advance()
            return UnaryOp(token.lexeme, self._parse_unary(), token.line, token.col)
        return self._parse_primary()

    def _parse_primary(self) -> Expr:
        """最小单元：常量 / NULL / 列名（可限定）/ 聚合函数 / '(' 表达式 ')'。"""
        token = self._peek()
        if token.type is TokenType.CONST:
            self._advance()
            return Literal(token.value, token.value_type, token.line, token.col)
        if token.type is TokenType.KEYWORD and token.lexeme.upper() == "NULL":
            self._advance()
            return Literal(None, ConstType.NULL, token.line, token.col)
        if token.type is TokenType.IDENTIFIER:
            # 函数调用：IDENT '('
            if self._is_delimiter("(", 1):
                return self._parse_function_call()
            return self._parse_column_ref()
        if token.type is TokenType.DELIMITER and token.lexeme == "(":
            self._advance()
            expr = self._parse_expr()
            self._expect_delimiter(")")
            return expr
        raise SyntaxErr(f"期望列名、常量或 '('，实际得到 {_describe(token)}",
                        token.line, token.col)

    def _parse_column_ref(self) -> ColumnRef:
        """列引用，支持 `列名` 与 `表名.列名` 两种写法。"""
        first = self._expect_identifier("列名")
        if self._is_delimiter("."):
            self._advance()
            if self._is_operator("*"):
                token = self._peek()
                raise SyntaxErr("暂不支持 '表.*' 写法，请直接使用 *", token.line, token.col)
            second = self._expect_identifier("列名")
            return ColumnRef(second.lexeme, second.line, second.col, first.lexeme)
        return ColumnRef(first.lexeme, first.line, first.col)

    def _parse_function_call(self) -> FunctionCall:
        """函数调用：COUNT(*)、SUM(age) 等（函数名合法性由语义层校验）。"""
        name_token = self._expect_identifier("函数名")
        self._expect_delimiter("(")
        star = False
        arg: Optional[Expr] = None
        if self._is_operator("*"):
            self._advance()
            star = True
        else:
            arg = self._parse_expr()
        self._expect_delimiter(")")
        return FunctionCall(name_token.lexeme.upper(), arg, star,
                            name_token.line, name_token.col)

    # ------------------------------------------------------------------
    # FROM / JOIN / GROUP BY / HAVING / ORDER BY
    # ------------------------------------------------------------------
    def _parse_table_ref(self) -> TableRef:
        """表引用：表名 [AS] [别名]。"""
        token = self._expect_identifier("表名")
        alias: Optional[str] = None
        if self._is_keyword("AS"):
            self._advance()
            alias = self._expect_identifier("表别名").lexeme
        elif self._peek().type is TokenType.IDENTIFIER:
            alias = self._advance().lexeme
        return TableRef(token.lexeme, alias, token.line, token.col)

    def _parse_joins(self) -> List[Join]:
        """解析零个或多个连接子句。

        支持：`JOIN t ON e`、`INNER JOIN t ON e`、`LEFT [OUTER] JOIN t ON e`、
             `CROSS JOIN t`、以及逗号隐式连接 `FROM a, b`（等价 CROSS JOIN）。
        """
        joins: List[Join] = []
        while True:
            head = self._peek()
            if self._is_delimiter(","):
                self._advance()
                joins.append(Join(self._parse_table_ref(), "CROSS", None,
                                  head.line, head.col))
                continue

            join_type: Optional[str] = None
            if self._is_keyword("JOIN"):
                self._advance()
                join_type = "INNER"
            elif self._is_keyword("INNER") and self._is_keyword("JOIN", 1):
                self._advance()
                self._advance()
                join_type = "INNER"
            elif self._is_keyword("CROSS") and self._is_keyword("JOIN", 1):
                self._advance()
                self._advance()
                join_type = "CROSS"
            elif self._is_keyword("LEFT"):
                self._advance()
                if self._is_keyword("OUTER"):
                    self._advance()
                self._expect_keyword("JOIN")
                join_type = "LEFT"
            elif self._is_keyword("RIGHT") or self._is_keyword("FULL"):
                token = self._peek()
                raise SyntaxErr(
                    f"暂不支持 {token.lexeme.upper()} JOIN（当前支持 INNER / LEFT / CROSS）",
                    token.line, token.col)
            else:
                break

            table = self._parse_table_ref()
            on: Optional[Expr] = None
            if self._is_keyword("ON"):
                self._advance()
                on = self._parse_expr()
            elif join_type != "CROSS":
                token = self._peek()
                raise SyntaxErr(f"期望 ON，实际得到 {_describe(token)}",
                                token.line, token.col)
            joins.append(Join(table, join_type, on, head.line, head.col))
        return joins

    def _parse_group_by(self) -> List[Expr]:
        self._expect_keyword("GROUP")
        self._expect_keyword("BY")
        items = [self._parse_expr()]
        while self._is_delimiter(","):
            self._advance()
            items.append(self._parse_expr())
        return items

    def _parse_order_by(self) -> List[OrderItem]:
        self._expect_keyword("ORDER")
        self._expect_keyword("BY")
        items = [self._parse_order_item()]
        while self._is_delimiter(","):
            self._advance()
            items.append(self._parse_order_item())
        return items

    def _parse_order_item(self) -> OrderItem:
        expr = self._parse_expr()
        desc = False
        if self._is_keyword("ASC"):
            self._advance()
        elif self._is_keyword("DESC"):
            self._advance()
            desc = True
        return OrderItem(expr, desc, getattr(expr, "line", 0), getattr(expr, "col", 0))

    # ------------------------------------------------------------------
    # SELECT / DELETE / EXPLAIN
    # ------------------------------------------------------------------
    def _parse_select(self) -> Select:
        kw = self._expect_keyword("SELECT")

        star = False
        columns: List[Expr] = []
        aliases: List[Optional[str]] = []
        if self._is_operator("*"):
            self._advance()
            star = True
        else:
            expr, alias = self._parse_select_item()
            columns.append(expr)
            aliases.append(alias)
            while self._is_delimiter(","):
                self._advance()
                expr, alias = self._parse_select_item()
                columns.append(expr)
                aliases.append(alias)

        self._expect_keyword("FROM")
        from_table = self._parse_table_ref()
        joins = self._parse_joins()

        where = self._parse_where() if self._is_keyword("WHERE") else None
        group_by = self._parse_group_by() if self._is_keyword("GROUP") else []
        having = None
        if self._is_keyword("HAVING"):
            self._advance()
            having = self._parse_expr()
        order_by = self._parse_order_by() if self._is_keyword("ORDER") else []

        return Select(
            from_table.name, star, columns, where, kw.line, kw.col,
            from_alias=from_table.alias, joins=joins, group_by=group_by,
            having=having, order_by=order_by, aliases=aliases,
        )

    def _parse_select_item(self) -> Tuple[Expr, Optional[str]]:
        """一个投影项：表达式 [AS] [别名]。"""
        expr = self._parse_expr()
        alias: Optional[str] = None
        if self._is_keyword("AS"):
            self._advance()
            alias = self._expect_identifier("列别名").lexeme
        elif self._peek().type is TokenType.IDENTIFIER:
            alias = self._advance().lexeme
        return expr, alias

    def _parse_delete(self) -> Delete:
        kw = self._expect_keyword("DELETE")
        self._expect_keyword("FROM")
        table_token = self._expect_identifier("表名")
        where = self._parse_where() if self._is_keyword("WHERE") else None
        return Delete(table_token.lexeme, where, kw.line, kw.col)

    def _parse_explain(self) -> Explain:
        kw = self._expect_keyword("EXPLAIN")
        inner = self._parse_statement()
        if isinstance(inner, Explain):
            raise SyntaxErr("EXPLAIN 不支持嵌套", kw.line, kw.col)
        return Explain(inner, kw.line, kw.col)


def parse(tokens: List[Token]) -> List[Statement]:
    """语法分析入口：Token 列表 -> 语句列表。"""
    return Parser(tokens).parse()
