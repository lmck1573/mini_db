"""语义分析器测试（P0 范围）。"""

import unittest

from src.compiler.compiler import SQLCompiler
from src.compiler.errors import SemanticError
from src.compiler.semantic import Catalog


def compile_sql(sql):
    """编译 SQL，返回 (results, error)。"""
    return SQLCompiler().compile_safe(sql)


class TestSemanticCreateTable(unittest.TestCase):
    def test_create_and_register(self):
        results, error = compile_sql("CREATE TABLE student(id INT, name VARCHAR, age INT);")
        self.assertIsNone(error)
        self.assertTrue(results[0].semantic_ok)
        self.assertIn("创建成功", results[0].message)

    def test_duplicate_table_raises(self):
        sql = "CREATE TABLE t(id INT); CREATE TABLE t(id INT);"
        results, error = compile_sql(sql)
        self.assertIsInstance(error, SemanticError)
        self.assertIn("已存在", error.message)

    def test_duplicate_column_raises(self):
        _, error = compile_sql("CREATE TABLE t(id INT, id INT);")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("列名重复", error.message)

    def test_invalid_varchar_length_raises(self):
        _, error = compile_sql("CREATE TABLE t(name VARCHAR(0));")
        self.assertIsInstance(error, SemanticError)


class TestSemanticInsert(unittest.TestCase):
    def setUp(self):
        self.sql_prefix = "CREATE TABLE student(id INT, name VARCHAR, age INT);"

    def test_insert_ok(self):
        _, error = compile_sql(self.sql_prefix +
                               "INSERT INTO student(id,name,age) VALUES (1,'Alice',20);")
        self.assertIsNone(error)

    def test_unknown_table_raises(self):
        _, error = compile_sql("INSERT INTO nosuch VALUES (1);")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("不存在", error.message)

    def test_unknown_column_raises(self):
        _, error = compile_sql(self.sql_prefix +
                               "INSERT INTO student(id,nmae,age) VALUES (1,'Alice',20);")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("不存在列", error.message)

    def test_column_count_mismatch_raises(self):
        _, error = compile_sql(self.sql_prefix +
                               "INSERT INTO student(id,name,age) VALUES (1,'Alice');")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("列数不一致", error.message)

    def test_type_mismatch_raises(self):
        _, error = compile_sql(self.sql_prefix +
                               "INSERT INTO student(id,name,age) VALUES ('x','Alice',20);")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("类型不匹配", error.message)

    def test_null_is_always_allowed(self):
        _, error = compile_sql(self.sql_prefix +
                               "INSERT INTO student VALUES (1,NULL,NULL);")
        self.assertIsNone(error)

    def test_int_into_float_column_allowed(self):
        _, error = compile_sql("CREATE TABLE t(v FLOAT); INSERT INTO t VALUES (1);")
        self.assertIsNone(error)


class TestSemanticSelectDelete(unittest.TestCase):
    def setUp(self):
        self.sql_prefix = "CREATE TABLE student(id INT, name VARCHAR, age INT);"

    def test_select_ok(self):
        _, error = compile_sql(self.sql_prefix +
                               "SELECT id,name FROM student WHERE age > 18;")
        self.assertIsNone(error)

    def test_select_unknown_column_raises(self):
        _, error = compile_sql(self.sql_prefix + "SELECT nmae FROM student;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("不存在列", error.message)

    def test_where_unknown_column_raises(self):
        _, error = compile_sql(self.sql_prefix + "SELECT * FROM student WHERE aeg > 18;")
        self.assertIsInstance(error, SemanticError)

    def test_where_type_mismatch_raises(self):
        _, error = compile_sql(self.sql_prefix + "SELECT * FROM student WHERE name > 18;")
        self.assertIsInstance(error, SemanticError)
        self.assertIn("类型不匹配", error.message)

    def test_delete_ok(self):
        _, error = compile_sql(self.sql_prefix + "DELETE FROM student WHERE id = 1;")
        self.assertIsNone(error)

    def test_delete_unknown_table_raises(self):
        _, error = compile_sql("DELETE FROM nosuch WHERE id = 1;")
        self.assertIsInstance(error, SemanticError)


class TestCatalog(unittest.TestCase):
    def test_to_dict_and_from_dict(self):
        catalog = Catalog()
        compiler = SQLCompiler(catalog)
        compiler.compile("CREATE TABLE student(id INT, name VARCHAR(32), age INT);")
        restored = Catalog.from_dict(catalog.to_dict())
        schema = restored.get_table("student")
        self.assertIsNotNone(schema)
        self.assertEqual([c.name for c in schema.columns], ["id", "name", "age"])
        self.assertEqual(schema.get_column("name").length, 32)

    def test_table_name_case_insensitive(self):
        catalog = Catalog()
        compiler = SQLCompiler(catalog)
        compiler.compile("CREATE TABLE Student(id INT);")
        self.assertTrue(catalog.has_table("student"))
        self.assertTrue(catalog.has_table("STUDENT"))


if __name__ == "__main__":
    unittest.main()
