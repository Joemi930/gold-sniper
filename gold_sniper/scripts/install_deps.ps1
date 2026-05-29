# Installe les dependances Gold Sniper dans le Python utilise par le bot.
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Py = Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\python.exe"
if (-not (Test-Path $Py)) {
    $Py = "python"
}
Write-Host "pip install via: $Py"
& $Py -m pip install -r (Join-Path $Root "requirements.txt")
Write-Host "Verification psutil + matplotlib..."
& $Py -c "import psutil, matplotlib; print('OK', psutil.__version__, matplotlib.__version__)"
