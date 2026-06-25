$ManagerTaskName = "GoldSniper_PC_Manager"
$LegacyTaskName = "GoldSniperV3_Autostart"

Write-Host "=== Verification autostart Gold Sniper ===" -ForegroundColor Cyan
Write-Host ""

$task = Get-ScheduledTask -TaskName $ManagerTaskName -ErrorAction SilentlyContinue
if ($task) {
    $info = $task | Get-ScheduledTaskInfo
    Write-Host "[OK] Tache planifiee: $ManagerTaskName"
    Write-Host "     Etat: $($task.State)"
    Write-Host "     Derniere execution: $($info.LastRunTime)"
    Write-Host "     Code resultat: $($info.LastTaskResult) (0 = OK)"
    foreach ($tr in $task.Triggers) {
        Write-Host "     Declencheur: $($tr.CimClass.CimClassName) delay=$($tr.Delay)"
    }
    $action = ($task.Actions | Select-Object -First 1)
    if ($action) {
        Write-Host "     Action: $($action.Execute) $($action.Arguments)"
        Write-Host "     Repertoire: $($action.WorkingDirectory)"
    }
} else {
    Write-Host "[--] Tache planifiee $ManagerTaskName : absente"
}

$legacy = Get-ScheduledTask -TaskName $LegacyTaskName -ErrorAction SilentlyContinue
if ($legacy) {
    Write-Host "[!!] Tache legacy encore presente: $LegacyTaskName (risque double demarrage)"
} else {
    Write-Host "[OK] Pas de tache legacy $LegacyTaskName"
}

Write-Host ""
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "GoldSniper_PC_Manager.lnk"
if (Test-Path -LiteralPath $lnk) {
    Write-Host "[OK] Raccourci Demarrage: $lnk"
    $sh = New-Object -ComObject WScript.Shell
    $sc = $sh.CreateShortcut($lnk)
    Write-Host "     Cible: $($sc.TargetPath) $($sc.Arguments)"
} else {
    Write-Host "[--] Raccourci Demarrage GoldSniper_PC_Manager.lnk : absent"
}

Write-Host ""
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
@("lancer_manager.vbs", "lancer_manager_boot.vbs", "LancerManager.bat", "Install_Autostart.bat") | ForEach-Object {
    $p = Join-Path $root $_
    if (Test-Path -LiteralPath $p) {
        Write-Host "[OK] Fichier projet: $_"
    } else {
        Write-Host "[!!] Manquant: $_"
    }
}

Write-Host ""
if ($task -and (Test-Path -LiteralPath $lnk)) {
    Write-Host "CONCLUSION: DOUBLON tache + raccourci - lancez Install_Autostart.bat" -ForegroundColor Red
} elseif ($task -or (Test-Path -LiteralPath $lnk)) {
    Write-Host "CONCLUSION: Autostart OK (une seule methode)." -ForegroundColor Green
} else {
    Write-Host "CONCLUSION: Autostart NON installe. Lancez Install_Autostart.bat" -ForegroundColor Red
}
