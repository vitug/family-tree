# Family Tree Service - Development Mode (PowerShell)
Set-Location $PSScriptRoot

function Find-Python {
    $pythonCommands = @('python', 'py', 'python3')
    
    foreach ($cmd in $pythonCommands) {
        try {
            $version = & $cmd --version 2>&1
            if ($LASTEXITCODE -eq 0) {
                return $cmd, $version
            }
        } catch {
            continue
        }
    }
    
    return $null, $null
}

function Write-Header {
    Write-Host "==============================================================================" 
    Write-Host "                   Family Tree Service - Development Mode"
    Write-Host "==============================================================================" 
    Write-Host ""
    Write-Host "Starting with auto-reload enabled for development..."
    Write-Host ""
}

function Write-AccessUrls {
    Write-Host "[Access URLs]" -ForegroundColor Cyan
    Write-Host "  Main:     http://127.0.0.1:8000" -ForegroundColor Green
    Write-Host "  API Docs: http://127.0.0.1:8000/docs" -ForegroundColor Yellow
    Write-Host "  ReDoc:    http://127.0.0.1:8000/redoc" -ForegroundColor Yellow
    Write-Host ""
}

Write-Header

# Find Python
$pythonCmd, $pythonVersion = Find-Python

if ($null -eq $pythonCmd) {
    Write-Host "[ERROR] Python not found!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[Python] $pythonCmd" -ForegroundColor Green
Write-Host "[Version] $pythonVersion" -ForegroundColor Green
Write-Host ""

# Check requirements
if (-not (Test-Path "requirements.txt")) {
    Write-Host "[ERROR] requirements.txt not found!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Install dependencies
Write-Host "[Installing dependencies]" -ForegroundColor Cyan
& $pythonCmd -m pip install -r requirements.txt -q --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to install dependencies." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] Dependencies installed" -ForegroundColor Green
Write-Host ""

# Development configuration
$env:FAMILY_TREE_LOG_LEVEL = "debug"
$env:FAMILY_TREE_HOST = "127.0.0.1"
$env:FAMILY_TREE_PORT = "8000"

Write-Host "[Starting Development Server]" -ForegroundColor Cyan
Write-Host "  Host: 127.0.0.1" 
Write-Host "  Port: 8000"
Write-Host "  Log Level: DEBUG"
Write-Host "  Auth Token: secret (for testing)"
Write-Host ""

Write-AccessUrls

Write-Host "[NOTE] Changes to Python files will cause automatic restart" -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host "==============================================================================" 
Write-Host ""

# Start development server with auto-reload
& $pythonCmd -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --log-level DEBUG

Write-Host ""
Write-Host "[INFO] Server stopped" -ForegroundColor Yellow
Read-Host "Press Enter to exit"
