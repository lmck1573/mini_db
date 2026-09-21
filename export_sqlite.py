#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""export_sqlite.py —— 把 Mini-DB 的表和数据导出为真正的 SQLite 数据库文件。

背景：Mini-DB 的 data/mini.db 是自制二进制格式（页式存储），Navicat 无法直接
打开；本脚本用 Python 标准库 sqlite3（零第三方依赖）把「表结构 + 全部数据」
导出为标准 SQLite 文件，Navicat 即可直接打开查看。

用法（在项目根目录，含 src/ 的目录执行）：
    python export_sqlite.py                          # data/mini.db -> mini_db_export.db
    python export_sqlite.py --out 学生库.db           # 自定义输出文件名
    python export_sqlite.py --data-dir data --out out.db

类型映射：INT -> INTEGER，FLOAT -> REAL，VARCHAR/TEXT -> TEXT，NULL 保留为 NULL。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

from src.engine.database import Database

# mini-db 列类型 -> SQLite 类型
_TYPE_MAP = {"INT": "INTEGER", "FLOAT": "REAL", "VARCHAR": "TEXT", "TEXT": "TEXT"}


def quote_ident(name: str) -> str:
    """把表名/列名安全地用双引号包裹（转义内嵌双引号）。"""
    return '"' + name.replace('"', '""') + '"'


def sqlite_type(col_type: str) -> str:
    """mini-db 类型转 SQLite 类型，未知类型回退为 TEXT。"""
    return _TYPE_MAP.get(col_type.upper(), "TEXT")


def export(data_dir: str, out_path: str) -> int:
    """读取 Mini-DB 全部用户表，写入 SQLite 文件，返回导出表数量。"""
    db = Database(data_dir)  # verbose 默认关闭，导出过程不刷 [_CACHE] 日志
    exported = 0
    try:
        # list_tables() 只返回用户表，不含系统目录表 __catalog__
        tables = [t for t in db.catalog.list_tables() if t.lower() != "__catalog__"]

        conn = sqlite3.connect(out_path)
        try:
            cur = conn.cursor()
            for tname in tables:
                cols = db.storage_engine.get_table_schema(tname)
                col_defs = ", ".join(
                    f"{quote_ident(c.name)} {sqlite_type(c.type)}" for c in cols
                )
                cur.execute(
                    f"CREATE TABLE IF NOT EXISTS {quote_ident(tname)} ({col_defs})"
                )
                placeholders = ", ".join("?" for _ in cols)
                insert_sql = f"INSERT INTO {quote_ident(tname)} VALUES ({placeholders})"
                n = 0
                for row in db.storage_engine.scan_table(tname):
                    cur.execute(insert_sql, [row.get(c.name) for c in cols])
                    n += 1
                exported += 1
                print(f"  {tname}: {len(cols)} 列, {n} 行")
            conn.commit()
        finally:
            conn.close()
    finally:
        db.close()

    print(f"\n✅ 已导出 {exported} 张表 -> {os.path.abspath(out_path)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把 Mini-DB 数据导出为 SQLite 文件")
    parser.add_argument("--data-dir", default="data",
                        help="Mini-DB 数据目录（默认 data）")
    parser.add_argument("--out", default="mini_db_export.db",
                        help="输出 SQLite 文件路径（默认 mini_db_export.db）")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.data_dir):
        print(f"[Error] 数据目录不存在：{args.data_dir}", file=sys.stderr)
        return 1
    return export(args.data_dir, args.out)


if __name__ == "__main__":
    sys.exit(main())
