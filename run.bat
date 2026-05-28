@echo off
echo 🚀 Starting Poly-Robin in virtual environment...
if exist ".\venv\Scripts\python.exe" (
    .\venv\Scripts\python.exe main.py
) else (
    echo ❌ Error: Virtual environment not found at .\venv
    echo Please ensure you have created the venv using: python -m venv venv
    pause
)
