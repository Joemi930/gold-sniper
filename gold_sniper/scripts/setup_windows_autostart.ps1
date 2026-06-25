<#
.SYNOPSIS
    Configure le demarrage automatique Windows pour Gold Sniper :
    1) PC Manager (Discord) en premier
    2) Gold Sniper via pc_manager boot policy (LancerGoldSniper.vbs)

    Usage (PowerShell admin recommande) :
      powershell -ExecutionPolicy Bypass -File scripts\setup_windows_autostart.ps1
#>
param(
    [string]$ManagerTaskName = "GoldSniper_PC_Manager",
    [string]$LegacyTaskName = "GoldSniperV3_Autostart",
    [int]$ManagerDelaySeconds = 45,
    [int]$RemoveLegacyDirectStart = 1,
    [switch]$StartupFolderOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ManagerVbs = Join-Path $ProjectRoot "lancer_manager.vbs"
$GoldVbs = Join-Path $ProjectRoot "LancerGoldSniper.vbs"
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Remove-StartupShortcut {
    $startup = [Environment]::GetFolderPath("Startup")
    $lnkPath = Join-Path $startup "GoldSniper_PC_Manager.lnk"
    if (Test-Path -LiteralPath $lnkPath) {
        Remove-Item -LiteralPath $lnkPath -Force
        Write-Host "Raccourci Demarrage supprime: $lnkPath"
    }
}

function Remove-AllAutostartEntries {
    param([string]$ManagerName, [string]$LegacyName)
    Unregister-ScheduledTask -TaskName $ManagerName -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $LegacyName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-StartupShortcut
    Write-Host "Nettoyage autostart existant effectue."
}

Remove-AllAutostartEntries -ManagerName $ManagerTaskName -LegacyName $LegacyTaskName

function New-LogonTask {
    param(
        [string]$Name,
        [string]$Execute,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [int]$DelaySeconds,
        [string]$Description
    )
    $usedStartupFolder = $false
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = "PT${DelaySeconds}S"
    $action = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -WorkingDirectory $WorkingDirectory
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 5) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 0)
    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel Highest
    try {
        Register-ScheduledTask -TaskName $Name -Trigger $trigger -Action $action `
            -Settings $settings -Principal $principal -Description $Description -Force | Out-Null
        return $true
    } catch {
        Write-Warning "Register-ScheduledTask echoue pour $Name ($($_.Exception.Message))."
    }

    Install-StartupShortcut -DelaySeconds $DelaySeconds
    return $false
}

function Install-StartupShortcut {
    param([int]$DelaySeconds = 45)
    $bootVbs = Join-Path $ProjectRoot "lancer_manager_boot.vbs"
    if (-not (Test-Path -LiteralPath $bootVbs)) {
        throw "lancer_manager_boot.vbs introuvable"
    }
    $startup = [Environment]::GetFolderPath("Startup")
    $lnkPath = Join-Path $startup "GoldSniper_PC_Manager.lnk"
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($lnkPath)
    $lnk.TargetPath = "wscript.exe"
    $lnk.Arguments = "`"$bootVbs`" $DelaySeconds"
    $lnk.WorkingDirectory = $ProjectRoot
    $lnk.WindowStyle = 7
    $lnk.Description = "Gold Sniper PC Manager + autostart bot"
    $lnk.Save()
    Write-Host "Raccourci Demarrage: $lnkPath (delai ${DelaySeconds}s)"
}

if (-not (Test-Path -LiteralPath $ManagerVbs)) {
    throw "lancer_manager.vbs introuvable: $ManagerVbs"
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "LancerManager.bat"))) {
    throw "LancerManager.bat introuvable"
}

$taskOk = $false
if ($StartupFolderOnly) {
    Unregister-ScheduledTask -TaskName $ManagerTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Mode StartupFolderOnly: tache planifiee desactivee."
} else {
    $taskOk = New-LogonTask `
        -Name $ManagerTaskName `
        -Execute "wscript.exe" `
        -Arguments $ManagerVbs `
        -WorkingDirectory $ProjectRoot `
        -DelaySeconds $ManagerDelaySeconds `
        -Description "Gold Sniper PC Manager - Discord lifecycle, autostart Gold Sniper via boot policy"
}

