"""聚合（COUNT/SUM/AVG/MIN/MAX）/ GROUP BY / HAVING / ORDER BY 测试。"""

import unittest

from src.compiler.compiler import SQLCompiler
from src.compiler.errors import SemanticError
from src.compiler.lexer import tokenize
from src.compiler.parser import parse
from src.compiler.parser.ast_nodes import ColumnRef, FunctionCall
from src.compiler.planner.plan_nodes import (AggregatePlan, FilterPlan, PlanNode,
                                             ProjectPlan, SortPlan)

PREFIX = "CREATE TABLE emp(id INT, name VARCHAR(20), age INT, dept VARCHAR(20), salary FLOAT);"


def parse_one(sql):
    return parse(tokenize(sql))[0]


def compile_last(sql):
    results, error = SQLCompiler().compile_safe(PREFIX + sql)
    return (results[-1] if results else None), error


class TestParserAggregate(unittest.TestCase):
    def test_count_star(self):
        stmt = parse_one("SELECT COUNT(*) FROM emp;")
        call = stmt.columns[0]
        self.assertIsInstance(call, FunctionCall)
        self.assertEqual(call.name, "COUNT")
        self.assertTrue(call.star)
        self.assertEqual(call.label(), "COUNT(*)")

    def test_sum_with_argument(self):
        stmt = parse_one("SELECT SUM(salary) FROM emp;")
        call = stmt.columns[0]
        self.assertEqual(call.name, "SUM")
        self.assertFalse(call.star)
        self.assertIsInstance(call.arg, ColumnRef)
        self.assertEqual(call.arg.name, "salary")

    def test_aggregate_alias(self):
        stmt = parse_one("SELECT COUNT(*) AS cnt FROM emp;")
        self.assertEqual(stmt.aliases, ["cnt"])

    def test_group_by_list(self):
        stmt = parse_one("SELECT dept, COUNT(*) FROM emp GROUP BY dept;")
        self.assertEqual(len(stmt.group_by), 1)
        self.assertEqual(stmt.group_by[0].name, "dept")

    def test_having_and_order_by(self):
        stmt = parse_one(
            "SELECT dept, COUNT(*) AS c FROM emp GROUP BY dept "
            "HAVING COUNT(*) > 1 ORDER BY c DESC, dept ASC;")
        self.assertIsNotNone(stmt.having)
        self.assertEqual(stmt.having.op, ">")
        self.assertEqual([o.desc for o in stmt.order_by], [True, False])

    def test_has_aggregate_flag(self):
        self.assertTrue(parse_one("SELECT COUNT(*) FROM emp;").has_aggregate)
        self.assertTrue(parse_one("SELECT dept FROM emp GROUP BY dept;").has_aggregate)
        self.assertTrue(parse_one("SELECT dept, COUNT(*) AS c FROM emp GROUP BY dept;")
                        .has_aggregate)
        self.assertFalse(parse_one("SELECT id FROM emp;").has_aggregate)


