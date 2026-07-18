@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==============================================================================
echo                   Family Tree Service - Development Mode
echo ==============================================================================
echo Starting with auto-reload enabled for development...
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
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

echo [Python] %PYTHON_CMD%
echo.

if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found!
    pause
    exit /b 1
)

echo [Installing dependencies]
call %PYTHON_CMD% -m pip install -r requirements.txt -q --disable-pip-version-check
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo [OK] Dependencies installed
echo.

REM Development configuration
set "FAMILY_TREE_LOG_LEVEL=debug"
set "FAMILY_TREE_HOST=127.0.0.1"
set "FAMILY_TREE_PORT=8000"

echo [Starting Development Server]
echo   Host: %FAMILY_TREE_HOST%
echo   Port: %FAMILY_TREE_PORT%
echo   Log Level: %FAMILY_TREE_LOG_LEVEL%
echo   Auth Token: secret (for testing)
echo.
echo [Access URLs]
echo   Main: http://%FAMILY_tree_HOST%:%FAMILY_TREE_PORT%
echo   API Docs: http://%FAMILY_TREE_HOST%:%FAMILY_TREE_PORT%/docs
echo   ReDoc: http://%FAMILY_TREE_HOST%:%FAMILY_TREE_PORT%/redoc
echo.
echo [NOTE] Changes to Python files will cause automatic restart
echo Press Ctrl+C to stop the server
echo ==============================================================================
echo.

%PYTHON_CMD% -m uvicorn app.main:app --host %FAMILY_TREE_HOST% --port %FAMILY_TREE_PORT% --reload --log-level %FAMILY_TREE_LOG_LEVEL%

pause
