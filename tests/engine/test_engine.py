"""engine 模块集成测试。

覆盖（tests/AGENTS.md engine 章节）：
- 建表 → 插入 → 查询 → 条件查询 → 删除 → 再查询
- 重复建表、表不存在等错误路径
- 重启后数据持久性（1000 行量级）
- 部分列插入（NULL 补全）、NULL 往返、INT/FLOAT 比较提升、列名大小写不敏感

临时数据库文件建在 tests/tmp/（gitignore），测试结束清理。
"""

import os
import unittest

from src.engine.database import Database

_TESTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP_DIR = os.path.join(_TESTS_DIR, "tmp")


class EngineTestCase(unittest.TestCase):
    """公共脚手架：每个用例一个独立临时库目录，测试结束清理。"""

    def setUp(self):
        os.makedirs(TMP_DIR, exist_ok=True)
        self.data_dir = os.path.join(TMP_DIR, f"db_{self._testMethodName}")
        self.db = Database(self.data_dir)

    def tearDown(self):
        if getattr(self, "db", None) is not None:
            self.db.close()
        db_file = os.path.join(self.data_dir, "mini.db")
        if os.path.exists(db_file):
            os.remove(db_file)
        if os.path.isdir(self.data_dir):
            os.rmdir(self.data_dir)

    def _create_student(self):
        return self.db.execute(
            "CREATE TABLE student (id INT, name VARCHAR(32), score FLOAT);"
        )


class TestCrudFlow(EngineTestCase):
    """建表 → 插入 → 查询 → 条件查询 → 删除 → 再查询 的主流程。"""

    def test_create_insert_select_roundtrip(self):
        self.assertTrue(self._create_student().success)
        r = self.db.execute("INSERT INTO student VALUES (1, 'Alice', 90.5);")
        self.assertTrue(r.success)
        r = self.db.execute("SELECT * FROM student;")
        self.assertTrue(r.success)
        self.assertEqual(len(r.rows), 1)
        self.assertEqual(r.rows[0]["id"], 1)
        self.assertEqual(r.rows[0]["name"], "Alice")
        self.assertEqual(r.rows[0]["score"], 90.5)

    def test_insert_multiple_rows(self):
        self._create_student()
        r = self.db.execute(
            "INSERT INTO student VALUES (1, 'a', 1.0), (2, 'b', 2.0), (3, 'c', 3.0);"
        )
        self.assertTrue(r.success)
        self.assertEqual(r.message.count("3"), 1)  # 插入 3 行
        r = self.db.execute("SELECT * FROM student;")
        self.assertEqual(len(r.rows), 3)

    def test_select_with_where_filter(self):
        self._create_student()
        self.db.execute(
            "INSERT INTO student VALUES (1, 'a', 10.0), (2, 'b', 20.0), (3, 'c', 30.0);"
        )
        r = self.db.execute("SELECT * FROM student WHERE score > 15.0;")
        self.assertTrue(r.success)
        self.assertEqual([row["id"] for row in r.rows], [2, 3])

    def test_select_projection(self):
        self._create_student()
        self.db.execute("INSERT INTO student VALUES (1, 'Alice', 90.5);")
        r = self.db.execute("SELECT id, name FROM student;")
        self.assertTrue(r.success)
        self.assertEqual(list(r.rows[0].keys()), ["id", "name"])
        self.assertEqual(r.rows[0]["id"], 1)

    def test_where_column_name_case_insensitive(self):
        self._create_student()
        self.db.execute("INSERT INTO student VALUES (1, 'Alice', 90.5);")
        r = self.db.execute("SELECT * FROM student WHERE ID = 1;")
        self.assertTrue(r.success)
        self.assertEqual(len(r.rows), 1)

    def test_delete_with_where_then_reselect(self):
        self._create_student()
        self.db.execute(
            "INSERT INTO student VALUES (1, 'a', 10.0), (2, 'b', 20.0), (3, 'c', 30.0);"
        )
        r = self.db.execute("DELETE FROM student WHERE id = 2;")
        self.assertTrue(r.success)
        r = self.db.execute("SELECT * FROM student;")
        self.assertEqual([row["id"] for row in r.rows], [1, 3])

    def test_delete_all(self):
        self._create_student()
        self.db.execute("INSERT INTO student VALUES (1, 'a', 1.0), (2, 'b', 2.0);")
        r = self.db.execute("DELETE FROM student;")
        self.assertTrue(r.success)
        r = self.db.execute("SELECT * FROM student;")
        self.assertEqual(r.rows, [])


