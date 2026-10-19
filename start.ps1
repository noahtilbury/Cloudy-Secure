$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

$python = Join-Path $ROOT ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "[1/3] Creating the virtual environment..."
    python -m venv (Join-Path $ROOT ".venv")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] Could not create .venv. Install Python 3.11+ and tick 'Add python.exe to PATH'."
        Read-Host "Press Enter to close"
        exit 1
    }
} else {
    Write-Host "[1/3] Virtual environment found."
}

Write-Host "[2/3] Installing requirements..."
& $python -m pip install --upgrade pip --quiet --disable-pip-version-check
& $python -m pip install -r (Join-Path $ROOT "requirements.txt") --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] Dependency install failed. Run:"
    Write-Host "    .venv\Scripts\python.exe -m pip install -r requirements.txt"
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "[3/3] Starting the bot..."
Write-Host "      (close this window or press Ctrl+C to stop it)"
& $python (Join-Path $ROOT "bot.py")

if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] The bot exited with code $LASTEXITCODE - the error above says why."
    Read-Host "Press Enter to close"
}
