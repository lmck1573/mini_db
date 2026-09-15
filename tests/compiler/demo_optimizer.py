"""编译器进阶能力演示脚本（可独立运行，用于现场答辩展示）。

演示两件事：
  1. 复杂表达式：AND / OR / NOT、算术运算、括号改变结合顺序；
  2. 规则优化：同一条 SQL 的「原始计划」与「优化后计划」对比，以及命中的规则。

运行方式（在项目根目录）：
    python -m tests.compiler.demo_optimizer
    python tests/compiler/demo_optimizer.py
"""

import os
import sys

# 允许直接以脚本方式运行：把项目根目录加入模块搜索路径
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))

from src.compiler.compiler import SQLCompiler  # noqa: E402

SETUP = "CREATE TABLE student(id INT, age INT, score FLOAT, name VARCHAR(20));"

CASES = [
    ("① 复杂逻辑组合", "SELECT id, name FROM student WHERE age > 18 AND score >= 60;"),
    ("② 括号 + NOT", "SELECT * FROM student WHERE (age > 18 OR score > 90) AND NOT name = 'x';"),
    ("③ 算术表达式", "SELECT * FROM student WHERE (age + 1) * 2 > 40;"),
    ("④ 常量折叠", "SELECT * FROM student WHERE age > 1 + 2;"),
    ("⑤ 恒真条件短路", "SELECT * FROM student WHERE 1 = 1 AND age > 18;"),
    ("⑥ 恒真谓词消除 Filter", "SELECT * FROM student WHERE age > 18 OR 1 = 1;"),
    ("⑦ NOT 比较取反", "SELECT * FROM student WHERE NOT (age > 18);"),
    ("⑧ 德摩根律", "SELECT * FROM student WHERE NOT (age > 18 AND score = 60);"),
]


def main() -> None:
    compiler = SQLCompiler()
    compiler.compile(SETUP)  # 建表，注册 Catalog

    for title, sql in CASES:
        results, error = compiler.compile_safe(sql)
        print(f"\n=== {title} ===")
        print(f"SQL  : {sql}")
        if error is not None:
            print(f"!! {error}")
            continue

        result = results[0]
        print(f"AST  : {result.ast.where}")
        print(f"原始 : {result.raw_plan.to_sexpr()}")
        print(f"优化 : {result.plan.to_sexpr()}")
        if result.optimizations:
            for item in result.optimizations:
                print(f"       {item}")
        else:
            print("       （无需优化）")


if __name__ == "__main__":
    main()
