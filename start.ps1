# Family Tree Service - Startup Script (PowerShell)
Set-Location $PSScriptRoot

function Write-Header {
    Write-Host "==============================================================================" 
    Write-Host "                   Family Tree Service - Startup Script"
    Write-Host "==============================================================================" 
    Write-Host ""
}

function Find-Python {
    # Try to find Python automatically
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

function Test-Requirements {
    if (-not (Test-Path "requirements.txt")) {
        Write-Host "[ERROR] requirements.txt not found!" -ForegroundColor Red
        return $false
    }
    return $true
}

function Test-Application {
    if (-not (Test-Path "app")) {
        Write-Host "[ERROR] app directory not found!" -ForegroundColor Red
        return $false
    }
    if (-not (Test-Path "app\main.py")) {
        Write-Host "[ERROR] app\main.py not found!" -ForegroundColor Red
        return $false
    }
    return $true
}

function Get-ComputerName {
    return $env:COMPUTERNAME
}

# Main execution
Write-Header

# Find Python
Write-Host "[Finding Python...]" -ForegroundColor Cyan
$pythonCmd, $pythonVersion = Find-Python

if ($null -eq $pythonCmd) {
    Write-Host "[ERROR] Python not found! Please install Python 3.9+ and add it to PATH." -ForegroundColor Red
    Write-Host "Available from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[Python Found] $pythonCmd" -ForegroundColor Green
Write-Host "[Python Version] $pythonVersion" -ForegroundColor Green
Write-Host ""

# Configuration
$env:FAMILY_TREE_EDIT_TOKEN = if ($env:FAMILY_TREE_EDIT_TOKEN) { $env:FAMILY_TREE_EDIT_TOKEN } else { "secret" }
$env:FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS = if ($env:FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS) { $env:FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS } else { "24" }
$env:FAMILY_TREE_LOG_LEVEL = if ($env:FAMILY_TREE_LOG_LEVEL) { $env:FAMILY_TREE_LOG_LEVEL } else { "info" }
$env:FAMILY_TREE_HOST = if ($env:FAMILY_TREE_HOST) { $env:FAMILY_TREE_HOST } else { "0.0.0.0" }
$env:FAMILY_TREE_PORT = if ($env:FAMILY_TREE_PORT) { $env:FAMILY_TREE_PORT } else { "8000" }

# Display configuration
Write-Host "[Configuration]" -ForegroundColor Cyan
Write-Host "  Edit Token: $($env:FAMILY_TREE_EDIT_TOKEN)" 
Write-Host "  JWT Token Lifetime: $($env:FAMILY_TREE_JWT_ACCESS_TOKEN_EXPIRE_HOURS) hours"
Write-Host "  Log Level: $($env:FAMILY_TREE_LOG_LEVEL)"
Write-Host "  Host: $($env:FAMILY_TREE_HOST)"
Write-Host "  Port: $($env:FAMILY_TREE_PORT)"
Write-Host ""

# Check requirements
Write-Host "[1/4] Checking requirements..." -ForegroundColor Cyan
if (-not (Test-Requirements)) {
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] Requirements file found" -ForegroundColor Green
Write-Host ""

# Install dependencies
Write-Host "[2/4] Installing dependencies..." -ForegroundColor Cyan
& $pythonCmd -m pip install -r requirements.txt -q --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to install dependencies." -ForegroundColor Red
    Write-Host "Try running without -q flag to see details:" -ForegroundColor Yellow
    Write-Host "  $pythonCmd -m pip install -r requirements.txt" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] Dependencies installed successfully" -ForegroundColor Green
Write-Host ""

# Check application structure  
Write-Host "[3/4] Checking application structure..." -ForegroundColor Cyan
if (-not (Test-Application)) {
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] Application structure verified" -ForegroundColor Green
Write-Host ""

# Start server
Write-Host "[4/4] Starting Family Tree Service..." -ForegroundColor Cyan
Write-Host "==============================================================================" 
Write-Host "Application is accessible at:" -ForegroundColor Green
Write-Host "  Local:    http://127.0.0.1:$($env:FAMILY_TREE_PORT)"
Write-Host "  Network: http://$(Get-ComputerName):$($env:FAMILY_TREE_PORT)"
Write-Host "==============================================================================" 
Write-Host ""
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host ""

# Start the uvicorn server
& $pythonCmd -m uvicorn app.main:app --host $env:FAMILY_TREE_HOST --port $env:FAMILY_TREE_PORT --log-level $env:FAMILY_TREE_LOG_LEVEL --access-log

Write-Host ""
Write-Host "[INFO] Server stopped" -ForegroundColor Yellow
Read-Host "Press Enter to exit"
