# ============================================================================
# AI Record Label — Cross-platform launcher (Windows PowerShell)
# ============================================================================
# Starts all services and opens the desktop app.
# Usage: .\scripts\launch.ps1
#   -Stop        Stop all services
#   -NoApp       Skip opening the desktop app
# ============================================================================

param(
    [switch]$Stop,
    [switch]$NoApp
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

# Resolve data directory
if ($env:AI_RECORD_LABEL_DATA) {
    $DataDir = $env:AI_RECORD_LABEL_DATA
} else {
    $DataDir = Join-Path $env:APPDATA "ai-record-label"
}
New-Item -ItemType Directory -Force -Path "$DataDir\inbox" | Out-Null

$DbFile = Join-Path $DataDir "hermes.db"
$InboxDir = Join-Path $DataDir "inbox"

function Write-Ok($msg) { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "  ✗ $msg" -ForegroundColor Red }
function Write-Info($msg) { Write-Host "  ▸ $msg" -ForegroundColor Cyan }

# ── Stop mode ──────────────────────────────────────────────────────────────
if ($Stop) {
    Write-Info "Stopping all services..."
    foreach ($p in @("a_and_r", "manager", "creative_director", "bandcamp")) {
        $bin = Join-Path $env:LOCALAPPDATA "hermes\$p.exe"
        if (Test-Path $bin) {
            & $bin gateway stop 2>$null
            Write-Ok "$p stopped"
        }
    }

    $pidFile = Join-Path $DataDir ".watcher.pid"
    if (Test-Path $pidFile) {
        $pid = Get-Content $pidFile
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        Remove-Item $pidFile -Force
        Write-Ok "File watcher stopped"
    }

    Write-Ok "All services stopped."
    exit 0
}

Write-Host ""
Write-Host "  ╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║     🎵  AI Record Label  🎵         ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""
Write-Info "Data dir: $DataDir"

# ── 1. Check prerequisites ────────────────────────────────────────────────
Write-Info "Checking prerequisites..."
$hermesBin = Get-Command hermes -ErrorAction SilentlyContinue
if (-not $hermesBin) {
    $hermesBin = Join-Path $env:LOCALAPPDATA "hermes\hermes.exe"
    if (-not (Test-Path $hermesBin)) {
        Write-Err "hermes not found. Install from: https://github.com/NousResearch/hermes-agent"
        exit 1
    }
}

# ── 2. Initialize DB if needed ─────────────────────────────────────────────
if (-not (Test-Path $DbFile)) {
    Write-Info "Initializing database..."
    $migration = Join-Path $Root "schema\migrations\001_initial.sql"
    sqlite3 $DbFile ".read $migration"
    Write-Ok "Database created at $DbFile"
}

# ── 3. Start gateways ─────────────────────────────────────────────────────
Write-Info "Starting agent gateways..."
& hermes gateway start 2>$null
foreach ($p in @("a_and_r", "manager", "creative_director", "bandcamp")) {
    $bin = Get-Command $p -ErrorAction SilentlyContinue
    if ($bin) {
        & $p gateway start 2>$null
        Write-Ok "$p gateway"
    } else {
        Write-Warn "$p agent not installed — skipping"
    }
}

# ── 4. Start file watcher ─────────────────────────────────────────────────
Write-Info "Starting file watcher on $InboxDir..."
$pidFile = Join-Path $DataDir ".watcher.pid"
$watcherRunning = $false
if (Test-Path $pidFile) {
    $pid = Get-Content $pidFile
    $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
    if ($proc) { $watcherRunning = $true }
}

if (-not $watcherRunning) {
    $python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        $python = "python"
    }
    $env:AI_RECORD_LABEL_DATA = $DataDir
    $proc = Start-Process -FilePath $python -ArgumentList "-m", "file_watcher.watcher", $InboxDir, $DbFile -WorkingDirectory $Root -PassThru -WindowStyle Hidden
    $proc.Id | Out-File -FilePath $pidFile -Force
    Write-Ok "File watcher (PID $($proc.Id))"
} else {
    Write-Warn "File watcher already running"
}

# ── 5. Launch desktop app ─────────────────────────────────────────────────
if (-not $NoApp) {
    $binary = Join-Path $Root "desktop-app\src-tauri\target\release\ai-record-label.exe"
    if (Test-Path $binary) {
        Write-Info "Opening desktop app..."
        $env:AI_RECORD_LABEL_DATA = $DataDir
        Start-Process -FilePath $binary
        Write-Ok "App launched"
    } else {
        Write-Warn "Desktop app not built. Run: cd desktop-app; npm run tauri build"
    }
}

Write-Host ""
Write-Ok "AI Record Label is running!"
Write-Host ""
Write-Host "  Data:      $DataDir"
Write-Host "  Database:  $DbFile"
Write-Host "  Inbox:     $InboxDir"
Write-Host ""
Write-Host "  Stop all:  .\scripts\launch.ps1 -Stop"
Write-Host ""
