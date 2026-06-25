@echo off
REM Khoi dong API + giao dien web (http://127.0.0.1:8000)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m uvicorn web.app:app --host 127.0.0.1 --port 8000
pause
