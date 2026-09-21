"""合并验收测试（test_merge3）：跨 compiler / storage / engine 三模块的集成回归。

与 tests/ 下各模块单测互补，这里专门覆盖「单测没有覆盖」的空白：

- compiler 四阶段（Token/AST/语义/计划）对同一条 SQL 的一次性联产；
  `compile` 抛错 vs `compile_safe` 返回部分结果；
- engine 共享谓词求值器 eval_expr / resolve_column（7 个比较符、NULL 三值逻辑、
  INT/FLOAT 提升、字符串比较、列名大小写不敏感）；
- 经 Database 门面的端到端 CRUD（建表/多值插入/投影/全比较符过滤/删除）；
- 多表重启后的数据 + 目录（schema）恢复；
- 编译错误传播为 QueryResult(False) 并携带 [错误类型，位置，原因]；
- storage 的空闲链表 free_list、魔数校验；
- 缓存 [_CACHE] 日志的 verbose 开关（默认静默）。

临时数据库统一建在 tests/tmp/，用例结束清理。
"""

import contextlib
import io
import os
import shutil
import unittest

from src.compiler.compiler import SQLCompiler
from src.compiler.errors import CompileError, LexicalError, SemanticError, SyntaxErr
from src.compiler.parser.ast_nodes import BinaryOp, ColumnRef, Literal
from src.compiler.planner.plan_nodes import (DeletePlan, FilterPlan, InsertPlan,
                                             ProjectPlan, SeqScanPlan)
from src.engine.database import Database
from src.engine.errors import ExecutionError
from src.engine.expr import eval_expr, resolve_column
from src.storage import PAGE_SIZE, PageError, StorageManager

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
TMP_DIR = os.path.join(_TESTS_DIR, "tmp")


# --------------------------------------------------------------------------- #
# 一、compiler 完整流水线
# --------------------------------------------------------------------------- #
class TestCompilerPipeline(unittest.TestCase):
    """四类语句一次性走完 词法→语法→语义→计划，并校验 compile 两种模式。"""

    _SCRIPT = (
        "CREATE TABLE student (id INT, name VARCHAR(8), score FLOAT);"
        "INSERT INTO student VALUES (1, 'a', 90.5);"
        "SELECT id, name FROM student WHERE score > 80.0;"
        "DELETE FROM student WHERE id = 1;"
    )

    def test_compile_produces_four_stages_per_statement(self):
        compiler = SQLCompiler()
        results = compiler.compile(self._SCRIPT)
        self.assertEqual(len(results), 4)
        # 四类语句对应的计划算子
        self.assertEqual([type(r.plan).__name__ for r in results],
                         ["CreateTablePlan", "InsertPlan", "ProjectPlan", "DeletePlan"])
        for r in results:
            self.assertTrue(r.semantic_ok)
            self.assertTrue(r.tokens)          # 每句都有 Token 流
            self.assertIsNotNone(r.ast)        # 每句都有 AST
            self.assertIsNotNone(r.plan)       # 每句都有执行计划
            self.assertIsNone(r.error)
        # 语句序号从 1 开始；SQL 由 Token 词素重建
        self.assertEqual([r.index for r in results], [1, 2, 3, 4])
        self.assertIn("CREATE TABLE student", results[0].sql)

    def test_select_plan_shape_is_project_filter_seqscan(self):
        results = SQLCompiler().compile(
            "CREATE TABLE t(id INT, score FLOAT);"
            "SELECT id FROM t WHERE score > 1.0;"
        )
        plan = results[-1].plan
        self.assertIsInstance(plan, ProjectPlan)
        self.assertIsInstance(plan.child, FilterPlan)
        self.assertIsInstance(plan.child.child, SeqScanPlan)

    def test_compile_raises_on_first_error(self):
        with self.assertRaises(SemanticError):
            SQLCompiler().compile("SELECT * FROM nosuch;")

    def test_compile_safe_returns_partial_results(self):
        results, error = SQLCompiler().compile_safe(
            "CREATE TABLE t(id INT); SELECT * FROM nosuch; SELECT * FROM t;"
        )
        self.assertIsInstance(error, SemanticError)
        # 第一条成功 + 第二条失败（第三条未到达）
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0].semantic_ok)
        self.assertFalse(results[1].semantic_ok)
        self.assertIsNotNone(results[1].error)
        self.assertIsNone(results[1].plan)

    def test_lexer_and_syntax_errors_are_distinct(self):
        _, err = SQLCompiler().compile_safe("SELECT @")
        self.assertIsInstance(err, LexicalError)
        self.assertIsNotNone(err.line)
        _, err2 = SQLCompiler().compile_safe("SELECT id FROM t")  # 缺分号
        self.assertIsInstance(err2, SyntaxErr)

    def test_error_triple_format(self):
        self.assertEqual(
            str(SemanticError("表 'x' 不存在", 2, 5)),
            "[SemanticError, Line 2 Col 5, 表 'x' 不存在]",
        )
        self.assertIn("[LexicalError,", str(LexicalError("坏字符", 1, 1)))
        self.assertIn("[SyntaxError,", str(SyntaxErr("期望 ';'", 1, 9)))


