@echo off
rem raytsystem native Windows launcher: double-click -> web UI at 127.0.0.1:8765
rem Loopback-only; opens the default browser automatically.
setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%" || exit /b 1
if not exist "%ROOT%.venv\" (
    echo First run: creating the environment with uv sync --dev
    uv sync --dev || exit /b 1
)
set "RAYTSYSTEM_PLATFORM_ASSUME=windows"
uv run raytsystem start --host 127.0.0.1 --port 8765
echo.
echo Press any key to exit...
pause >nul
endlocal
