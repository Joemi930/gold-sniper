$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$all = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue
$proj = $all | Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Root*" }

Write-Host "--- Processus Python lies au projet ---"
if ($proj) {
    $proj | ForEach-Object {
        $line = $_.CommandLine
        if ($line.Length -gt 120) { $line = $line.Substring(0, 120) }
        Write-Host ("PID {0}: {1}" -f $_.ProcessId, $line)
    }
} else {
    Write-Host "Aucun."
}

Write-Host ""
Write-Host ("Total python.exe + pythonw.exe (machine): {0}" -f @($all).Count)
Write-Host ("Total lies gold_sniper: {0}" -f @($proj).Count)
Write-Host ""
Write-Host ("kill_flag: {0}" -f (Test-Path (Join-Path $Root "kill_flag.txt")))
foreach ($name in @("data\pc_manager.lock", "data\watchdog.lock", "data\discord_inbox.lock")) {
    $p = Join-Path $Root $name
    Write-Host ("lock {0}: {1}" -f $name, (Test-Path $p))
}
