#!/usr/bin/env bash
# Mini-DB 测试脚本（Git Bash / macOS / Linux）：运行全部 unittest 用例
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1

if [ -x ".venv/Scripts/python.exe" ]; then
    exec ".venv/Scripts/python.exe" -m unittest discover -s tests -t . -p "test_*.py" -v "$@"
elif [ -x ".venv/bin/python" ]; then
    exec ".venv/bin/python" -m unittest discover -s tests -t . -p "test_*.py" -v "$@"
elif command -v py >/dev/null 2>&1; then
    exec py -3.11 -m unittest discover -s tests -t . -p "test_*.py" -v "$@"
else
    exec python3 -m unittest discover -s tests -t . -p "test_*.py" -v "$@"
fi
