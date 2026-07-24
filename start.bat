@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==============================================================================
echo                   Family Tree Service - Startup Script
echo ==============================================================================
echo.

REM Startup mode selection
if "%FAMILY_TREE_STARTUP_MODE%"=="" (
    echo [Startup Mode]
    echo   1 - Development (debug logging, minimal security)
    echo   2 - Production (info logging, full security)
    echo.
    set /p "mode_choice=Select mode (1 or 2, default: 2): "
    if "!mode_choice!"=="" set "mode_choice=2"
    if "!mode_choice!"=="1" (
        set "FAMILY_TREE_LOG_LEVEL=DEBUG"
        set "FAMILY_TREE_PROTECT_API_DOCS=false"
        set "DEV_MODE=true"
        echo [INFO] Development mode enabled
    ) else (
        set "FAMILY_TREE_LOG_LEVEL=INFO"
        set "FAMILY_TREE_PROTECT_API_DOCS=true"
        set "DEV_MODE=false"
        echo [INFO] Production mode enabled
    )
    echo.
) else (
    if "%FAMILY_TREE_STARTUP_MODE%"=="dev" (
        set "FAMILY_TREE_LOG_LEVEL=DEBUG"
        set "FAMILY_TREE_PROTECT_API_DOCS=false"
        set "DEV_MODE=true"
    ) else (
        set "FAMILY_TREE_LOG_LEVEL=INFO"
        set "FAMILY_TREE_PROTECT_API_DOCS=true"
        set "DEV_MODE=false"
    )
)

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

echo [Python Check]
for /f "tokens=*" %%i in ('%PYTHON_CMD% --version 2^>^&1') do echo [Python Found] %%i
for /f "tokens=2" %%v in ('%PYTHON_CMD% --version 2^>^&1 ^| findstr /R "[0-9][.][0-9]"') do (
    set "PYTHON_VERSION=%%v"
    echo [Python Version] !PYTHON_VERSION!
)

REM Check Python version (minimum 3.9)
for /f "tokens=1,2,3 delims=." %%a in ("%PYTHON_VERSION%") do (
    set "MAJOR=%%a"
    set "MINOR=%%b"
    if !MAJOR! lss 3 (
        echo [ERROR] Python 3.9+ required, found !MAJOR!.!MINOR!
        pause
        exit /b 1
    )
    if !MAJOR! equ 3 if !MINOR! lss 9 (
        echo [ERROR] Python 3.9+ required, found !MAJOR!.!MINOR!
        pause
        exit /b 1
    )
)
echo [OK] Python version meets requirements
echo.

REM Check .env file
echo [Configuration Check]
if not exist ".env" (
    echo [INFO] .env file not found, generating secure defaults...
    %PYTHON_CMD% -c "import secrets; print('FAMILY_TREE_JWT_SECRET_KEY=' + secrets.token_urlsafe(32))" > .env
    %PYTHON_CMD% -c "import secrets; print('FAMILY_TREE_VIEW_TOKEN=' + secrets.token_urlsafe(18))" >> .env
    %PYTHON_CMD% -c "import secrets; print('FAMILY_TREE_EDIT_TOKEN=' + secrets.token_urlsafe(18))" >> .env
    echo FAMILY_TREE_LOG_LEVEL=!FAMILY_TREE_LOG_LEVEL! >> .env
    echo FAMILY_TREE_PROTECT_API_DOCS=!FAMILY_TREE_PROTECT_API_DOCS! >> .env
    echo FAMILY_TREE_ENABLE_BRUTE_FORCE_PROTECTION=true >> .env
    echo FAMILY_TREE_ENABLE_SECURITY_HEADERS=true >> .env
    echo FAMILY_TREE_ALLOW_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000 >> .env
    echo [OK] Generated .env file with Secure Tokens
    echo [IMPORTANT] Save these tokens in a secure location:
    %PYTHON_CMD% -c "from envtmp import os; f = open('.env'); lines = f.readlines(); tokens = [l for l in lines if 'TOKEN' in l]; print(''.join(tokens).replace('=\n', ' '))"
) else (
    echo [OK] .env file found
)
echo.

