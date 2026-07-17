@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=C:\python\WPy64-31180\scripts\python.bat"

echo [1/2] Dependencies...
call "%PYTHON%" -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo Failed to install dependencies.
  pause
  exit /b 1
)

echo [2/2] Starting Family Tree on http://127.0.0.1:8000
echo Press Ctrl+C to stop.
echo.
call "%PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

pause
