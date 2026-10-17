@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run install.bat first to install the app dependencies.
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" app.py
if errorlevel 1 (
    echo.
    echo The app could not start. Check that dependencies are installed.
    pause
)
