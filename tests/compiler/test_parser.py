"""语法分析器测试（P0 范围）。"""

import unittest

from src.compiler.errors import SyntaxErr
from src.compiler.lexer import tokenize
from src.compiler.parser import parse
from src.compiler.parser.ast_nodes import (BinaryOp, ColumnRef, CreateTable,
                                           Delete, Insert, Literal, Select)


def parse_one(sql):
    """解析单条语句并返回 AST 节点。"""
    return parse(tokenize(sql))[0]


class TestParserCreateTable(unittest.TestCase):
    def test_create_table_ast(self):
        stmt = parse_one("CREATE TABLE student(id INT, name VARCHAR(32), age INT);")
        self.assertIsInstance(stmt, CreateTable)
        self.assertEqual(stmt.table_name, "student")
        self.assertEqual([(c.name, c.type, c.length) for c in stmt.columns],
                         [("id", "INT", None), ("name", "VARCHAR", 32), ("age", "INT", None)])

    def test_varchar_default_length(self):
        stmt = parse_one("CREATE TABLE t(name VARCHAR);")
        self.assertEqual(stmt.columns[0].length, 32)

    def test_unsupported_column_type_raises(self):
        with self.assertRaises(SyntaxErr):
            parse_one("CREATE TABLE t(id INTEGER);")

    def test_unclosed_paren_raises(self):
        with self.assertRaises(SyntaxErr):
            parse_one("CREATE TABLE t(id INT;")


class TestParserInsert(unittest.TestCase):
    def test_insert_with_columns(self):
        stmt = parse_one("INSERT INTO student(id,name,age) VALUES (1,'Alice',20);")
        self.assertIsInstance(stmt, Insert)
        self.assertEqual(stmt.columns, ["id", "name", "age"])
        self.assertEqual(len(stmt.rows), 1)
        self.assertEqual([v.value for v in stmt.rows[0]], [1, "Alice", 20])

    def test_insert_multiple_rows_without_columns(self):
        stmt = parse_one("INSERT INTO student VALUES (1,'A',20), (2,'B',21);")
        self.assertIsNone(stmt.columns)
        self.assertEqual(len(stmt.rows), 2)

    def test_insert_null_value(self):
        stmt = parse_one("INSERT INTO student VALUES (1,NULL,20);")
        self.assertIsNone(stmt.rows[0][1].value)
        self.assertEqual(stmt.rows[0][1].value_type, "NULL")

    def test_insert_identifier_as_value_raises(self):
        # P0 不允许表达式作为值
        with self.assertRaises(SyntaxErr):
            parse_one("INSERT INTO student VALUES (id, 20);")


class TestParserSelectDelete(unittest.TestCase):
    def test_select_star(self):
        stmt = parse_one("SELECT * FROM student;")
        self.assertTrue(stmt.star)
        self.assertEqual(stmt.table_name, "student")

    def test_select_columns_with_where(self):
        stmt = parse_one("SELECT id,name FROM student WHERE age > 18;")
        self.assertEqual([c.name for c in stmt.columns], ["id", "name"])
        self.assertIsInstance(stmt.where, BinaryOp)
        self.assertEqual(stmt.where.op, ">")
        self.assertEqual(stmt.where.left.name, "age")
        self.assertEqual(stmt.where.right.value, 18)

    def test_delete_with_where(self):
        stmt = parse_one("DELETE FROM student WHERE id = 1;")
        self.assertIsInstance(stmt, Delete)
        self.assertEqual(stmt.where.op, "=")

    def test_delete_without_where(self):
        stmt = parse_one("DELETE FROM student;")
        self.assertIsNone(stmt.where)

    def test_comparison_operators(self):
        for op in ("=", "<>", "!=", "<", "<=", ">", ">="):
            stmt = parse_one(f"SELECT * FROM t WHERE a {op} 1;")
            self.assertEqual(stmt.where.op, op)


class TestParserErrors(unittest.TestCase):
    """错误定位：出错位置 + 期望符号。"""

    def test_missing_semicolon(self):
        with self.assertRaises(SyntaxErr) as ctx:
            parse_one("SELECT id FROM student")
        self.assertEqual(ctx.exception.error_type, "SyntaxError")
        self.assertIn("期望 ';'", ctx.exception.message)

    def test_missing_from(self):
        with self.assertRaises(SyntaxErr) as ctx:
            parse_one("SELECT id student;")
        self.assertIn("期望 FROM", ctx.exception.message)

    def test_unknown_statement(self):
        with self.assertRaises(SyntaxErr):
            parse_one("UPDATE student SET age = 1;")

    def test_and_is_rejected_in_p0(self):
        with self.assertRaises(SyntaxErr) as ctx:
            parse_one("SELECT * FROM t WHERE a > 1 AND b < 2;")
        self.assertIn("P0 不支持", ctx.exception.message)

    def test_or_is_rejected_in_p0(self):
        with self.assertRaises(SyntaxErr):
            parse_one("SELECT * FROM t WHERE a > 1 OR b < 2;")

    def test_arithmetic_is_rejected_in_p0(self):
        with self.assertRaises(SyntaxErr):
            parse_one("SELECT * FROM t WHERE a + 1 > 2;")

    def test_parenthesized_expr_rejected_in_p0(self):
        with self.assertRaises(SyntaxErr):
            parse_one("SELECT * FROM t WHERE (a > 1);")

    def test_error_has_position(self):
        with self.assertRaises(SyntaxErr) as ctx:
            parse_one("SELECT id student;")
        self.assertEqual(ctx.exception.line, 1)
        self.assertIsNotNone(ctx.exception.col)


class TestParserMultipleStatements(unittest.TestCase):
    def test_parse_multiple(self):
        sql = "CREATE TABLE t(id INT); INSERT INTO t VALUES (1); SELECT * FROM t;"
        statements = parse(tokenize(sql))
        self.assertEqual([type(s).__name__ for s in statements],
                         ["CreateTable", "Insert", "Select"])


if __name__ == "__main__":
    unittest.main()
