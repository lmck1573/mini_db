"""Mini-DB 命令行入口（模块一：SQL 编译器）。

用法：
    python -m src.main --file tests/compiler/sql/valid.sql
    python -m src.main --file tests/compiler/sql/invalid.sql
    python -m src.main                     # 交互模式，输入 exit 退出
    python -m src.main --plan-format json  # 只输出 JSON 形式的执行计划
    python -m src.main --execute           # 数据库执行模式（实际执行 SQL 并打印结果集）
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from src.compiler.compiler import SQLCompiler, StatementResult
from src.compiler.errors import CompileError, PlannerError
from src.engine.database import Database, QueryResult

SEPARATOR = "-" * 60


def print_result(result: StatementResult, plan_format: str) -> None:
    """打印单条语句的 Token 流 / AST / 语义结果 / 执行计划。"""
    print(f"\n===== 语句 #{result.index} =====")
    print(f"SQL: {result.sql}")

    print(f"\n--- Token 流（种别码，词素值，行号，列号）---")
    for token in result.tokens:
        print("  " + str(token))

    print(f"\n--- 抽象语法树（AST）---")
    print(result.ast.to_tree())

    if not result.semantic_ok:
        stage = "执行计划生成" if isinstance(result.error, PlannerError) else "语义分析"
        print(f"\n--- {stage} ---")
        print(f"  {result.message}")
        return

    print(f"\n--- 语义分析 ---")
    print(f"  {result.message}")

    print(f"\n--- 执行计划 ---")
    if plan_format in ("tree", "all"):
        print("[树形结构]")
        print(result.plan.to_tree())
    if plan_format in ("json", "all"):
        print("\n[JSON]")
        print(result.plan.to_json())
    if plan_format in ("sexpr", "all"):
        print("\n[S 表达式]")
        print(result.plan.to_sexpr())


def run_sql(sql_text: str, plan_format: str, compiler: Optional[SQLCompiler] = None) -> None:
    """编译一段 SQL 并打印全过程。

    交互模式下复用同一个 compiler，保证 Catalog 在多次输入之间持续生效。
    """
    compiler = compiler if compiler is not None else SQLCompiler()
    results, error = compiler.compile_safe(sql_text)

    for result in results:
        print_result(result, plan_format)

    if error is not None:
        print(f"\n{SEPARATOR}")
        if results:
            print(f"!! 第 {len(results)} 条语句之后编译中断")
        print(f"!! {error}")
    else:
        print(f"\n{SEPARATOR}")
        print(f"全部 {len(results)} 条语句编译通过")


def run_interactive(plan_format: str) -> None:
    """交互模式：累积输入直到遇到分号即编译执行。"""
    print("Mini-DB SQL 编译器（输入 SQL，以 ; 结束；输入 exit 退出）")
    compiler = SQLCompiler()  # 交互期间保持 Catalog
    buffer = ""
    while True:
        try:
            line = input("mini-db> " if not buffer else "      -> ")
        except (EOFError, KeyboardInterrupt):
            print()
            # 输入结束时仍残留未加分号的语句，照样编译一次，让"缺分号"错误能暴露出来
            if buffer.strip():
                run_sql(buffer.strip(), plan_format, compiler)
            break
        if line.strip().lower() in ("exit", "quit"):
            break
        buffer += " " + line
        if ";" in buffer:
            run_sql(buffer.strip(), plan_format, compiler)
            buffer = ""


def _fmt_cell(value) -> str:
    """把结果集单元格格式化为字符串，NULL 显示为 NULL。"""
    if value is None:
        return "NULL"
    return str(value)


def print_query_result(result: QueryResult) -> None:
    """打印执行结果：SELECT 结果集（表格）或 DDL/DML 提示信息。"""
    if not result.success:
        print(f"[Error] {result.message}")
        return
    if result.rows is not None:
        if not result.rows:
            print("(空结果集)")
        else:
            columns = list(result.rows[0].keys())
            cells = [[_fmt_cell(r.get(c)) for c in columns] for r in result.rows]
            widths = [max(len(c), *(len(row[i]) for row in cells))
                      for i, c in enumerate(columns)]
            print(" | ".join(c.ljust(widths[i]) for i, c in enumerate(columns)))
            print("-+-".join("-" * w for w in widths))
            for row in cells:
                print(" | ".join(v.ljust(widths[i]) for i, v in enumerate(row)))
    print(f"[OK] {result.message}")


def run_dbms_file(path: str, verbose: bool = False) -> None:
    """以执行模式运行一个 SQL 文件，逐条打印执行结果。"""
    db = Database("data", verbose=verbose)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            sql_text = fp.read()
        for result in db.execute_script(sql_text):
            print_query_result(result)
    except OSError as err:
        print(f"[IOError] 无法读取文件：{err}")
    finally:
        db.close()


def run_dbms_interactive(verbose: bool = False) -> None:
    """数据库执行模式的交互式 REPL。"""
    db = Database("data", verbose=verbose)
    print("Mini-DB 数据库（输入 SQL，以 ; 结束；输入 exit 退出）")
    buffer = ""
    try:
        while True:
            try:
                line = input("mini-db> " if not buffer else "      -> ")
            except (EOFError, KeyboardInterrupt):
                print()
                if buffer.strip():
                    print_query_result(db.execute(buffer))
                break
            if line.strip().lower() in ("exit", "quit"):
                break
            buffer += " " + line
            if ";" in buffer:
                print_query_result(db.execute(buffer))
                buffer = ""
    finally:
        db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mini-DB SQL 编译器")
    parser.add_argument("--file", help="SQL 文件路径；不指定则进入交互模式")
    parser.add_argument("--plan-format", default="all",
                        choices=["tree", "json", "sexpr", "all"],
                        help="执行计划输出形式（默认 all）")
    parser.add_argument("--execute", action="store_true",
                        help="进入数据库执行模式（实际执行 SQL 并打印结果集）")
    parser.add_argument("--verbose", action="store_true",
                        help="输出 storage 缓存 [_CACHE] 日志（默认关闭）")
    args = parser.parse_args(argv)

    if args.execute:
        if args.file:
            run_dbms_file(args.file, args.verbose)
        else:
            run_dbms_interactive(args.verbose)
        return 0

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as fp:
                sql_text = fp.read()
        except OSError as err:
            print(f"[IOError] 无法读取文件：{err}")
            return 1
        run_sql(sql_text, args.plan_format)
    else:
        run_interactive(args.plan_format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
