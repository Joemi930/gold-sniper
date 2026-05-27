param(
    [string]$TaskName = "GoldSniperV3_Autostart",
    [int]$DelaySeconds = 60
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VbsFile = Join-Path $ProjectRoot "LancerGoldSniper.vbs"
$LogDir = Join-Path $ProjectRoot "logs"
$LogPath = Join-Path $LogDir "autostart.log"

if (-not (Test-Path -LiteralPath $VbsFile)) {
    throw "LancerGoldSniper.vbs introuvable: $VbsFile"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Trigger.Delay = "PT${DelaySeconds}S"

$Action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "`"$VbsFile`"" `
    -WorkingDirectory $ProjectRoot

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

$Description = "Gold Sniper V3.0 - demarrage automatique a l'ouverture de session avec delai de 60 secondes."
$UserId = "$env:USERDOMAIN\$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Highest

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Trigger $Trigger `
        -Action $Action `
        -Settings $Settings `
        -Principal $Principal `
        -Description $Description `
        -Force | Out-Null
} catch {
    Write-Warning "Creation avec RunLevel Highest impossible ($($_.Exception.Message)). Repli en mode utilisateur standard."
    $Principal = New-ScheduledTaskPrincipal `
        -UserId $UserId `
        -LogonType Interactive `
        -RunLevel Limited

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Trigger $Trigger `
        -Action $Action `
        -Settings $Settings `
        -Principal $Principal `
        -Description $Description `
        -Force | Out-Null
}

$Task = Get-ScheduledTask -TaskName $TaskName
$Info = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host "Tache planifiee creee: $($Task.TaskName)"
Write-Host "Etat: $($Task.State)"
Write-Host "Prochaine execution: $($Info.NextRunTime)"
Write-Host "Action: wscript.exe `"$VbsFile`""
Write-Host "Delai ouverture session: ${DelaySeconds}s"
