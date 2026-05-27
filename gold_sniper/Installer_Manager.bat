@echo off
:: Installer_Manager.bat
:: Installe la tâche planifiée pour lancer pc_manager.py au démarrage de Windows.

echo ========================================================
echo Installation du Gold Sniper PC Manager
echo ========================================================

set SCRIPT_DIR=%~dp0
set VBS_PATH=%SCRIPT_DIR%lancer_manager.vbs
set PYTHONW_EXE=C:\Users\tetej\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe

:: 1. Création d'un VBS fantôme pour le PC Manager
echo Set WshShell = CreateObject("WScript.Shell") > "%VBS_PATH%"
echo WshShell.Run "cmd /c """"%PYTHONW_EXE%"" ""%SCRIPT_DIR%pc_manager.py""""", 0, False >> "%VBS_PATH%"

:: 2. Création de la tâche planifiée
schtasks /create /tn "GoldSniper_PC_Manager" /tr "wscript.exe \"%VBS_PATH%\"" /sc onlogon /f

echo.
echo [SUCCES] La tache planifiee "GoldSniper_PC_Manager" a ete creee.
echo Le PC Manager demarrera de maniere 100%% invisible a chaque ouverture de session Windows.
echo.
echo Voulez-vous demarrer le PC Manager maintenant ? (O/N)
set /p start_now=
if /i "%start_now%"=="O" (
    wscript.exe "%VBS_PATH%"
    echo PC Manager demarre !
)

pause
