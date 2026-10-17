@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Run install.bat first to create the app environment.
    pause
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo.
    echo Build dependencies could not be installed. Check your internet connection.
    pause
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name "Yunzii Screen" --collect-all hid --collect-all PIL app.py
if errorlevel 1 (
    echo.
    echo The executable build failed.
    pause
    exit /b 1
)

copy /Y "%~dp0LICENSE" "%~dp0dist\LICENSE.txt" >nul
if errorlevel 1 (
    echo.
    echo The executable was built, but the required license file could not be copied.
    pause
    exit /b 1
)

echo.
echo Built: %~dp0dist\Yunzii Screen.exe
echo Keep dist\LICENSE.txt with the executable when sharing it.
pause
