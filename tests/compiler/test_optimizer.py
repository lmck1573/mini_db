"""执行计划优化器测试。

覆盖 optimizer.py 的 4 条规则：常量折叠、恒真/恒假简化、NOT 消除、Filter 消除，
并验证优化后的计划仍满足 Project -> Filter -> SeqScan 的形状与 JSON 往返能力。
"""

import unittest

from src.compiler.compiler import SQLCompiler
from src.compiler.planner.plan_nodes import PlanNode


def build(sql):
    """建一张测试表，再编译给定查询，返回该查询的编译结果。"""
    compiler = SQLCompiler()
    compiler.compile("CREATE TABLE t(id INT, age INT, name VARCHAR(20));")
    results, err = compiler.compile_safe(sql)
    if err is not None:
        raise err
    return results[0]


class TestConstantFolding(unittest.TestCase):
    """规则 1：常量折叠。"""

    def test_arithmetic_in_predicate(self):
        r = build("SELECT * FROM t WHERE age > 1 + 2;")
        self.assertEqual(r.raw_plan.to_sexpr(),
                         "(Project [*] (Filter (> age (+ 1 2)) (SeqScan t)))")
        self.assertEqual(r.plan.to_sexpr(),
                         "(Project [*] (Filter (> age 3) (SeqScan t)))")
        self.assertTrue(any("constant_folding" in a for a in r.optimizations))

    def test_multiplication_folded_first(self):
        """乘除优先折叠：age + 1 * 2 > 40  =>  age + 2 > 40。"""
        r = build("SELECT * FROM t WHERE age + 1 * 2 > 40;")
        self.assertEqual(r.plan.to_sexpr(),
                         "(Project [*] (Filter (> (+ age 2) 40) (SeqScan t)))")

    def test_constant_comparison_folded(self):
        r = build("SELECT * FROM t WHERE 1 + 1 = 2 AND age > 1;")
        self.assertTrue(any("constant_folding" in a for a in r.optimizations))


class TestConstantCondition(unittest.TestCase):
    """规则 2：恒真 / 恒假条件简化。"""

    def test_true_and_x_keeps_x(self):
        r = build("SELECT * FROM t WHERE 1 = 1 AND age > 18;")
        self.assertEqual(r.plan.to_sexpr(),
                         "(Project [*] (Filter (> age 18) (SeqScan t)))")

    def test_false_or_x_keeps_x(self):
        r = build("SELECT * FROM t WHERE 1 = 0 OR age > 18;")
        self.assertEqual(r.plan.to_sexpr(),
                         "(Project [*] (Filter (> age 18) (SeqScan t)))")

    def test_false_and_x_becomes_false(self):
        r = build("SELECT * FROM t WHERE 1 = 0 AND age > 18;")
        self.assertIn("FALSE", r.plan.to_sexpr())

    def test_true_or_x_becomes_true(self):
        r = build("SELECT * FROM t WHERE age > 18 OR 1 = 1;")
        self.assertEqual(r.plan.to_sexpr(), "(Project [*] (SeqScan t))")


class TestFilterRemoval(unittest.TestCase):
    """规则 4：恒真谓词导致 Filter 算子整体消除。"""

    def test_always_true_filter_removed(self):
        r = build("SELECT * FROM t WHERE 1 = 1;")
        self.assertEqual(r.raw_plan.to_sexpr(),
                         "(Project [*] (Filter (= 1 1) (SeqScan t)))")
        self.assertEqual(r.plan.to_sexpr(), "(Project [*] (SeqScan t))")
        self.assertTrue(any("filter_removal" in a for a in r.optimizations))


class TestNotElimination(unittest.TestCase):
    """规则 3：NOT 消除（比较取反 / 双重否定 / 德摩根律）。"""

    def test_comparison_negation(self):
        r = build("SELECT * FROM t WHERE NOT (age > 18);")
        self.assertEqual(r.plan.to_sexpr(),
                         "(Project [*] (Filter (<= age 18) (SeqScan t)))")

    def test_equality_negation(self):
        r = build("SELECT * FROM t WHERE NOT (id = 1);")
        self.assertEqual(r.plan.to_sexpr(),
                         "(Project [*] (Filter (<> id 1) (SeqScan t)))")

    def test_double_negation(self):
        r = build("SELECT * FROM t WHERE NOT NOT (age = 1);")
        self.assertEqual(r.plan.to_sexpr(),
                         "(Project [*] (Filter (= age 1) (SeqScan t)))")

    def test_demorgan(self):
        r = build("SELECT * FROM t WHERE NOT (age > 18 AND id = 1);")
        self.assertEqual(
            r.plan.to_sexpr(),
            "(Project [*] (Filter (OR (<= age 18) (<> id 1)) (SeqScan t)))")


class TestPlanIntegrity(unittest.TestCase):
    """优化不改变计划的整体形状与可序列化性。"""

    def test_shape_preserved(self):
        for sql in ("SELECT * FROM t WHERE age > 18;",
                    "SELECT * FROM t WHERE age > 1 + 2 AND name <> 'x';",
                    "SELECT * FROM t WHERE NOT (age = 1);"):
            r = build(sql)
            self.assertEqual(r.plan.op, "Project")
            self.assertEqual(r.plan.child.op, "Filter")
            self.assertEqual(r.plan.child.child.op, "SeqScan")

    def test_plain_condition_triggers_no_rule(self):
        r = build("SELECT * FROM t WHERE age > 18;")
        self.assertEqual(r.optimizations, [])

    def test_optimized_plan_json_roundtrip(self):
        r = build("SELECT * FROM t WHERE NOT (age > 18) AND 1 = 1;")
        data = r.plan.to_dict()
        self.assertEqual(PlanNode.from_dict(data).to_dict(), data)

    def test_raw_plan_with_unary_roundtrip(self):
        """未优化计划含 UnaryOp / 算术节点，同样要能 JSON 往返。"""
        r = build("SELECT * FROM t WHERE NOT (age + 1 > 18);")
        data = r.raw_plan.to_dict()
        self.assertEqual(PlanNode.from_dict(data).to_dict(), data)

    def test_delete_plan_optimized(self):
        r = build("DELETE FROM t WHERE id = 1 + 1;")
        self.assertEqual(r.raw_plan.to_sexpr(), "(Delete t (= id (+ 1 1)))")
        self.assertEqual(r.plan.to_sexpr(), "(Delete t (= id 2))")


if __name__ == "__main__":
    unittest.main()