# --------------------------------------------------------------------------- #
# 二、共享谓词求值器 eval_expr / resolve_column
# --------------------------------------------------------------------------- #
class TestExpressionEvaluation(unittest.TestCase):
    """engine 的谓词求值器（Filter 与 DELETE 共用），覆盖全比较符与 NULL 语义。"""

    def test_all_comparison_operators(self):
        row = {"id": 10}
        expected = {"=": True, "<>": False, "!=": False,
                    "<": False, "<=": True, ">": False, ">=": True}
        for op, want in expected.items():
            expr = BinaryOp(op, ColumnRef("id"), Literal(10, "INT"))
            self.assertEqual(eval_expr(row, expr), want, f"op={op}")

    def test_comparison_true_branches(self):
        row = {"id": 10}
        self.assertTrue(eval_expr(row, BinaryOp(">", ColumnRef("id"), Literal(5, "INT"))))
        self.assertTrue(eval_expr(row, BinaryOp("<", ColumnRef("id"), Literal(15, "INT"))))
        self.assertTrue(eval_expr(row, BinaryOp("<>", ColumnRef("id"), Literal(0, "INT"))))

    def test_null_never_matches(self):
        row = {"name": None}
        for op in ("=", "<>", "!=", "<", "<=", ">", ">="):
            self.assertFalse(eval_expr(row, BinaryOp(op, ColumnRef("name"),
                                                     Literal("x", "STRING"))))
            self.assertFalse(eval_expr(row, BinaryOp(op, ColumnRef("name"),
                                                     Literal(None, "NULL"))))

    def test_int_float_promotion(self):
        row = {"v": 1}
        self.assertTrue(eval_expr(row, BinaryOp("=", ColumnRef("v"), Literal(1.0, "FLOAT"))))
        self.assertFalse(eval_expr(row, BinaryOp(">", ColumnRef("v"), Literal(1.5, "FLOAT"))))

    def test_string_comparison(self):
        row = {"name": "Alice"}
        self.assertTrue(eval_expr(row, BinaryOp("=", ColumnRef("name"),
                                                Literal("Alice", "STRING"))))
        self.assertTrue(eval_expr(row, BinaryOp("<", ColumnRef("name"),
                                                Literal("Bob", "STRING"))))

    def test_resolve_column_case_insensitive(self):
        row = {"Name": "x"}
        self.assertEqual(resolve_column(row, "name"), "Name")
        self.assertEqual(resolve_column(row, "NAME"), "Name")
        with self.assertRaises(ExecutionError):
            resolve_column(row, "nope")


