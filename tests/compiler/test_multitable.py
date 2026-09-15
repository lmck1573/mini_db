"""多表连接（JOIN / 逗号连接 / 限定列）的语法、语义与计划测试。"""

import unittest

from src.compiler.compiler import SQLCompiler
from src.compiler.errors import SemanticError, SyntaxErr
from src.compiler.lexer import tokenize
from src.compiler.parser import parse
from src.compiler.parser.ast_nodes import ColumnRef, Join, Select
from src.compiler.planner.plan_nodes import (FilterPlan, JoinPlan, PlanNode,
                                             ProjectPlan, SeqScanPlan)

PREFIX = (
    "CREATE TABLE student(id INT, name VARCHAR(20), age INT, dept VARCHAR(20));"
    "CREATE TABLE score(sid INT, course VARCHAR(20), score INT);"
    "CREATE TABLE teacher(id INT, name VARCHAR(20));"
)


def parse_one(sql):
    return parse(tokenize(sql))[0]


def compile_last(sql):
    """编译并返回 (最后一条语句结果, 错误)。"""
    results, error = SQLCompiler().compile_safe(PREFIX + sql)
    return (results[-1] if results else None), error


class TestParserJoin(unittest.TestCase):
    def test_qualified_column(self):
        stmt = parse_one("SELECT s.name FROM student s;")
        self.assertIsInstance(stmt, Select)
        ref = stmt.columns[0]
        self.assertIsInstance(ref, ColumnRef)
        self.assertEqual((ref.table, ref.name), ("s", "name"))

    def test_table_alias(self):
        stmt = parse_one("SELECT * FROM student AS s;")
        self.assertEqual(stmt.table_name, "student")
        self.assertEqual(stmt.from_alias, "s")

    def test_inner_join_with_on(self):
        stmt = parse_one(
            "SELECT * FROM student s JOIN score c ON s.id = c.sid;")
        self.assertEqual(len(stmt.joins), 1)
        join = stmt.joins[0]
        self.assertIsInstance(join, Join)
        self.assertEqual(join.join_type, "INNER")
        self.assertEqual(join.table.name, "score")
        self.assertEqual(join.table.alias, "c")
        self.assertIsNotNone(join.on)

    def test_inner_keyword_variant(self):
        stmt = parse_one("SELECT * FROM a INNER JOIN b ON a.id = b.aid;")
        self.assertEqual(stmt.joins[0].join_type, "INNER")

    def test_left_and_cross_join(self):
        stmt = parse_one("SELECT * FROM a LEFT JOIN b ON a.id = b.aid;")
        self.assertEqual(stmt.joins[0].join_type, "LEFT")
        stmt = parse_one("SELECT * FROM a CROSS JOIN b;")
        self.assertEqual(stmt.joins[0].join_type, "CROSS")
        self.assertIsNone(stmt.joins[0].on)

    def test_comma_join_is_cross(self):
        stmt = parse_one("SELECT * FROM a, b;")
        self.assertEqual(len(stmt.joins), 1)
        self.assertEqual(stmt.joins[0].join_type, "CROSS")

    def test_multi_join_chain(self):
        stmt = parse_one(
            "SELECT * FROM a JOIN b ON a.id = b.aid JOIN c ON b.id = c.bid;")
        self.assertEqual([j.table.name for j in stmt.joins], ["b", "c"])

    def test_sources_helper(self):
        stmt = parse_one("SELECT * FROM student s JOIN score c ON s.id = c.sid;")
        self.assertEqual(stmt.sources, ["s", "c"])
        self.assertTrue(stmt.is_multi_table)


class TestParserJoinErrors(unittest.TestCase):
    def test_join_without_on_raises(self):
        with self.assertRaises(SyntaxErr) as ctx:
            parse_one("SELECT * FROM a JOIN b;")
        self.assertIn("期望 ON", ctx.exception.message)

    def test_right_join_unsupported(self):
        with self.assertRaises(SyntaxErr) as ctx:
            parse_one("SELECT * FROM a RIGHT JOIN b ON a.id = b.aid;")
        self.assertIn("暂不支持 RIGHT JOIN", ctx.exception.message)

    def test_qualified_star_unsupported(self):
        with self.assertRaises(SyntaxErr):
            parse_one("SELECT a.* FROM a;")