class TestSemanticAggregate(unittest.TestCase):
    def test_aggregate_ok(self):
        result, error = compile_last(
            "SELECT dept, COUNT(*), AVG(age) FROM emp GROUP BY dept;")
        self.assertIsNone(error)
        self.assertIn("含聚合", result.message)

    def test_global_aggregate_without_group_by(self):
        _, error = compile_last("SELECT COUNT(*), MAX(salary) FROM emp;")
        self.assertIsNone(error)

    def test_aggregate_in_where_rejected(self):
        _, error = compile_last("SELECT * FROM emp WHERE COUNT(*) > 1;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("不能出现在 WHERE", error.message)

    def test_aggregate_in_group_by_rejected(self):
        _, error = compile_last(
            "SELECT dept FROM emp GROUP BY COUNT(*);")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("GROUP BY", error.message)

    def test_nested_aggregate_rejected(self):
        _, error = compile_last("SELECT SUM(COUNT(*)) FROM emp;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("聚合函数", error.message)

    def test_unknown_function_rejected(self):
        _, error = compile_last("SELECT FOO(age) FROM emp;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("不支持的函数", error.message)

    def test_sum_on_string_rejected(self):
        _, error = compile_last("SELECT SUM(name) FROM emp;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("需要数值参数", error.message)

    def test_non_grouped_column_rejected(self):
        _, error = compile_last("SELECT dept, name, COUNT(*) FROM emp GROUP BY dept;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("必须出现在 GROUP BY", error.message)

    def test_bare_column_without_group_by_rejected(self):
        _, error = compile_last("SELECT dept, COUNT(*) FROM emp;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("必须出现在 GROUP BY", error.message)

    def test_having_must_be_boolean(self):
        _, error = compile_last(
            "SELECT dept FROM emp GROUP BY dept HAVING dept;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("逻辑表达式", error.message)

    def test_min_max_on_string_ok(self):
        _, error = compile_last("SELECT MIN(name), MAX(name) FROM emp;")
        self.assertIsNone(error)


class TestPlanAggregate(unittest.TestCase):
    def test_aggregate_plan_shape(self):
        result, error = compile_last(
            "SELECT dept, COUNT(*) AS cnt FROM emp GROUP BY dept;")
        self.assertIsNone(error)
        plan = result.plan
        self.assertIsInstance(plan, ProjectPlan)
        self.assertEqual(plan.columns, ["dept", "cnt"])
        aggregate = plan.child
        self.assertIsInstance(aggregate, AggregatePlan)
        self.assertEqual(aggregate.group_labels, ["dept"])
        self.assertEqual([a.label for a in aggregate.aggregates], ["COUNT(*)"])

    def test_projection_references_aggregate_labels(self):
        result, _ = compile_last(
            "SELECT dept, COUNT(*) AS cnt, AVG(age) AS avg_age "
            "FROM emp GROUP BY dept;")
        names = [expr.name for expr in result.plan.exprs]
        self.assertEqual(names, ["dept", "COUNT(*)", "AVG(age)"])

    def test_having_becomes_filter_above_aggregate(self):
        result, _ = compile_last(
            "SELECT dept FROM emp GROUP BY dept HAVING COUNT(*) > 1;")
        plan = result.plan
        self.assertIsInstance(plan.child, FilterPlan)
        self.assertIsInstance(plan.child.child, AggregatePlan)
        # HAVING 中的聚合被改写成对聚合结果列的引用
        self.assertEqual(str(plan.child.predicate), "(COUNT(*) > 1)")

    def test_order_by_aggregate(self):
        result, _ = compile_last(
            "SELECT dept, COUNT(*) AS c FROM emp GROUP BY dept ORDER BY c DESC;")
        sort = result.plan.child
        self.assertIsInstance(sort, SortPlan)
        self.assertTrue(sort.keys[0].desc)
        self.assertEqual(str(sort.keys[0].expr), "COUNT(*)")

    def test_order_by_position(self):
        result, _ = compile_last(
            "SELECT name, age FROM emp ORDER BY 2 DESC;")
        sort = result.plan.child
        self.assertIsInstance(sort, SortPlan)
        self.assertEqual(str(sort.keys[0].expr), "age")
        self.assertTrue(sort.keys[0].desc)

    def test_expression_projection(self):
        result, error = compile_last("SELECT name, age + 1 AS next_age FROM emp;")
        self.assertIsNone(error)
        self.assertEqual(result.plan.columns, ["name", "next_age"])
        self.assertIsNotNone(result.plan.exprs)

    def test_aggregate_sexpr(self):
        result, _ = compile_last("SELECT COUNT(*) FROM emp;")
        text = result.plan.to_sexpr()
        self.assertIn("(Aggregate [] [COUNT(*)]", text)

    def test_aggregate_plan_roundtrip(self):
        result, _ = compile_last(
            "SELECT dept, COUNT(*) AS c FROM emp GROUP BY dept "
            "HAVING COUNT(*) > 1 ORDER BY c DESC;")
        data = result.plan.to_dict()
        restored = PlanNode.from_dict(data)
        self.assertEqual(restored.to_dict(), data)
        self.assertEqual(restored.to_sexpr(), result.plan.to_sexpr())

    def test_sort_plan_roundtrip(self):
        result, _ = compile_last("SELECT name FROM emp ORDER BY age DESC, name ASC;")
        data = result.plan.to_dict()
        restored = PlanNode.from_dict(data)
        self.assertEqual(restored.to_dict(), data)


if __name__ == "__main__":
    unittest.main()
