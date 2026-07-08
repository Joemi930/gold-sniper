$ErrorActionPreference = "Stop"

$taskName = "GoldSniper_Guard"
$projectRoot = "C:\Users\tetej\Music\Bug bounty\Trading"
$pythonw = "C:\Users\tetej\AppData\Local\Programs\Python\Python313\pythonw.exe"
$guardScript = Join-Path $projectRoot "scripts\gold_sniper_guard.py"
$userId = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path -LiteralPath $pythonw)) {
    throw "pythonw.exe introuvable: $pythonw"
}
if (-not (Test-Path -LiteralPath $guardScript)) {
    throw "Guard introuvable: $guardScript"
}

$action = New-ScheduledTaskAction `
    -Execute $pythonw `
    -Argument "`"$guardScript`"" `
    -WorkingDirectory $projectRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$trigger.Delay = "PT3M"
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -Hidden

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Gold Sniper hidden self-healing guard" `
    -Force | Out-Null

Write-Host "GoldSniper_Guard registered: hidden guard, restart-on-failure"
