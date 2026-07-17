# Запуск сервиса семейного дерева (WinPython)
Set-Location $PSScriptRoot

$Python = "C:\python\WPy64-31180\scripts\python.bat"

Write-Host "[1/2] Dependencies..." -ForegroundColor Cyan
& $Python -m pip install -r requirements.txt -q
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to install dependencies." -ForegroundColor Red
    exit 1
}

Write-Host "[2/2] Starting Family Tree on http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop."
Write-Host ""
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