# --------------------------------------------------------------------------- #
# 三、端到端 CRUD（经 Database 门面）
# --------------------------------------------------------------------------- #
class _DbTestCase(unittest.TestCase):
    """Database 测试基类：每个用例一个独立临时数据目录。"""

    def setUp(self):
        os.makedirs(TMP_DIR, exist_ok=True)
        self.data_dir = os.path.join(TMP_DIR, f"merge3_{self._testMethodName}")
        self.db = Database(self.data_dir)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.data_dir, ignore_errors=True)


class TestEndToEndCrud(_DbTestCase):
    def test_full_workflow_all_comparison_operators(self):
        self.assertTrue(self.db.execute(
            "CREATE TABLE student (id INT, name VARCHAR(32), score FLOAT);").success)
        r = self.db.execute(
            "INSERT INTO student VALUES (1,'a',10.0),(2,'b',20.0),(3,'c',30.0);")
        self.assertTrue(r.success)
        self.assertIn("插入 3 行", r.message)

        def ids_of(cond):
            return [row["id"] for row in
                    self.db.execute(f"SELECT id FROM student WHERE {cond};").rows]

        self.assertEqual(ids_of("score = 20.0"), [2])
        self.assertEqual(ids_of("score <> 20.0"), [1, 3])
        self.assertEqual(ids_of("score != 20.0"), [1, 3])
        self.assertEqual(ids_of("score < 20.0"), [1])
        self.assertEqual(ids_of("score <= 20.0"), [1, 2])
        self.assertEqual(ids_of("score > 20.0"), [3])
        self.assertEqual(ids_of("score >= 20.0"), [2, 3])

    def test_projection_and_delete(self):
        self.db.execute("CREATE TABLE t(id INT, name VARCHAR(16), score FLOAT);")
        self.db.execute("INSERT INTO t VALUES (1,'x',1.0),(2,'y',2.0),(3,'z',3.0);")
        r = self.db.execute("SELECT id, name FROM t;")
        self.assertEqual(list(r.rows[0].keys()), ["id", "name"])
        # 条件删除
        self.assertIn("删除 1 行", self.db.execute("DELETE FROM t WHERE id = 2;").message)
        self.assertEqual(len(self.db.execute("SELECT * FROM t;").rows), 2)
        # 无条件删除整表
        self.assertIn("删除 2 行", self.db.execute("DELETE FROM t;").message)
        self.assertEqual(self.db.execute("SELECT * FROM t;").rows, [])

    def test_text_and_null_roundtrip(self):
        self.db.execute("CREATE TABLE note (id INT, title TEXT, body TEXT);")
        self.db.execute("INSERT INTO note VALUES (1, '你好', 'hello'), (2, NULL, NULL);")
        rows = self.db.execute("SELECT * FROM note;").rows
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {"id": 1, "title": "你好", "body": "hello"})
        self.assertIsNone(rows[1]["title"])
        self.assertIsNone(rows[1]["body"])
        # WHERE NULL 永不匹配
        self.assertEqual(len(self.db.execute("SELECT * FROM note WHERE title = 'x';").rows), 0)

    def test_execute_returns_last_result(self):
        results = self.db.execute_script(
            "CREATE TABLE t(id INT); INSERT INTO t VALUES (1),(2); SELECT * FROM t;")
        self.assertEqual(len(results), 3)
        self.assertEqual(results[-1].rows, [{"id": 1}, {"id": 2}])
        r = self.db.execute(
            "CREATE TABLE u(id INT); INSERT INTO u VALUES (7); SELECT * FROM u;")
        self.assertEqual(r.rows, [{"id": 7}])

    def test_empty_sql_is_ok(self):
        r = self.db.execute("")
        self.assertTrue(r.success)


