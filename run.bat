@echo off
rem Mini-DB launcher (Windows)
rem Usage:
rem   double-click            -> interactive mode
rem   run.bat --file tests\compiler\sql\valid.sql
rem   run.bat --plan-format json
rem   run.bat --execute       -> database execute mode
rem Note: switch console to UTF-8 (chcp 65001) and force PYTHONUTF8=1
rem       so Chinese output is not garbled. Prefers .venv interpreter,
rem       otherwise falls back to py -3.11 (Python 3.11).

cd /d "%~dp0"

chcp 65001 >nul
set "PYTHONUTF8=1"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m src.main %*
) else (
    py -3.11 -m src.main %*
)
