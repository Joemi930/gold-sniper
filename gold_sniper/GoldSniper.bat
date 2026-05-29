@echo off
setlocal

cd /d "%~dp0"

REM Ne pas lancer watchdog/main si pile deja active (sauf apres !kill)
set "_GUARD_PY="
where pythonw.exe >nul 2>&1 && set "_GUARD_PY=pythonw.exe"
if not defined _GUARD_PY where pyw.exe >nul 2>&1 && set "_GUARD_PY=pyw.exe"
if not defined _GUARD_PY where python.exe >nul 2>&1 && set "_GUARD_PY=python.exe"
if defined _GUARD_PY (
    %_GUARD_PY% "%~dp0scripts\guard_launch.py" bot >nul 2>&1
    if errorlevel 1 (
        exit /b 0
    )
)

powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\start_mt5_minimized.ps1" -WaitSeconds 90

where pythonw.exe > nul 2>&1
if %errorlevel% equ 0 (
    start "Gold Sniper V2.1" /min pythonw.exe "%~dp0watchdog.py"
    exit /b 0
)

where pyw.exe > nul 2>&1
if %errorlevel% equ 0 (
    start "Gold Sniper V2.1" /min pyw.exe "%~dp0watchdog.py"
    exit /b 0
)

where python.exe > nul 2>&1
if %errorlevel% equ 0 (
    start "Gold Sniper V2.1" /min python.exe "%~dp0watchdog.py"
    exit /b 0
)

echo [ERREUR] Python n'est pas installe ou pas dans le PATH.
pause
exit /b 1