class TestSemanticJoin(unittest.TestCase):
    def test_join_ok(self):
        result, error = compile_last(
            "SELECT s.name, c.score FROM student s JOIN score c ON s.id = c.sid;")
        self.assertIsNone(error)
        self.assertIn("多表查询", result.message)

    def test_unknown_alias_raises(self):
        _, error = compile_last("SELECT x.name FROM student s;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("不存在", error.message)

    def test_unknown_table_raises(self):
        _, error = compile_last("SELECT * FROM student s JOIN nosuch n ON s.id = n.id;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("表 'nosuch' 不存在", error.message)

    def test_ambiguous_column_raises(self):
        # name 同时存在于 student 与 teacher 两张表
        _, error = compile_last(
            "SELECT name FROM student s JOIN teacher t ON s.id = t.id;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("歧义", error.message)

    def test_qualified_resolves_ambiguity(self):
        result, error = compile_last(
            "SELECT t.name FROM student s JOIN teacher t ON s.id = t.id;")
        self.assertIsNone(error)
        self.assertEqual(result.plan.columns, ["t.name"])

    def test_unqualified_unique_column_ok(self):
        # course 只存在于 score 表，无需限定即可解析
        result, error = compile_last(
            "SELECT course FROM student s JOIN score c ON s.id = c.sid;")
        self.assertIsNone(error)

    def test_duplicate_source_name_raises(self):
        _, error = compile_last(
            "SELECT * FROM student s JOIN score s ON s.id = s.sid;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("重复", error.message)

    def test_on_must_be_boolean(self):
        _, error = compile_last("SELECT * FROM student s JOIN score c ON s.id;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("逻辑表达式", error.message)

    def test_on_must_reference_both_sides(self):
        _, error = compile_last(
            "SELECT * FROM student s JOIN score c ON s.age > 10;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("两侧", error.message)


class TestPlanJoin(unittest.TestCase):
    def test_join_plan_shape(self):
        result, error = compile_last(
            "SELECT s.name, c.score FROM student s JOIN score c ON s.id = c.sid "
            "WHERE c.score > 90;")
        self.assertIsNone(error)
        plan = result.plan
        self.assertIsInstance(plan, ProjectPlan)
        self.assertEqual(plan.columns, ["s.name", "c.score"])
        self.assertIsInstance(plan.child, FilterPlan)
        join = plan.child.child
        self.assertIsInstance(join, JoinPlan)
        self.assertEqual(join.join_type, "INNER")
        self.assertIsInstance(join.left, SeqScanPlan)
        self.assertEqual(join.left.source, "s")
        self.assertEqual(join.right.source, "c")
        # LEFT JOIN 补 NULL 需要知道右侧所有限定列
        self.assertEqual(join.right_columns, ["c.sid", "c.course", "c.score"])

    def test_left_join_right_columns(self):
        result, _ = compile_last(
            "SELECT * FROM student s LEFT JOIN score c ON s.id = c.sid;")
        join = result.plan.child
        self.assertEqual(join.join_type, "LEFT")
        self.assertEqual(join.right_columns, ["c.sid", "c.course", "c.score"])

    def test_cross_join_plan(self):
        result, _ = compile_last("SELECT * FROM student, score;")
        join = result.plan.child
        self.assertEqual(join.join_type, "CROSS")
        self.assertIsNone(join.on)

    def test_join_sexpr(self):
        result, _ = compile_last(
            "SELECT * FROM student s JOIN score c ON s.id = c.sid;")
        text = result.plan.to_sexpr()
        self.assertIn("(Join INNER (= s.id c.sid)", text)
        self.assertIn("(SeqScan student :s)", text)
        self.assertIn("(SeqScan score :c)", text)

    def test_join_roundtrip(self):
        result, _ = compile_last(
            "SELECT s.name FROM student s LEFT JOIN score c ON s.id = c.sid "
            "WHERE s.age > 18;")
        data = result.plan.to_dict()
        restored = PlanNode.from_dict(data)
        self.assertEqual(restored.to_dict(), data)
        self.assertEqual(restored.to_sexpr(), result.plan.to_sexpr())


if __name__ == "__main__":
    unittest.main()
