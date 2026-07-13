@echo off
setlocal enabledelayedexpansion

:: ============================================================
::  AI Record Label — File Watcher Launcher
::  Watches your Ableton folder and sends events to the label.
:: ============================================================

echo.
echo ============================================================
echo   AI Record Label -- File Watcher
echo ============================================================
echo.

:: ── 1. Check Python is installed ─────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on this computer.
    echo.
    echo   To fix this:
    echo     1. Go to https://www.python.org/downloads/
    echo     2. Download Python 3.11 or newer.
    echo     3. During install, CHECK "Add Python to PATH".
    echo     4. Re-open this window and try again.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   Python found: %PYVER%

:: ── 2. Check config.json exists ──────────────────────────────
if not exist "%~dp0config.json" (
    echo.
    echo [ERROR] config.json not found.
    echo.
    echo   To fix this:
    echo     1. In this folder, find config.example.json
    echo     2. Copy it and rename the copy to config.json
    echo     3. Open config.json in Notepad and fill in your values:
    echo          remote_url   -- the tunnel URL from the Mac
    echo          api_token    -- your API token
    echo          watch_folder -- path to your Ableton projects folder
    echo.
    pause
    exit /b 1
)

echo   Config found: %~dp0config.json

:: ── 3. Check / install watchdog ──────────────────────────────
pip show watchdog >nul 2>&1
if errorlevel 1 (
    echo.
    echo   watchdog not installed -- installing now ...
    echo.
    pip install watchdog
    if errorlevel 1 (
        echo.
        echo [ERROR] Failed to install watchdog.
        echo   Try running this window as Administrator, or install manually:
        echo     pip install watchdog
        echo.
        pause
        exit /b 1
    )
    echo.
    echo   watchdog installed successfully.
)

echo   watchdog: OK
echo.

:: ── 4. Start watcher ─────────────────────────────────────────
echo   Starting watcher -- this window staying open means it is running.
echo   Close this window (or press Ctrl+C) to stop watching.
echo.

python "%~dp0watcher.py"

echo.
echo   Watcher stopped.
pause
