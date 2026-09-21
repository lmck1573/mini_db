@echo off
rem Mini-DB test runner (Windows): run all unittest cases
cd /d "%~dp0"

chcp 65001 >nul
set "PYTHONUTF8=1"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m unittest discover -s tests -t . -p "test_*.py" -v %*
) else (
    py -3.11 -m unittest discover -s tests -t . -p "test_*.py" -v %*
)
