# AI Record Label Windows launcher
# Usage: .\scripts\launch.ps1 [-Stop] [-NoApp] [-NoBuild]

param([switch]$Stop, [switch]$NoApp, [switch]$NoBuild)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DataDir = if ($env:AI_RECORD_LABEL_DATA) {
    [System.IO.Path]::GetFullPath($env:AI_RECORD_LABEL_DATA)
} else {
    Join-Path $env:APPDATA "ai-record-label"
}
$InboxDir = Join-Path $DataDir "inbox"
$DbFile = Join-Path $DataDir "hermes.db"
$ApiPidFile = Join-Path $DataDir ".api.pid"
$WatcherPidFile = Join-Path $DataDir ".watcher.pid"
$ApiLog = Join-Path $DataDir "api.stdout.log"
$ApiErrorLog = Join-Path $DataDir "api.stderr.log"
$WatcherLog = Join-Path $DataDir "watcher.stdout.log"
$WatcherErrorLog = Join-Path $DataDir "watcher.stderr.log"

function Write-Ok($Message) { Write-Host "  [OK] $Message" -ForegroundColor Green }
function Write-Warn($Message) { Write-Host "  [!]  $Message" -ForegroundColor Yellow }
function Write-Info($Message) { Write-Host "  ->  $Message" -ForegroundColor Cyan }

function Stop-ProcessTree($ProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-ProcessTree ([int]$child.ProcessId) }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-RecordedProcess($PidFile, $ExpectedCommand, $Label) {
    if (-not (Test-Path -LiteralPath $PidFile)) { return }
    $serviceProcessId = [int](Get-Content -LiteralPath $PidFile -Raw)
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $serviceProcessId" -ErrorAction SilentlyContinue
    if ($processInfo -and $processInfo.CommandLine -like "*$ExpectedCommand*") {
        Stop-ProcessTree $serviceProcessId
        Write-Ok "$Label stopped"
    } elseif ($processInfo) {
        Write-Warn "$Label PID now belongs to another command; left it running"
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $InboxDir | Out-Null

if ($Stop) {
    Write-Info "Stopping AI Record Label services"
    Stop-RecordedProcess $ApiPidFile "http_api.py" "HTTP API"
    Stop-RecordedProcess $WatcherPidFile "file_watcher.watcher" "File watcher"
    Write-Ok "AI Record Label stopped"
    exit 0
}

# Load project configuration without displaying secrets.
$envFile = Join-Path $Root ".env"
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}
$env:AI_RECORD_LABEL_DATA = $DataDir

$PythonExe = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { throw "Python environment is missing and uv is not installed." }
    Write-Info "Creating Python environment"
    & uv sync
}
if (-not (Test-Path -LiteralPath $PythonExe)) { throw "Python environment was not created." }

Write-Info "Applying database migrations"
& $PythonExe (Join-Path $Root "scripts\migrate_db.py") $DbFile
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
Write-Ok "Database ready"

if (-not $NoBuild) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { throw "npm is required to build the web application." }
    Write-Info "Building web application"
    Push-Location (Join-Path $Root "desktop-app")
    try {
        if (-not (Test-Path -LiteralPath "node_modules")) { & npm ci }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "Web application build failed." }
    } finally {
        Pop-Location
    }
    Write-Ok "Web application built"
}

Stop-RecordedProcess $ApiPidFile "http_api.py" "stale HTTP API"
Stop-RecordedProcess $WatcherPidFile "file_watcher.watcher" "stale file watcher"

Write-Info "Checking file watcher"
$watcherMatches = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like "*file_watcher.watcher*" -and $_.CommandLine -like "*$DbFile*"
})
if ($watcherMatches.Count -gt 0) {
    $matchIds = @($watcherMatches | ForEach-Object { [int]$_.ProcessId })
    $watcherProcess = $watcherMatches | Where-Object {
        $matchIds -notcontains [int]$_.ParentProcessId
    } | Select-Object -First 1
    $watcherProcess.ProcessId | Set-Content -LiteralPath $WatcherPidFile
    Write-Warn "File watcher already running (PID $($watcherProcess.ProcessId))"
} else {
    $watcherProcess = Start-Process -FilePath $PythonExe `
        -ArgumentList "-m", "file_watcher.watcher", $InboxDir, $DbFile `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $WatcherLog -RedirectStandardError $WatcherErrorLog
    $watcherProcess.Id | Set-Content -LiteralPath $WatcherPidFile
    Write-Ok "File watcher started (PID $($watcherProcess.Id))"
}

Write-Info "Checking HTTP API"
$existingListener = Get-NetTCPConnection -LocalPort 8086 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($existingListener) {
    $listenerProcess = Get-CimInstance Win32_Process `
        -Filter "ProcessId = $($existingListener.OwningProcess)"
    if ($listenerProcess.CommandLine -notlike "*http_api.py*") {
        throw "Port 8086 is occupied by an unrelated process (PID $($listenerProcess.ProcessId))."
    }
    $apiProcess = $listenerProcess
    $apiProcess.ProcessId | Set-Content -LiteralPath $ApiPidFile
    Write-Warn "HTTP API already running (PID $($apiProcess.ProcessId)); restart to load code changes"
} else {
    Write-Info "Starting HTTP API"
    $apiProcess = Start-Process -FilePath $PythonExe `
        -ArgumentList (Join-Path $Root "http_api.py") `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $ApiLog -RedirectStandardError $ApiErrorLog
    $apiProcess.Id | Set-Content -LiteralPath $ApiPidFile
}

$healthy = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 250
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8086/health" -TimeoutSec 2
        if ($health) { $healthy = $true; break }
    } catch { }
    if ($apiProcess -is [System.Diagnostics.Process] -and $apiProcess.HasExited) { break }
}
if (-not $healthy) {
    $details = if (Test-Path -LiteralPath $ApiErrorLog) {
        Get-Content $ApiErrorLog -Tail 20 | Out-String
    } else { "No API error log." }
    throw "HTTP API did not become healthy.`n$details"
}
$reportedApiPid = if ($apiProcess.Id) { $apiProcess.Id } else { $apiProcess.ProcessId }
Write-Ok "HTTP API ready at http://localhost:8086 (PID $reportedApiPid)"

if (-not $NoApp) {
    $nativeApp = Join-Path $Root "desktop-app\src-tauri\target\release\ai-record-label.exe"
    if (Test-Path -LiteralPath $nativeApp) {
        Start-Process -FilePath $nativeApp | Out-Null
        Write-Ok "Desktop app opened"
    } else {
        Start-Process "http://localhost:8086/" | Out-Null
        Write-Ok "Web app opened"
    }
}

$hermes = Get-Command hermes -ErrorAction SilentlyContinue
if (-not $hermes) {
    Write-Warn "Optional Hermes gateway is not installed; the core label and action pipeline still work."
}
Write-Host ""
Write-Ok "AI Record Label is running"
Write-Host "  Data: $DataDir"
Write-Host "  Stop: .\scripts\launch.ps1 -Stop"
