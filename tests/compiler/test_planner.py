"""执行计划生成器测试（P0 范围）。"""

import unittest

from src.compiler.compiler import SQLCompiler
from src.compiler.planner.plan_nodes import (CreateTablePlan, DeletePlan,
                                             FilterPlan, InsertPlan, PlanNode,
                                             ProjectPlan, SeqScanPlan)

DDL = "CREATE TABLE student(id INT, name VARCHAR, age INT);"


def plan_of(sql):
    """编译 SQL 并返回最后一条语句的执行计划。"""
    results, error = SQLCompiler().compile_safe(sql)
    assert error is None, str(error)
    return results[-1].plan


class TestPlannerShapes(unittest.TestCase):
    def test_create_table_plan(self):
        plan = plan_of(DDL)
        self.assertIsInstance(plan, CreateTablePlan)
        self.assertEqual(plan.to_dict()["table_name"], "student")

    def test_insert_plan_columns_resolved(self):
        plan = plan_of(DDL + " INSERT INTO student VALUES (1,'Alice',20);")
        self.assertIsInstance(plan, InsertPlan)
        # 未显式指定列时，应展开为表定义的全列顺序
        self.assertEqual(plan.columns, ["id", "name", "age"])

    def test_select_star_plan(self):
        plan = plan_of(DDL + " SELECT * FROM student;")
        self.assertIsInstance(plan, ProjectPlan)
        self.assertEqual(plan.columns, "*")
        self.assertIsInstance(plan.child, SeqScanPlan)

    def test_select_where_plan_shape(self):
        plan = plan_of(DDL + " SELECT id,name FROM student WHERE age > 18;")
        self.assertIsInstance(plan, ProjectPlan)
        self.assertEqual(plan.columns, ["id", "name"])
        self.assertIsInstance(plan.child, FilterPlan)
        self.assertIsInstance(plan.child.child, SeqScanPlan)
        self.assertEqual(plan.child.child.table_name, "student")

    def test_delete_plan(self):
        plan = plan_of(DDL + " DELETE FROM student WHERE id = 1;")
        self.assertIsInstance(plan, DeletePlan)
        self.assertEqual(plan.table_name, "student")
        self.assertIsNotNone(plan.predicate)


class TestPlannerOutput(unittest.TestCase):
    def setUp(self):
        self.plan = plan_of(DDL + " SELECT id,name FROM student WHERE age > 18;")

    def test_json_structure(self):
        data = self.plan.to_dict()
        self.assertEqual(data["op"], "Project")
        self.assertEqual(data["columns"], ["id", "name"])
        self.assertEqual(data["child"]["op"], "Filter")
        self.assertEqual(data["child"]["predicate"]["op"], ">")
        self.assertEqual(data["child"]["predicate"]["left"],
                         {"type": "ColumnRef", "name": "age"})
        self.assertEqual(data["child"]["predicate"]["right"],
                         {"type": "Literal", "value": 18, "value_type": "INT"})
        self.assertEqual(data["child"]["child"]["op"], "SeqScan")

    def test_sexpr(self):
        self.assertEqual(self.plan.to_sexpr(),
                         "(Project [id name] (Filter (> age 18) (SeqScan student)))")

    def test_tree(self):
        tree = self.plan.to_tree()
        self.assertIn("Project", tree)
        self.assertIn("Filter", tree)
        self.assertIn("SeqScan", tree)

    def test_roundtrip(self):
        restored = PlanNode.from_dict(self.plan.to_dict())
        self.assertEqual(restored.to_dict(), self.plan.to_dict())
        self.assertEqual(restored.to_sexpr(), self.plan.to_sexpr())

    def test_json_is_serializable(self):
        text = self.plan.to_json()
        self.assertIn('"op": "Project"', text)


if __name__ == "__main__":
    unittest.main()
