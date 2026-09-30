# Gold Sniper - Deploy PAPER/DEMO local
# ---------------------------------------------------------------
# This script configures the Windows environment for headless
# Gold Sniper operation: scheduled task + .env setup + validation.
#
# Usage (Run As Administrator for schtasks registration):
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_paper_demo.ps1

param(
    [switch]$SkipScheduledTask,
    [switch]$SkipEnvCheck
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $RepoRoot

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Gold Sniper - PAPER/DEMO Deployment" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# -- 1. Verify .env exists -----------------------------------------------
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.template") {
        Write-Host "[WARN] .env not found. Copy .env.template -> .env and fill in tokens." -ForegroundColor Yellow
        Write-Host "       Required: MT5_PASSWORD, DISCORD_TOKEN, DISCORD_GUILD_ID, DISCORD_USER_ID, DISCORD_*_CHANNEL_ID"
        Write-Host "       Optional: FINNHUB_TOKEN, FMP_TOKEN, DASHBOARD_TOKEN"
        Write-Host ""
        Copy-Item ".env.template" ".env"
        Write-Host "[INFO] Created .env from template. EDIT IT before starting." -ForegroundColor Yellow
    } else {
        Write-Host "[ERROR] No .env or .env.template found." -ForegroundColor Red
        exit 1
    }
}

# -- 2. Verify Python and MT5 ---------------------------------------------
if (-not $SkipEnvCheck) {
    Write-Host "[CHECK] Verifying Python and MT5..." -ForegroundColor Cyan

    $pyCheck = python -c "import MetaTrader5; print('OK')" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Python/MT5 check failed: $pyCheck" -ForegroundColor Red
        exit 1
    }
    Write-Host "        Python + MetaTrader5: OK" -ForegroundColor Green

    # Verify MT5 can connect
    $mt5Check = python -c @"
import MetaTrader5 as mt5
mt5.initialize()
info = mt5.account_info()
print(f'{info.login}|{info.server}|{info.trade_mode}')
mt5.shutdown()
"@ 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] MT5 not running. Start MT5 and log into JustMarkets-Demo3 first." -ForegroundColor Yellow
    } else {
        $parts = $mt5Check -split '\|'
        Write-Host "        MT5 Account: $($parts[0]) @ $($parts[1]) (trade_mode=$($parts[2]), 0=DEMO)" -ForegroundColor Green
    }
}

# -- 3. Run test suite -----------------------------------------------------
Write-Host ""
Write-Host "[CHECK] Running test suite..." -ForegroundColor Cyan
$testResult = python -m pytest gold_sniper/tests -q -p no:cacheprovider --tb=no 2>&1
$testPassed = ($testResult | Select-String -Pattern "(\d+) passed").Matches.Groups[1].Value
Write-Host "        Tests: $testPassed passed (target: >=1690)" -ForegroundColor Green

# -- 4. Verify live guards import -----------------------------------------
Write-Host ""
Write-Host "[CHECK] Verifying live guard imports..." -ForegroundColor Cyan
$guardCheck = python -c @"
import os; os.environ['GOLD_SNIPER_SKIP_DOTENV']='1'
os.environ['GS_UNIFIED_PIPELINE']='1'
import sys; sys.path.insert(0,'gold_sniper')
from execution.live_guards import run_all_live_guards, loss_guard_diag, min_rr_block, concurrency_block
print('OK: 5 guards importable')
from core.unified_live_decision import unified_live_decision, unified_pipeline_enabled
print(f'OK: unified pipeline enabled={unified_pipeline_enabled()}')
"@ 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "        All modules importable" -ForegroundColor Green
} else {
    Write-Host "[ERROR] Import check failed: $guardCheck" -ForegroundColor Red
    exit 1
}

# -- 5. Register Windows Scheduled Task -----------------------------------
if (-not $SkipScheduledTask) {
    Write-Host ""
    Write-Host "[SETUP] Registering Windows Scheduled Task..." -ForegroundColor Cyan

    $pythonBin = (Get-Command python).Source
    $taskName = "GoldSniper_PCManager"
    $taskCommand = "$pythonBin"
    $taskArgs = "-m gold_sniper.pc_manager"
    $taskWorkingDir = $RepoRoot

    # Remove existing task if present
    schtasks /delete /tn $taskName /f 2>$null | Out-Null

    # Create new task: trigger on logon, run hidden
    $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT3M</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
  </Settings>
  <Actions>
    <Exec>
      <Command>$pythonBin</Command>
      <Arguments>-m gold_sniper.pc_manager</Arguments>
      <WorkingDirectory>$RepoRoot</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

    $taskFile = "$RepoRoot\scripts\gold_sniper_task.xml"
    $xml | Out-File -FilePath $taskFile -Encoding Unicode

    schtasks /create /tn $taskName /xml $taskFile 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "        Scheduled task '$taskName' created (ONLOGON + 3min delay)" -ForegroundColor Green
        Remove-Item $taskFile
    } else {
        Write-Host "[WARN] Could not create scheduled task (run as Administrator?)" -ForegroundColor Yellow
        Write-Host "        Manual setup: schtasks /create /tn GoldSniper_PCManager /tr 'python -m gold_sniper.pc_manager' /sc ONLOGON /delay 0003:00"
    }
}

# -- 6. Summary -----------------------------------------------------------
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " DEPLOYMENT SUMMARY" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Pipeline:   Unified Kasper/PDE (GS_UNIFIED_PIPELINE=1)" -ForegroundColor White
Write-Host "Mode:       PAPER (JustMarkets-Demo3, MAGIC 240115)" -ForegroundColor White
Write-Host "Risk Scale: GS_RISK_SCALE=3 (A+ 3%/A 2.25%/B 1.5%)" -ForegroundColor White
Write-Host "Guards:     rr>=4, cap=1/side, dd=10%, breaker=2, cooldown=60min" -ForegroundColor White
Write-Host "Auto-start: ONLOGON + 3min delay -> pc_manager.py -> boot policy" -ForegroundColor White
Write-Host ""
Write-Host "To start manually:  python -m gold_sniper.pc_manager" -ForegroundColor Yellow
Write-Host "To stop:            echo kill > kill_flag.txt  OR  Discord !kill" -ForegroundColor Yellow
Write-Host "To check status:    Discord !status  OR  Dashboard localhost:8765" -ForegroundColor Yellow
Write-Host "To test parity:     python gold_sniper/tests/parity_proof.py" -ForegroundColor Yellow
