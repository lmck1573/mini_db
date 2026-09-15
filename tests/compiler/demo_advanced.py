"""编译器扩展能力演示脚本（多表连接 / 分组聚合 / 排序 / EXPLAIN / 计划可视化）。

运行：
    cd mini_dbnew
    python tests/compiler/demo_advanced.py

脚本会：
  1. 打印每条 SQL 的 AST → 原始计划 → 优化后计划（含命中的优化规则）；
  2. 把每条语句的执行计划渲染成可视化 HTML，输出到
     tests/compiler/plans/ 目录（用浏览器打开即可看到计划树图）。

全程只使用编译器，不连接存储与执行引擎。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from src.compiler.compiler import SQLCompiler          # noqa: E402
from src.compiler.planner.visualize import write_plan_html  # noqa: E402

SEP = "=" * 76
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plans")

SETUP = [
    "CREATE TABLE student(id INT, name VARCHAR(20), age INT, dept VARCHAR(20));",
    "CREATE TABLE score(sid INT, course VARCHAR(20), score INT);",
]

CASES = [
    ("内连接 + 限定列 + 过滤",
     "SELECT s.name, c.course, c.score FROM student s JOIN score c "
     "ON s.id = c.sid WHERE c.score > 90;"),
    ("左连接（无匹配补 NULL）",
     "SELECT s.name, c.course FROM student s LEFT JOIN score c ON s.id = c.sid;"),
    ("交叉连接（逗号写法）",
     "SELECT student.name, score.course FROM student, score "
     "WHERE student.id = score.sid;"),
    ("三表连接链",
     "SELECT s.name, c.course FROM student s JOIN score c ON s.id = c.sid "
     "JOIN teacher t ON t.id = s.id;"),
    ("分组聚合 + HAVING + 排序",
     "SELECT dept, COUNT(*) AS cnt, AVG(age) AS avg_age FROM student "
     "GROUP BY dept HAVING COUNT(*) > 1 ORDER BY cnt DESC, dept ASC;"),
    ("全局聚合",
     "SELECT COUNT(*), MAX(score), MIN(score), SUM(score) FROM score;"),
    ("聚合表达式（SUM/COUNT 混合）",
     "SELECT dept, SUM(age) / COUNT(*) AS avg_age FROM student "
     "GROUP BY dept ORDER BY avg_age DESC;"),
    ("表达式投影 + 序号排序",
     "SELECT name, age + 1 AS next_age, age * 2 AS double_age FROM student "
     "ORDER BY 2 DESC;"),
    ("EXPLAIN（只输出计划，不执行）",
     "EXPLAIN SELECT s.name FROM student s JOIN score c ON s.id = c.sid "
     "WHERE c.score > 60;"),
]

SETUP_ALL = SETUP + [
    "CREATE TABLE teacher(id INT, name VARCHAR(20));",
]


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    compiler = SQLCompiler()

    print(SEP)
    print("Mini-DB 模块一 SQL 编译器 —— 扩展能力演示")
    print("（多表连接 / 分组聚合 / HAVING / ORDER BY / EXPLAIN / 计划可视化）")
    print(SEP)

    print("\n[建表]")
    for sql in SETUP_ALL:
        results, error = compiler.compile_safe(sql)
        if error:
            print(f"  !! {error}")
            return 1
        print(f"  OK  {results[0].message}")

    for index, (title, sql) in enumerate(CASES, start=1):
        print("\n" + SEP)
        print(f"【{index}】{title}")
        print(SEP)
        print(f"SQL : {sql}")

        results, error = compiler.compile_safe(sql)
        if error:
            print(f"  !! {error}")
            continue
        result = results[0]

        print(f"\n-- 语义分析 --\n   {result.message}")
        if result.raw_plan is not None:
            print(f"\n-- 原始计划 --\n   {result.raw_plan.to_sexpr()}")

        plan = result.display_plan  # EXPLAIN 时取 explain_plan，否则取优化后计划
        if result.plan is not None:
            print(f"\n-- 优化后计划 --\n   {result.plan.to_sexpr()}")
        else:
            print(f"\n-- 执行计划（EXPLAIN，仅编译不执行）--\n   {plan.to_sexpr()}")

        if result.optimizations:
            print("   命中规则：")
            for item in result.optimizations:
                print(f"     · {item}")
        else:
            print("   命中规则：无（谓词中无可用常量/恒真条件）")

        print("\n-- 计划树 --")
        for line in plan.to_tree().splitlines():
            print("   " + line)

        path = os.path.join(OUT_DIR, f"plan_{index}.html")
        write_plan_html(plan, path, title=f"{index}. {title}", sql=sql)
        print(f"\n   可视化：{os.path.relpath(path)}")

    print("\n" + SEP)
    print(f"完成。可视化文件位于：{os.path.relpath(OUT_DIR)}")
    print("用浏览器打开其中的 plan_*.html 即可看到计划树图。")
    print(SEP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
