@echo off
title GOLD SNIPER V2.1 - Institutional Intelligence

cls
echo.
echo  ============================================================
echo   * GOLD SNIPER V2.1 - INSTITUTIONAL INTELLIGENCE
echo  ============================================================
echo.
echo  Verification de l'environnement...
echo.

:: Detect Python
set PYTHON_CMD=python
%PYTHON_CMD% --version > nul 2>&1
if %errorlevel% neq 0 (
    set PYTHON_CMD=py
    py --version > nul 2>&1
    if errorlevel 1 (
        echo  [ERREUR] Python n'est pas installe ou pas dans le PATH.
        echo  Telechargez Python 3.11+ sur https://python.org
        pause
        exit /b 1
    )
)

:: Aller dans le bon dossier
cd /d "%~dp0"

echo  Lancement de Gold Sniper...
echo.

:: Lancer le bot
%PYTHON_CMD% main.py

if %errorlevel% neq 0 (
    echo.
    echo  [ERREUR] Le programme s'est termine avec une erreur.
    echo  Consultez les logs dans le dossier 'logs/'
    pause
)