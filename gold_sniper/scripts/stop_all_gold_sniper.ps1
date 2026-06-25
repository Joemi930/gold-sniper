# Arrete toute la pile Gold Sniper (manager, watchdog, main) sur ce PC.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\stop_all_gold_sniper.ps1

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "Gold Sniper - arret complet ($Root)" -ForegroundColor Cyan

# 1) Signal d arret pour le moteur / watchdog
$killFlag = Join-Path $Root "kill_flag.txt"
Set-Content -Path $killFlag -Value "1" -Encoding utf8
Write-Host "kill_flag.txt cree."

# 2) Processus Python lies au projet
$patterns = @("pc_manager.py", "watchdog.py", "main.py", "discord_commander")
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return $false }
        (
            $cmd -like "*$Root*" -or
            $cmd -like "*pc_manager.py*" -or
            $cmd -like "*watchdog.py*" -or
            $cmd -like "*gold_sniper*main.py*"
        ) -and ($patterns | Where-Object { $cmd -like "*$_*" })
    } |
    ForEach-Object {
        $snippet = $_.CommandLine
        if ($snippet.Length -gt 80) { $snippet = $snippet.Substring(0, 80) }
        Write-Host "Stop PID $($_.ProcessId): $snippet"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

# 3) cloudflared tunnel dashboard (orphelins)
$py = Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py -c "import sys; sys.path.insert(0, r'$Root'); from utils.cloudflared_manager import stop_cloudflared_processes; print('cloudflared:', stop_cloudflared_processes())" 2>$null

# 4) Nettoyage locks
@(
    (Join-Path $Root "data\pc_manager.lock"),
    (Join-Path $Root "data\watchdog.lock"),
    (Join-Path $Root "data\discord_inbox.lock")
) | ForEach-Object {
    if (Test-Path $_) {
        Remove-Item $_ -Force -ErrorAction SilentlyContinue
        Write-Host "Lock supprime: $_"
    }
}

Write-Host 'Termine. Relancez un seul manager: LancerManager.bat ou lancer_manager.vbs' -ForegroundColor Green
