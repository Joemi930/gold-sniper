param(
    [string]$TerminalPath = $env:MT5_TERMINAL_PATH,
    [int]$WaitSeconds = 15
)

if ([string]::IsNullOrWhiteSpace($TerminalPath)) {
    $TerminalPath = "C:\Program Files\MetaTrader 5\terminal64.exe"
}

if (-not (Test-Path -LiteralPath $TerminalPath)) {
    Write-Warning "MT5 introuvable: $TerminalPath"
    exit 0
}

$process = Get-Process terminal,terminal64 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $process) {
    $process = Start-Process -FilePath $TerminalPath -WindowStyle Minimized -PassThru
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Mt5WindowTools {
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@ -ErrorAction SilentlyContinue

$deadline = (Get-Date).AddSeconds([Math]::Max(5, $WaitSeconds))
$handled = $false

while ((Get-Date) -lt $deadline -and -not $handled) {
    Get-Process terminal,terminal64 -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 } |
        ForEach-Object {
            [Mt5WindowTools]::ShowWindow($_.MainWindowHandle, 0) | Out-Null
            $script:handled = $true
        }
    if (-not $handled) {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $handled) {
    Write-Warning "Fenetre MT5 non detectee apres ${WaitSeconds}s; le terminal est lance mais pas encore minimisable."
}
