#!/usr/bin/env bash
# Mini-DB 一键运行脚本（Git Bash / macOS / Linux）
# 用法：
#   ./run.sh                              # 交互模式
#   ./run.sh --file tests/compiler/sql/valid.sql
#   ./run.sh --plan-format json
#   ./run.sh --execute                    # 数据库执行模式
# 说明：强制 UTF-8 输出避免中文乱码；优先 .venv，其次 py -3.11，最后 python3。

cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1

if [ -x ".venv/Scripts/python.exe" ]; then
    exec ".venv/Scripts/python.exe" -m src.main "$@"
elif [ -x ".venv/bin/python" ]; then
    exec ".venv/bin/python" -m src.main "$@"
elif command -v py >/dev/null 2>&1; then
    exec py -3.11 -m src.main "$@"
else
    exec python3 -m src.main "$@"
fi
