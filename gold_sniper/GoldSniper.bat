@echo off
setlocal

cd /d "%~dp0"

powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\start_mt5_minimized.ps1" -WindowMode Hidden -WaitSeconds 60

where pythonw.exe > nul 2>&1
if %errorlevel% equ 0 (
    start "Gold Sniper V2.1" /min pythonw.exe "%~dp0main.py"
    exit /b 0
)

where pyw.exe > nul 2>&1
if %errorlevel% equ 0 (
    start "Gold Sniper V2.1" /min pyw.exe "%~dp0main.py"
    exit /b 0
)

where python.exe > nul 2>&1
if %errorlevel% equ 0 (
    start "Gold Sniper V2.1" /min python.exe "%~dp0main.py"
    exit /b 0
)

echo [ERREUR] Python n'est pas installe ou pas dans le PATH.
pause
exit /b 1
