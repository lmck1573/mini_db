#!/usr/bin/env bash
# Mini-DB 一键运行脚本（Git Bash / macOS / Linux）
# 用法：
#   ./run.sh                              # 交互模式
#   ./run.sh --file tests/compiler/sql/valid.sql
#   ./run.sh --plan-format json
# 说明：强制 UTF-8 输出避免中文乱码；优先使用 .venv 解释器。

cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1

if [ -x ".venv/Scripts/python.exe" ]; then
    PY=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v py >/dev/null 2>&1; then
    PY="py"
else
    PY="python3"
fi

exec "$PY" -m src.main "$@"
