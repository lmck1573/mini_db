"""词法分析器测试（P0 必做范围内）。"""

import unittest

from src.compiler.errors import LexicalError
from src.compiler.lexer import tokenize
from src.compiler.lexer.token import ConstType, TokenType


def tuples(sql):
    """把 SQL 切分为四元式 [种别码, 词素值, 行号, 列号]。"""
    return [t.to_tuple() for t in tokenize(sql)]


class TestLexerBasic(unittest.TestCase):
    """正常语句的切分与四元式输出。"""

    def test_create_table_tokens(self):
        sql = "CREATE TABLE student(id INT);"
        got = [(t[0], t[1]) for t in tuples(sql)]
        expect = [
            ("KEYWORD", "CREATE"),
            ("KEYWORD", "TABLE"),
            ("IDENTIFIER", "student"),
            ("DELIMITER", "("),
            ("IDENTIFIER", "id"),
            ("KEYWORD", "INT"),
            ("DELIMITER", ")"),
            ("DELIMITER", ";"),
            ("EOF", "<EOF>"),
        ]
        self.assertEqual(got, expect)

    def test_token_is_four_tuple_with_position(self):
        sql = "SELECT id"
        got = tuples(sql)
        self.assertEqual(got[0], ("KEYWORD", "SELECT", 1, 1))
        self.assertEqual(got[1], ("IDENTIFIER", "id", 1, 8))

    def test_keyword_case_insensitive(self):
        # 关键字大小写不敏感，词素值保留源码原样
        self.assertEqual(tuples("select")[0][:2], ("KEYWORD", "select"))
        self.assertEqual(tuples("Select")[0][:2], ("KEYWORD", "Select"))

    def test_identifier_with_underscore_and_digits(self):
        self.assertEqual(tuples("stu_2")[0][:2], ("IDENTIFIER", "stu_2"))

    def test_position_tracks_line_and_col(self):
        sql = "SELECT\n  age\nFROM t;"
        got = {t[1]: (t[2], t[3]) for t in tuples(sql)}
        self.assertEqual(got["SELECT"], (1, 1))
        self.assertEqual(got["age"], (2, 3))
        self.assertEqual(got["FROM"], (3, 1))


class TestLexerConstants(unittest.TestCase):
    """常量识别。"""

    def test_int_const_value(self):
        token = tokenize("20")[0]
        self.assertEqual(token.type, TokenType.CONST)
        self.assertEqual(token.value, 20)
        self.assertEqual(token.value_type, ConstType.INT)

    def test_float_const_value(self):
        token = tokenize("3.14")[0]
        self.assertEqual(token.value, 3.14)
        self.assertEqual(token.value_type, ConstType.FLOAT)

    def test_string_const_value(self):
        token = tokenize("'Alice'")[0]
        self.assertEqual(token.value, "Alice")
        self.assertEqual(token.value_type, ConstType.STRING)

    def test_string_escape_double_quote(self):
        # 'it''s' 表示一个字面单引号
        token = tokenize("'it''s'")[0]
        self.assertEqual(token.value, "it's")
        self.assertEqual(token.lexeme, "'it''s'")

    def test_negative_number_folded_into_const(self):
        token = tokenize("-5")[0]
        self.assertEqual(token.type, TokenType.CONST)
        self.assertEqual(token.value, -5)
        self.assertEqual(token.lexeme, "-5")

    def test_minus_after_identifier_is_operator(self):
        # 算术属 P1，词法层仍如实产出运算符，由 parser 报语法错误
        self.assertEqual(tuples("a-1")[1][:2], ("OPERATOR", "-"))

    def test_null_is_keyword(self):
        self.assertEqual(tuples("NULL")[0][:2], ("KEYWORD", "NULL"))


class TestLexerOperators(unittest.TestCase):
    """运算符与分隔符。"""

    def test_two_char_operators(self):
        for op in ("<=", ">=", "<>", "!="):
            self.assertEqual(tuples(op)[0][:2], ("OPERATOR", op))

    def test_single_char_operators(self):
        for op in ("=", "<", ">", "+"):
            self.assertEqual(tuples(op)[0][:2], ("OPERATOR", op))

    def test_delimiters(self):
        got = [t[1] for t in tuples("(),;")]
        self.assertEqual(got, ["(", ")", ",", ";", "<EOF>"])


class TestLexerIgnorable(unittest.TestCase):
    """空白与注释。"""

    def test_line_comment_skipped(self):
        got = [t[1] for t in tuples("SELECT -- 注释\nid")]
        self.assertEqual(got, ["SELECT", "id", "<EOF>"])

    def test_block_comment_skipped(self):
        got = [t[1] for t in tuples("SELECT /* 注释 */ id")]
        self.assertEqual(got, ["SELECT", "id", "<EOF>"])

    def test_unclosed_block_comment_raises(self):
        with self.assertRaises(LexicalError):
            tokenize("SELECT /* 未闭合")


class TestLexerErrors(unittest.TestCase):
    """错误定位（P0 要求：错误类型 + 位置）。"""

    def test_illegal_char_raises_with_position(self):
        with self.assertRaises(LexicalError) as ctx:
            tokenize("SELECT @")
        err = ctx.exception
        self.assertEqual(err.line, 1)
        self.assertEqual(err.col, 8)
        self.assertIn("非法字符", err.message)

    def test_unterminated_string_raises(self):
        with self.assertRaises(LexicalError) as ctx:
            tokenize("'Alice")
        self.assertEqual(ctx.exception.line, 1)
        self.assertEqual(ctx.exception.col, 1)

    def test_string_cannot_span_lines(self):
        with self.assertRaises(LexicalError):
            tokenize("'Ali\nce'")

    def test_error_triple_format(self):
        err = LexicalError("非法字符 '@'", 3, 12)
        self.assertEqual(str(err), "[LexicalError, Line 3 Col 12, 非法字符 '@']")


class TestLexerStatements(unittest.TestCase):
    """课程给定的四类示例语句能完整切分。"""

    def test_four_statements(self):
        sql = (
            "CREATE TABLE student(id INT, name VARCHAR, age INT);\n"
            "INSERT INTO student(id,name,age) VALUES (1,'Alice',20);\n"
            "SELECT id,name FROM student WHERE age > 18;\n"
            "DELETE FROM student WHERE id = 1;\n"
        )
        tokens = tokenize(sql)
        self.assertEqual(tokens[-1].type, TokenType.EOF)
        # 4 个分号
        self.assertEqual(sum(1 for t in tokens if t.lexeme == ";"), 4)
        # 关键元素都在
        lexemes = [t.lexeme for t in tokens]
        for key in ("CREATE", "student", "'Alice'", "WHERE", "DELETE"):
            self.assertIn(key, lexemes)

    def test_empty_input_only_eof(self):
        tokens = tokenize("")
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].type, TokenType.EOF)


if __name__ == "__main__":
    unittest.main()
