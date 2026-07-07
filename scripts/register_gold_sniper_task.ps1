$ErrorActionPreference = "Stop"
$taskName = "GoldSniper_PCManager"
$scriptPath = "C:\Users\tetej\Music\Bug bounty\Trading\scripts\start_gold_sniper_hidden.vbs"
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = "PT3M"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "GoldSniper_PCManager registered: ONLOGON + 3min hidden start"
