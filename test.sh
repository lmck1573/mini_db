#!/usr/bin/env bash
# Mini-DB 测试脚本（Git Bash / macOS / Linux）：运行全部 unittest 用例
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

exec "$PY" -m unittest discover -s tests -t . -p "test_*.py" -v "$@"
