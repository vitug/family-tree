# Family Tree Service - Startup Script (PowerShell)
Set-Location $PSScriptRoot

function Write-Header {
    Write-Host "==============================================================================" 
    Write-Host "                   Family Tree Service - Startup Script"
    Write-Host "==============================================================================" 
    Write-Host ""
}

function Test-PythonVersion {
    param([string]$versionString)
    
    # Parse version like "Python 3.11.0"
    if ($versionString -match "Python (\d+)\.(\d+)") {
        $major = [int]$matches[1]
        $minor = [int]$matches[2]
        
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 9)) {
            return $false, $versionString
        }
        return $true, [string]$matches[0]
    }
    return $false, "Unknown version"
}

function Find-Python {
    # Try to find Python automatically
    $pythonCommands = @('python', 'py', 'python3')
    
    foreach ($cmd in $pythonCommands) {
        try {
            $version = & $cmd --version 2>&1
            if ($LASTEXITCODE -eq 0) {
                $isValid, $parsedVersion = Test-PythonVersion $version
                if ($isValid) {
                    return $cmd, $parsedVersion
                }
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
    if (-not (Test-Path "app\security.py")) {
        Write-Host "[WARNING] app\security.py not found, security features may be limited" -ForegroundColor Yellow
    }
    return $true
}

function New-SecureEnvFile {
    # Generate .env file with secure defaults
    $envContent = @()
    
    $jwtSecret = & python -c "import secrets; print(secrets.token_urlsafe(32))"
    $viewToken = & python -c "import secrets; print(secrets.token_urlsafe(18))"
    $editToken = & python -c "import secrets; print(secrets.token_urlsafe(18))"
    
    if ($env:FAMILY_TREE_LOG_LEVEL) {
        $logLevel = $env:FAMILY_TREE_LOG_LEVEL
    } else {
        $logLevel = "INFO"
    }
    
    if ($env:FAMILY_TREE_PROTECT_API_DOCS) {
        $protectDocs = $env:FAMILY_TREE_PROTECT_API_DOCS
    } else {
        $protectDocs = "true"
    }
    
    $envContent += "FAMILY_TREE_JWT_SECRET_KEY=$jwtSecret"
    $envContent += "FAMILY_TREE_VIEW_TOKEN=$viewToken"
    $envContent += "FAMILY_TREE_EDIT_TOKEN=$editToken"
    $envContent += "FAMILY_TREE_LOG_LEVEL=$logLevel"
    $envContent += "FAMILY_TREE_PROTECT_API_DOCS=$protectDocs"
    $envContent += "FAMILY_TREE_ENABLE_BRUTE_FORCE_PROTECTION=true"
    $envContent += "FAMILY_TREE_ENABLE_SECURITY_HEADERS=true"
    $envContent += "FAMILY_TREE_ALLOW_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000"
    
    Set-Content -Path ".env" -Value $envContent -Encoding UTF8
    
    return @{
        VIEW_TOKEN = $viewToken
        EDIT_TOKEN = $editToken
        JWT_SECRET = $jwtSecret.Substring(0, 8) + "..." + $jwtSecret.Substring(-8)
    }
}

function Get-TokensFromEnv {
    $tokens = @{}
    
    if (Test-Path ".env") {
        $envLines = Get-Content ".env"
        foreach ($line in $envLines) {
            if ($line -match '^FAMILY_TREE_(.+?)_TOKEN=(.+)$') {
                $tokenName = $matches[1]
                $tokenValue = $matches[2]
                if ($tokenValue.Length -gt 14) {
                    $masked = $tokenValue.Substring(0, 10) + "..." + $tokenValue.Substring(-4)
                } else {
                    $masked = $tokenValue.Substring(0, 3) + "..." + $tokenValue.Substring(-3)
                }
                $tokens[$tokenName] = @{
                    RAW = $tokenValue
                    MASKED = $masked
                }
            }
        }
    }
    
    return $tokens
}

function Test-PortAvailable {
    param([string]$port)
    
    $tcpListener = $null
    try {
        $tcpListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
        $tcpListener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($tcpListener) {
            $tcpListener.Stop()
        }
    }
}

function Get-LocalIP {
    try {
        $computerName = $env:COMPUTERNAME
        $ipAddresses = Get-NetIPAddress | Where-Object { $_.AddressFamily -eq "IPv4" -and $_.IPAddress -ne "127.0.0.1" } | Select-Object -First 1 -ExpandProperty IPAddress
        
        if ($ipAddresses) {
            return $ipAddresses
        }
    } catch {
        # Fallback
    }
    
    return $env:COMPUTERNAME
}

# Main execution
Write-Header

# Select startup mode
if (-not $env:FAMILY_TREE_STARTUP_MODE) {
    Write-Host "[Startup Mode]" -ForegroundColor Cyan
    Write-Host "  1 - Development (debug logging, minimal security)"
    Write-Host "  2 - Production (info logging, full security)"
    Write-Host ""
    $modeChoice = Read-Host "Select mode (1 or 2, default: 2)"
    
    if (-not $modeChoice) { $modeChoice = "2" }
    
    switch ($modeChoice) {
        "1" {
            $env:FAMILY_TREE_STARTUP_MODE = "dev"
            $env:FAMILY_TREE_LOG_LEVEL = "DEBUG"
            $env:FAMILY_TREE_PROTECT_API_DOCS = "false"
            $devMode = $true
            Write-Host "[INFO] Development mode enabled" -ForegroundColor Green
        }
        default {
            $env:FAMILY_TREE_STARTUP_MODE = "prod"
            $env:FAMILY_TREE_LOG_LEVEL = "INFO"
            $env:FAMILY_TREE_PROTECT_API_DOCS = "true"
            $devMode = $false
            Write-Host "[INFO] Production mode enabled" -ForegroundColor Green
        }
    }
    Write-Host ""
} else {
    switch ($env:FAMILY_TREE_STARTUP_MODE) {
        "dev" {
            $env:FAMILY_TREE_LOG_LEVEL = "DEBUG"
            $env:FAMILY_TREE_PROTECT_API_DOCS = "false"
            $devMode = $true
        }
        default {
            $env:FAMILY_TREE_LOG_LEVEL = "INFO"
            $env:FAMILY_TREE_PROTECT_API_DOCS = "true"
            $devMode = $false
        }
    }
}

# Find Python
Write-Host "[Python Check]" -ForegroundColor Cyan
$pythonCmd, $pythonVersion = Find-Python

if ($null -eq $pythonCmd) {
    Write-Host "[ERROR] Python not found! Please install Python 3.9+ and add it to PATH." -ForegroundColor Red
    Write-Host "Available from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[Python Found] $pythonCmd" -ForegroundColor Green
Write-Host "[Python Version] $pythonVersion" -ForegroundColor Green
Write-Host "[OK] Python version 3.9+ requirements met" -ForegroundColor Green
Write-Host ""

# Check .env file
Write-Host "[Configuration Check]" -ForegroundColor Cyan
$tokens = Get-TokensFromEnv

if (-not (Test-Path ".env")) {
    Write-Host "[INFO] .env file not found, generating secure defaults..." -ForegroundColor Yellow
    $newTokens = New-SecureEnvFile
    $tokens.VIEW = $newTokens.VIEW_TOKEN
    $tokens.EDIT = $newTokens.EDIT_TOKEN
    Write-Host "[OK] Generated .env file with secure tokens" -ForegroundColor Green
    Write-Host "[IMPORTANT] Save these tokens in a secure location:" -ForegroundColor Yellow
    Write-Host "View Token: $($tokens.VIEW.Substring(0, 10))...$($tokens.VIEW.Substring(-4))" -ForegroundColor Yellow
    Write-Host "Edit Token: $($tokens.EDIT.Substring(0, 10))...$($tokens.EDIT.Substring(-4))" -ForegroundColor Yellow
} else {
    Write-Host "[OK] .env file found" -ForegroundColor Green
}

Write-Host ""

# Display authentication tokens
Write-Host "[Authentication Tokens]" -ForegroundColor Cyan
foreach ($tokenName in $tokens.Keys) {
    $token = $tokens[$tokenName]
    $friendlyName = switch ($tokenName) {
        "VIEW" { "VIEW TOKEN (viewer)" }
        "EDIT" { "EDIT TOKEN (editor)" }
        default { $tokenName }
    }
    Write-Host "  $friendlyName: $($token.MASKED)" -ForegroundColor White
}
Write-Host ""
Write-Host "[IMPORTANT] Use these tokens to login in the application" -ForegroundColor Yellow
Write-Host ""

# Default configuration
if (-not $env:FAMILY_TREE_HOST) { $env:FAMILY_TREE_HOST = "0.0.0.0" }
if (-not $env:FAMILY_TREE_PORT) { $env:FAMILY_TREE_PORT = "8000" }

Write-Host "[Server Configuration]" -ForegroundColor Cyan
Write-Host "  Host: $($env:FAMILY_TREE_HOST)" -ForegroundColor White
Write-Host "  Port: $($env:FAMILY_TREE_PORT)" -ForegroundColor White
Write-Host "  Log Level: $($env:FAMILY_TREE_LOG_LEVEL)" -ForegroundColor White
Write-Host "  Protect API Docs: $($env:FAMILY_TREE_PROTECT_API_DOCS)" -ForegroundColor White
Write-Host ""

# Check requirements
Write-Host "[1/6] Checking requirements..." -ForegroundColor Cyan
if (-not (Test-Requirements)) {
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] Requirements file found" -ForegroundColor Green
Write-Host ""

# Install dependencies
Write-Host "[2/6] Installing dependencies..." -ForegroundColor Cyan
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
Write-Host "[3/6] Checking application structure..." -ForegroundColor Cyan
if (-not (Test-Application)) {
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host "[OK] Application structure verified" -ForegroundColor Green
Write-Host ""

# Prepare directories
Write-Host "[4/6] Preparing directories..." -ForegroundColor Cyan
if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }
if (-not (Test-Path "media")) { New-Item -ItemType Directory -Path "media" | Out-Null }
if (-not (Test-Path "media\files")) { New-Item -ItemType Directory -Path "media\files" | Out-Null }
Write-Host "[OK] Directories prepared" -ForegroundColor Green
Write-Host ""

# Check port availability
Write-Host "[5/6] Checking port $($env:FAMILY_TREE_PORT) availability..." -ForegroundColor Cyan
$portAvailable = Test-PortAvailable -port $env:FAMILY_TREE_PORT

if (-not $portAvailable) {
    Write-Host "[WARNING] Port $($env:FAMILY_TREE_PORT) is already in use!" -ForegroundColor Yellow
    Write-Host "Another application might be running on this port." -ForegroundColor Yellow
    Write-Host ""
    $continue = Read-Host "Continue anyway? (y/n, default: n)"
    if ($continue -ne "y") {
        Write-Host "Aborting startup" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
} else {
    Write-Host "[OK] Port $($env:FAMILY_TREE_PORT) is available" -ForegroundColor Green
}
Write-Host ""

# Start server
Write-Host "[6/6] Starting Family Tree Service..." -ForegroundColor Cyan
Write-Host "==============================================================================" 
Write-Host "Application is accessible at:" -ForegroundColor Green
Write-Host "  Local:    http://127.0.0.1:$($env:FAMILY_TREE_PORT)" -ForegroundColor White
$localIP = Get-LocalIP
Write-Host "  Network: http://$localIP:$($env:FAMILY_TREE_PORT)" -ForegroundColor White
Write-Host "==============================================================================" 

if ($tokens.VIEW) {
    Write-Host "View Token: $($tokens.VIEW.Substring(0, 10))...$($tokens.VIEW.Substring(-4))" -ForegroundColor Yellow
}
if ($tokens.EDIT) {
    Write-Host "Edit Token: $($tokens.EDIT.Substring(0, 10))...$($tokens.EDIT.Substring(-4))" -ForegroundColor Yellow
}

Write-Host "==============================================================================" 
Write-Host ""
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host ""

# Start the uvicorn server
$uvicornLogLevel = $env:FAMILY_TREE_LOG_LEVEL.ToLower()
& $pythonCmd -m uvicorn app.main:app --host $env:FAMILY_TREE_HOST --port $env:FAMILY_TREE_PORT --log-level $uvicornLogLevel --access-log

Write-Host ""
Write-Host "[INFO] Server stopped" -ForegroundColor Yellow
Read-Host "Press Enter to exit"
