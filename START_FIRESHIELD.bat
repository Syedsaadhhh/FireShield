@echo off
cd /d "%~dp0"
python -c "import numpy, cryptography" >nul 2>nul
if errorlevel 1 (
  echo Install Python and run: python -m pip install -r requirements-demo.txt
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8000/operator after the server starts.
python api_adapter.py
pause
