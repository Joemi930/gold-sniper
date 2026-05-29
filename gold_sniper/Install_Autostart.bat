@echo off
title Installation demarrage automatique Gold Sniper
cd /d "%~dp0"
echo.
echo Installation des taches planifiees Windows...
echo (PC Manager + Gold Sniper au demarrage / apres rallumage)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_windows_autostart.ps1"
echo.
pause
