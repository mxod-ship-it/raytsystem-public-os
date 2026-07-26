# raytsystem native Windows launcher: double-click -> web UI at 127.0.0.1:8765
# Loopback-only; opens the default browser automatically.
# Requires Administrator rights — auto-elevates if needed.

$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Requesting Administrator privileges..."
    $scriptPath = $MyInvocation.MyCommand.Path
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    exit
}

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root

try {
    if (-not (Test-Path -LiteralPath (Join-Path $root ".venv"))) {
        Write-Host "First run: creating the environment with uv sync --dev"
        uv sync --dev
    }

    $env:RAYTSYSTEM_PLATFORM_ASSUME = "windows"
    uv run raytsystem start --host 127.0.0.1 --port 8765
}
finally {
    Write-Host ""
    Write-Host "Press any key to exit..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
