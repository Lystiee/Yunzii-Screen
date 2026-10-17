@echo off
setlocal
cd /d "%~dp0"
"python" -m venv .venv
if errorlevel 1 (
    echo.
    echo Could not create the virtual environment. Check your Python installation.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed. Check your internet connection.
    pause
    exit /b 1
)
echo.
echo Dependencies installed. Run start.bat to launch Yunzii Screen.
pause
