# Poly-Robin Startup Script
Write-Host "🚀 Starting Poly-Robin in virtual environment..." -ForegroundColor Cyan

if (Test-Path ".\venv\Scripts\python.exe") {
    .\venv\Scripts\python.exe main.py
} else {
    Write-Host "❌ Error: Virtual environment not found at .\venv" -ForegroundColor Red
    Write-Host "Please ensure you have created the venv using: python -m venv venv" -ForegroundColor Yellow
}