class TestNullAndTypes(EngineTestCase):
    """NULL、部分列插入、类型提升。"""

    def test_insert_partial_columns_fills_null(self):
        self._create_student()
        r = self.db.execute("INSERT INTO student (id, name) VALUES (1, 'partial');")
        self.assertTrue(r.success)
        r = self.db.execute("SELECT * FROM student;")
        self.assertEqual(r.rows[0]["score"], None)

    def test_null_roundtrip(self):
        self._create_student()
        self.db.execute("INSERT INTO student VALUES (1, NULL, NULL);")
        r = self.db.execute("SELECT * FROM student;")
        self.assertEqual(r.rows[0]["name"], None)
        self.assertEqual(r.rows[0]["score"], None)

    def test_where_null_does_not_match(self):
        self._create_student()
        self.db.execute("INSERT INTO student VALUES (1, 'a', 10.0), (2, NULL, NULL);")
        r = self.db.execute("SELECT * FROM student WHERE score > 1.0;")
        self.assertEqual([row["id"] for row in r.rows], [1])

    def test_int_float_comparison_promotes(self):
        self._create_student()
        self.db.execute("INSERT INTO student VALUES (1, 'a', 10.0), (2, 'b', 20.0);")
        # INT 列 id 与 FLOAT 字面量比较，应提升为 FLOAT
        r = self.db.execute("SELECT * FROM student WHERE id > 1.5;")
        self.assertEqual([row["id"] for row in r.rows], [2])


class TestErrorPaths(EngineTestCase):
    """错误路径：重复建表、表/列不存在等，均返回 success=False。"""

    def test_duplicate_create_table_error(self):
        self._create_student()
        r = self.db.execute("CREATE TABLE student (id INT);")
        self.assertFalse(r.success)

    def test_insert_nonexistent_table_error(self):
        r = self.db.execute("INSERT INTO ghost VALUES (1);")
        self.assertFalse(r.success)

    def test_select_nonexistent_table_error(self):
        r = self.db.execute("SELECT * FROM ghost;")
        self.assertFalse(r.success)

    def test_select_nonexistent_column_error(self):
        self._create_student()
        r = self.db.execute("SELECT * FROM student WHERE nope > 1;")
        self.assertFalse(r.success)

    def test_insert_wrong_column_count_error(self):
        self._create_student()
        r = self.db.execute("INSERT INTO student VALUES (1, 'a');")
        self.assertFalse(r.success)


class TestPersistence(EngineTestCase):
    """重启后数据与元数据不丢失。"""

    def test_persistence_across_restart_1000_rows(self):
        self._create_student()
        for i in range(1000):
            r = self.db.execute(f"INSERT INTO student VALUES ({i}, 'n{i}', {i + 0.5});")
            self.assertTrue(r.success)
        self.db.close()

        db2 = Database(self.data_dir)
        try:
            r = db2.execute("SELECT * FROM student;")
            self.assertTrue(r.success)
            self.assertEqual(len(r.rows), 1000)
            # 抽查首尾行，确认顺序与内容一致
            self.assertEqual(r.rows[0]["id"], 0)
            self.assertEqual(r.rows[-1]["id"], 999)
            self.assertEqual(r.rows[-1]["name"], "n999")
        finally:
            db2.close()


if __name__ == "__main__":
    unittest.main()
