# raytsystem native Windows launcher: double-click -> web UI at 127.0.0.1:8765
# Loopback-only; opens the default browser automatically.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

if (-not (Test-Path -LiteralPath (Join-Path $root ".venv"))) {
    Write-Host "First run: creating the environment with uv sync --dev"
    uv sync --dev
}

$env:RAYTSYSTEM_PLATFORM_ASSUME = "windows"
uv run raytsystem start --host 127.0.0.1 --port 8765
