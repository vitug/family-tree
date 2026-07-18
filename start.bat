@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==============================================================================
echo                   Family Tree Service - Startup Script
echo ==============================================================================
echo.

REM Find Python automatically
set "PYTHON_CMD="
for %%p in (python py python3) do (
    where %%p >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_CMD=%%p"
        goto :python_found
    )
)

:python_found
if "%PYTHON_CMD%"=="" (
    echo [ERROR] Python not found! Please install Python 3.9+ and add it to PATH.
    echo Available from: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [Python Found] %PYTHON_CMD%
for /f "tokens=*" %%i in ('%PYTHON_CMD% --version 2^>^&1') do echo [Python Version] %%i
echo.

REM Configuration (can be overridden via environment variables)
if not defined FAMILY_TREE_EDIT_TOKEN set "FAMILY_TREE_EDIT_TOKEN=secret"
if not defined FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS set "FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS=24"
if not defined FAMILY_TREE_LOG_LEVEL set "FAMILY_TREE_LOG_LEVEL=info"
if not defined FAMILY_TREE_HOST set "FAMILY_TREE_HOST=0.0.0.0"
if not defined FAMILY_TREE_PORT set "FAMILY_TREE_PORT=8000"

echo [Configuration]
echo   Edit Token: %FAMILY_TREE_EDIT_TOKEN%
echo   JWT Token Lifetime: %FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS% hours
echo   Log Level: %FAMILY_TREE_LOG_LEVEL%
echo   Host: %FAMILY_TREE_HOST%
echo   Port: %FAMILY_TREE_PORT%
echo.

REM Check if requirements.txt exists
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found!
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
call %PYTHON_CMD% -m pip install -r requirements.txt -q --disable-pip-version-check
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    echo Try running without -q flag to see details:
    echo   %PYTHON_CMD% -m pip install -r requirements.txt
    pause
    exit /b 1
)
echo [OK] Dependencies installed successfully
echo.

REM Check if app directory exists
if not exist "app" (
    echo [ERROR] app directory not found!
    pause
    exit /b 1
)

echo [2/3] Checking application structure...
if not exist "app\main.py" (
    echo [ERROR] app\main.py not found!
    pause
    exit /b 1
)
echo [OK] Application structure verified
echo.

echo [3/3] Starting Family Tree Service...
echo ==============================================================================
echo Application is accessible at:
echo   Local:    http://127.0.0.1:%FAMILY_TREE_PORT%
echo   Network: http://%COMPUTERNAME%:%FAMILY_TREE_PORT%
echo ==============================================================================
echo.
echo Press Ctrl+C to stop the server
echo

REM Start the server
%PYTHON_CMD% -m uvicorn app.main:app --host %FAMILY_TREE_HOST% --port %FAMILY_TREE_PORT% --log-level %FAMILY_TREE_LOG_LEVEL% --access-log

echo.
echo Server stopped
pause
