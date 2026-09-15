"""EXPLAIN 与执行计划可视化（HTML/SVG）测试。"""

import os
import tempfile
import unittest

from src.compiler.compiler import SQLCompiler
from src.compiler.errors import SyntaxErr
from src.compiler.lexer import tokenize
from src.compiler.parser import parse
from src.compiler.parser.ast_nodes import Explain
from src.compiler.planner.plan_nodes import (CreateTablePlan, JoinPlan,
                                             ProjectPlan, SeqScanPlan)
from src.compiler.planner.visualize import plan_to_html, plan_to_svg

PREFIX = (
    "CREATE TABLE student(id INT, name VARCHAR(20), age INT, dept VARCHAR(20));"
    "CREATE TABLE score(sid INT, course VARCHAR(20), score INT);"
)


def parse_one(sql):
    return parse(tokenize(sql))[0]


def compile_last(sql):
    results, error = SQLCompiler().compile_safe(PREFIX + sql)
    return (results[-1] if results else None), error


class TestExplain(unittest.TestCase):
    def test_parse_explain(self):
        stmt = parse_one("EXPLAIN SELECT * FROM t;")
        self.assertIsInstance(stmt, Explain)
        self.assertEqual(type(stmt.statement).__name__, "Select")

    def test_explain_nested_rejected(self):
        with self.assertRaises(SyntaxErr) as ctx:
            parse_one("EXPLAIN EXPLAIN SELECT * FROM t;")
        self.assertIn("不支持嵌套", ctx.exception.message)

    def test_explain_marks_result(self):
        result, error = compile_last("EXPLAIN SELECT id FROM student WHERE age > 18;")
        self.assertIsNone(error)
        self.assertTrue(result.explain)
        # 计划**不放进** plan 字段，执行引擎看到 None 会自动跳过
        self.assertIsNone(result.plan)
        self.assertIsNotNone(result.explain_plan)
        self.assertIs(result.display_plan, result.explain_plan)
        self.assertIn("EXPLAIN", result.message)

    def test_explain_does_not_mutate_catalog(self):
        # EXPLAIN CREATE TABLE 只做干跑，不应真的建表
        compiler = SQLCompiler()
        compiler.compile("CREATE TABLE t(id INT);")
        results, error = compiler.compile_safe("EXPLAIN CREATE TABLE t2(id INT);")
        self.assertIsNone(error)
        self.assertTrue(results[0].explain)
        self.assertFalse(compiler.catalog.has_table("t2"))

    def test_explain_plan_shows_join(self):
        result, _ = compile_last(
            "EXPLAIN SELECT s.name FROM student s JOIN score c ON s.id = c.sid "
            "WHERE c.score > 60;")
        plan = result.explain_plan
        self.assertIsInstance(plan, ProjectPlan)
        self.assertIsInstance(plan.child.child, JoinPlan)

    def test_normal_statement_keeps_plan(self):
        result, _ = compile_last("SELECT id FROM student;")
        self.assertFalse(result.explain)
        self.assertIsNotNone(result.plan)
        self.assertIsNone(result.explain_plan)
        self.assertIs(result.display_plan, result.plan)


class TestVisualize(unittest.TestCase):
    def test_svg_contains_operators(self):
        result, _ = compile_last(
            "SELECT s.name, c.score FROM student s JOIN score c ON s.id = c.sid "
            "WHERE c.score > 90 ORDER BY c.score DESC;")
        svg = plan_to_svg(result.plan)
        self.assertTrue(svg.startswith("<svg"))
        for name in ("Project", "Sort", "Filter", "Join INNER", "SeqScan"):
            self.assertIn(name, svg)

    def test_svg_contains_aggregate(self):
        result, _ = compile_last(
            "SELECT dept, COUNT(*) AS c FROM student GROUP BY dept HAVING COUNT(*) > 1;")
        svg = plan_to_svg(result.plan)
        self.assertIn("Aggregate", svg)
        self.assertIn("COUNT(*)", svg)

    def test_html_is_self_contained(self):
        result, _ = compile_last("SELECT id FROM student;")
        html = plan_to_html(result.plan, title="测试计划", sql="SELECT id FROM student;")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<svg", html)
        self.assertIn("</html>", html)
        # 不依赖任何外部资源
        self.assertNotIn("<script", html)
        self.assertNotIn("<link", html)
        self.assertNotIn("@import", html)
        self.assertNotIn("url(http", html)

    def test_html_escapes_unsafe_text(self):
        result, _ = compile_last("SELECT * FROM student WHERE name > 'a<b&c';")
        html = plan_to_html(result.plan, sql="SELECT '<script>'")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_plan_to_html_method(self):
        result, _ = compile_last("SELECT id FROM student;")
        self.assertIn("<svg", result.plan.to_html())

    def test_write_file(self):
        result, _ = compile_last("SELECT id FROM student;")
        path = os.path.join(tempfile.gettempdir(), "mini_db_plan_test.html")
        try:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(result.plan.to_html(title="写文件测试"))
            self.assertTrue(os.path.getsize(path) > 500)
            with open(path, encoding="utf-8") as fp:
                self.assertIn("<svg", fp.read())
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_single_node_plan(self):
        compiler = SQLCompiler()
        compiler.compile(PREFIX)
        from src.compiler.planner.planner import build_plan
        plan = build_plan(
            parse_one("CREATE TABLE solo(id INT);"), compiler.catalog)
        svg = plan_to_svg(plan)
        self.assertIn("CreateTable", svg)


if __name__ == "__main__":
    unittest.main()