# --------------------------------------------------------------------------- #
# 四、多表持久化 + 目录恢复
# --------------------------------------------------------------------------- #
class TestPersistenceAndCatalog(_DbTestCase):
    def test_reopen_recovers_multiple_tables_and_schemas(self):
        self.db.execute("CREATE TABLE a(id INT, name VARCHAR(16));")
        self.db.execute("CREATE TABLE b(id INT, val FLOAT);")
        self.db.execute("INSERT INTO a VALUES (1,'x'),(2,'y');")
        self.db.execute("INSERT INTO b VALUES (10,1.5);")
        self.db.close()

        db2 = Database(self.data_dir)
        try:
            self.assertEqual(sorted(db2.catalog.list_tables()), ["a", "b"])
            schema = db2.storage_engine.get_table_schema("a")
            self.assertEqual([(c.name, c.type, c.length) for c in schema],
                             [("id", "INT", None), ("name", "VARCHAR", 16)])
            self.assertEqual(len(db2.execute("SELECT * FROM a;").rows), 2)
            self.assertEqual(db2.execute("SELECT * FROM b;").rows,
                             [{"id": 10, "val": 1.5}])
        finally:
            db2.close()


# --------------------------------------------------------------------------- #
# 五、错误传播（编译错误 → 失败结果）
# --------------------------------------------------------------------------- #
class TestErrorPropagation(_DbTestCase):
    def test_compile_error_becomes_failed_result_with_triple(self):
        self.db.execute("CREATE TABLE t(id INT);")
        r = self.db.execute("SELECT * FROM nosuch;")
        self.assertFalse(r.success)
        self.assertIn("[SemanticError,", r.message)
        self.assertIn("不存在", r.message)

    def test_type_error_becomes_failed_result(self):
        self.db.execute("CREATE TABLE t(id INT);")
        r = self.db.execute("SELECT * FROM t WHERE id = 'abc';")
        self.assertFalse(r.success)
        self.assertIn("[SemanticError,", r.message)


# --------------------------------------------------------------------------- #
# 六、storage 补充边界
# --------------------------------------------------------------------------- #
class TestStorageExtras(unittest.TestCase):
    def test_free_list_reflects_freed_pages(self):
        path = os.path.join(TMP_DIR, "merge3_freelist.db")
        mgr = StorageManager(path)
        try:
            a = mgr.alloc_page()
            b = mgr.alloc_page()
            mgr.free_page(b)
            self.assertEqual(mgr.page_mgr.free_list(), [b])
            self.assertNotIn(a, mgr.page_mgr.free_list())
        finally:
            mgr.close()
            os.remove(path)

    def test_corrupt_magic_raises_page_error(self):
        path = os.path.join(TMP_DIR, "merge3_corrupt.db")
        with open(path, "wb") as f:
            f.write(b"XXXX" + b"\x00" * (PAGE_SIZE - 4))
        try:
            with self.assertRaises(PageError):
                StorageManager(path)
        finally:
            os.remove(path)


# --------------------------------------------------------------------------- #
# 七、缓存日志 verbose 开关
# --------------------------------------------------------------------------- #
class TestVerboseFlag(unittest.TestCase):
    def _run_quiet(self):
        data_dir = os.path.join(TMP_DIR, "merge3_verbose_off")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            db = Database(data_dir)
            db.execute("CREATE TABLE t(id INT); INSERT INTO t VALUES (1);")
            db.execute("SELECT * FROM t;")
            db.close()
        shutil.rmtree(data_dir, ignore_errors=True)
        return buf.getvalue()

    def _run_verbose(self):
        data_dir = os.path.join(TMP_DIR, "merge3_verbose_on")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            db = Database(data_dir, verbose=True)
            db.execute("CREATE TABLE t(id INT);")
            db.close()
        shutil.rmtree(data_dir, ignore_errors=True)
        return buf.getvalue()

    def test_database_quiet_by_default(self):
        self.assertEqual(self._run_quiet(), "")

    def test_verbose_emits_cache_log(self):
        self.assertIn("[_CACHE]", self._run_verbose())


if __name__ == "__main__":
    unittest.main()
