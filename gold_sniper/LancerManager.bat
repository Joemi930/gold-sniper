@echo off
setlocal
cd /d "%~dp0"

REM Ne pas lancer un 2e PC Manager si un est deja actif
set "_GUARD_PY="
where pythonw.exe >nul 2>&1 && set "_GUARD_PY=pythonw.exe"
if not defined _GUARD_PY where pyw.exe >nul 2>&1 && set "_GUARD_PY=pyw.exe"
if not defined _GUARD_PY where python.exe >nul 2>&1 && set "_GUARD_PY=python.exe"
if defined _GUARD_PY (
    %_GUARD_PY% "%~dp0scripts\guard_launch.py" manager >nul 2>&1
    if errorlevel 1 (
        exit /b 0
    )
)

where pythonw.exe >nul 2>&1
if %errorlevel% equ 0 (
    start "Gold Sniper PC Manager" /min pythonw.exe "%~dp0pc_manager.py"
    exit /b 0
)

where pyw.exe >nul 2>&1
if %errorlevel% equ 0 (
    start "Gold Sniper PC Manager" /min pyw.exe "%~dp0pc_manager.py"
    exit /b 0
)

where python.exe >nul 2>&1
if %errorlevel% equ 0 (
    start "Gold Sniper PC Manager" /min python.exe "%~dp0pc_manager.py"
    exit /b 0
)

echo [ERREUR] Python introuvable pour PC Manager.
exit /b 1