$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "GoldSniper_PC_Manager.lnk"

if ($taskOk) {
    Remove-StartupShortcut
    Write-Host "Tache planifiee Windows: $ManagerTaskName (delai ${ManagerDelaySeconds}s)"
    Write-Host "  (une seule methode active - pas de raccourci Demarrage en double)"
} else {
    Unregister-ScheduledTask -TaskName $ManagerTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-StartupShortcut
    Install-StartupShortcut -DelaySeconds $ManagerDelaySeconds
    Write-Host "Demarrage via dossier Demarrage uniquement (raccourci GoldSniper_PC_Manager.lnk)"
    Write-Host "  Astuce: relancer ce script en PowerShell ADMIN pour preferer la tache planifiee."
}

# Verification: une seule methode (si tache OK enregistree ici, pas de raccourci)
$taskExists = $null -ne (Get-ScheduledTask -TaskName $ManagerTaskName -ErrorAction SilentlyContinue)
$lnkExists = Test-Path -LiteralPath $lnkPath
if ($taskOk -and $lnkExists) {
    Write-Warning "Nettoyage raccourci Demarrage (tache planifiee prioritaire)."
    Remove-StartupShortcut
    $lnkExists = $false
}
if ($taskExists -and $lnkExists) {
    Write-Warning "DOUBLON detecte (tache + raccourci). Suppression du raccourci."
    Remove-StartupShortcut
    $lnkExists = $false
}
if (-not $taskExists -and -not $lnkExists) {
    Write-Warning "Aucune methode autostart - creation raccourci fallback."
    Install-StartupShortcut -DelaySeconds $ManagerDelaySeconds
    $lnkExists = $true
}
Write-Host "  -> pc_manager demarre Gold Sniper automatiquement si pas de kill_flag"

# --- 3. Retirer ancienne tache directe (evite double demarrage) ---
if ($RemoveLegacyDirectStart -eq 1) {
    Unregister-ScheduledTask -TaskName $LegacyTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Tache legacy supprimee (si presente): $LegacyTaskName"
    Write-Host "  Gold Sniper est lance par pc_manager, pas par une 2e tache planifiee."
} else {
    if (-not (Test-Path -LiteralPath $GoldVbs)) {
        throw "LancerGoldSniper.vbs introuvable"
    }
    New-LogonTask `
        -Name $LegacyTaskName `
        -Execute "wscript.exe" `
        -Arguments "`"$GoldVbs`"" `
        -WorkingDirectory $ProjectRoot `
        -DelaySeconds ($ManagerDelaySeconds + 90) `
        -Description "Gold Sniper V3 - demarrage direct (fallback)"
    Write-Host "Tache fallback creee: $LegacyTaskName"
}

Write-Host ""
Write-Host "=== Etat final autostart ==="
$taskFinal = Get-ScheduledTask -TaskName $ManagerTaskName -ErrorAction SilentlyContinue
$lnkFinal = Test-Path -LiteralPath $lnkPath
Write-Host ("  Tache {0}: {1}" -f $ManagerTaskName, $(if ($taskFinal) { "OUI" } else { "NON" }))
Write-Host ("  Raccourci Demarrage: {0}" -f $(if ($lnkFinal) { "OUI" } else { "NON" }))
if ($taskFinal -and $lnkFinal) {
    Write-Host "  ERREUR: doublon encore present - relancez ce script en admin." -ForegroundColor Red
} elseif ($taskFinal -or $lnkFinal) {
    Write-Host "  OK: une seule methode active." -ForegroundColor Green
} else {
    Write-Host "  ERREUR: autostart non configure." -ForegroundColor Red
}

Write-Host ""
Write-Host "Installation terminee. Au prochain demarrage Windows :"
Write-Host "  1. Connexion utilisateur"
Write-Host "  2. +${ManagerDelaySeconds}s -> pc_manager (invisible)"
Write-Host "  3. +8s -> Gold Sniper via !start automatique (boot policy)"
Write-Host ""
Write-Host "Verifier : Get-ScheduledTask -TaskName $ManagerTaskName"
