@echo off
REM Khoi dong worker xu ly job (chay cung luc voi run_web.bat)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m worker.run
pause