REM Display authentication tokens (masked)
echo [Authentication Tokens]
for /f "tokens=2 delims==" %%t in ('findstr "TOKEN" .env') do (
    for /f "tokens=1,2 delims==*" %%a in ("%%t") do (
        set var=%%a
        set val=%%b
        if "!var!"=="FAMILY_TREE_VIEW_TOKEN" (
            set "VIEW_TOKEN=!val!"
            echo   VIEW TOKEN: !val:~0,10!...!val:~-4! (!val:~0,10!...[masked])
        )
        if "!var!"=="FAMILY_TREE_EDIT_TOKEN" (
            set "EDIT_TOKEN=!val!"
            echo   EDIT TOKEN: !val:~0,10!...!val:~-4! (!val:~0,10!...[masked])
        )
        if "!var!"=="FAMILY_TREE_JWT_SECRET_KEY" (
            echo   JWT SECRET: !val:~0,8!...!val:~-8! (!val:~0,8!...[masked])
        )
    )
)
echo.
echo [IMPORTANT] Use these tokens to login in the application
echo.

REM Default configuration
if "%FAMILY_TREE_HOST%"=="" set "FAMILY_TREE_HOST=0.0.0.0"
if "%FAMILY_TREE_PORT%"=="" set "FAMILY_TREE_PORT=8000"

echo [Server Configuration]
echo   Host: %FAMILY_TREE_HOST%
echo   Port: %FAMILY_TREE_PORT%
echo   Log Level: %FAMILY_TREE_LOG_LEVEL%
echo   Protect API Docs: %FAMILY_TREE_PROTECT_API_DOCS%
echo.

REM Check if requirements.txt exists
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found!
    pause
    exit /b 1
)

echo [1/5] Installing dependencies...
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

echo [2/5] Checking application structure...
if not exist "app\main.py" (
    echo [ERROR] app\main.py not found!
    pause
    exit /b 1
)
if not exist "app\security.py" (
    echo [WARNING] app\security.py not found, security features disabled
)
echo [OK] Application structure verified
echo.

REM Create logs directory
echo [3/5] Preparing directories...
if not exist "logs" mkdir logs
if not exist "media" mkdir media
echo [OK] Directories prepared
echo.

REM Check if port is available
echo [4/5] Checking port availability...
netstat -ano | findstr ":%FAMILY_TREE_PORT% " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    echo [WARNING] Port %FAMILY_TREE_PORT% is already in use!
    echo Another application might be running on this port.
    echo.
    set /p "continue=Continue anyway? (y/n, default: n): "
    if /i not "!continue!"=="y" (
        echo Aborting startup
        pause
        exit /b 1
    )
)
echo [OK] Port check completed
echo.

REM Start server
echo [5/5] Starting Family Tree Service...
echo ==============================================================================
echo Application is accessible at:
echo   Local:    http://127.0.0.1:%FAMILY_TREE_PORT%
echo   Network: http://%COMPUTERNAME%:%FAMILY_TREE_PORT%
echo ==============================================================================
echo Use view token to login: %VIEW_TOKEN%
echo Use edit token for full access: %EDIT_TOKEN%
echo ==============================================================================
echo.
echo Press Ctrl+C to stop the server
echo.

REM Convert log level to lowercase for uvicorn (uvicorn requires lowercase log levels)
powershell -Command "'%FAMILY_TREE_LOG_LEVEL%'.ToLower()" > temp_log.txt
set /p UVICORN_LOG_LEVEL=<temp_log.txt
del temp_log.txt

REM Start the uvicorn server
%PYTHON_CMD% -m uvicorn app.main:app --host %FAMILY_TREE_HOST% --port %FAMILY_TREE_PORT% --log-level %UVICORN_LOG_LEVEL% --access-log

echo.
echo [INFO] Server stopped
pause
