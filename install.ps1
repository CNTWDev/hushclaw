# ─────────────────────────────────────────────────────────────────────────────
# GhostClaw Installer  —  Windows (PowerShell 5.1+)
#
# Usage (run in PowerShell as normal user):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\install.ps1
#   .\install.ps1 -Update       # pull latest code and restart
#   .\install.ps1 -StartOnly    # skip install, just start server
#
# Environment overrides (set before running):
#   $env:GHOSTCLAW_HOME   = "C:\Users\you\.ghostclaw"  (default: ~\.ghostclaw)
#   $env:GHOSTCLAW_PORT   = "8765"
#   $env:GHOSTCLAW_HOST   = "0.0.0.0"
#   $env:GHOSTCLAW_NO_BROWSER = "1"   (skip auto-open browser)
# ─────────────────────────────────────────────────────────────────────────────

param(
    [switch]$Update,
    [switch]$StartOnly,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

# ── Config ────────────────────────────────────────────────────────────────────
$RepoUrl    = "https://github.com/CNTWDev/ghostclaw.git"
$InstallDir = if ($env:GHOSTCLAW_HOME) { $env:GHOSTCLAW_HOME } else { "$HOME\.ghostclaw" }
$Port       = if ($env:GHOSTCLAW_PORT) { $env:GHOSTCLAW_PORT } else { "8765" }
$BindHost   = if ($env:GHOSTCLAW_HOST) { $env:GHOSTCLAW_HOST } else { "0.0.0.0" }
$NoBrowser  = $env:GHOSTCLAW_NO_BROWSER -eq "1"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Write-Section($msg) {
    Write-Host ""
    Write-Host "══ $msg " -ForegroundColor Blue -NoNewline
    Write-Host ""
}
function Write-Ok($msg)   { Write-Host "  ✓  $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  ▸  $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "  !  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  ✗  $msg" -ForegroundColor Red }
function Die($msg)        { Write-Err $msg; exit 1 }

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "   _____ _               _      _____ _" -ForegroundColor Cyan
Write-Host "  / ____| |             | |    / ____| |" -ForegroundColor Cyan
Write-Host " | |  __| |__   ___  ___| |_  | |    | | __ ___      __" -ForegroundColor Cyan
Write-Host " | | |_ | '_ \ / _ \/ __| __| | |    | |/ _' \ \ /\ / /" -ForegroundColor Cyan
Write-Host " | |__| | | | | (_) \__ \ |_  | |____| | (_| |\ V  V /" -ForegroundColor Cyan
Write-Host "  \_____|_| |_|\___/|___/\__|  \_____|_|\__,_| \_/\_/" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Lightweight AI Agent Framework with Persistent Memory" -ForegroundColor White
Write-Host "  https://github.com/CNTWDev/ghostclaw" -ForegroundColor DarkCyan
Write-Host ""

if ($Help) {
    Write-Host "Usage: .\install.ps1 [-Update] [-StartOnly] [-Help]"
    Write-Host "  (no flag)   Install GhostClaw and start server"
    Write-Host "  -Update     Pull latest code and restart"
    Write-Host "  -StartOnly  Skip install, start existing installation"
    exit 0
}

$Mode = if ($StartOnly) { "start" } elseif ($Update) { "update" } else { "install" }

# ── Python detection ──────────────────────────────────────────────────────────
Write-Section "Checking Python"

$PythonExe = $null
$candidates = @("python3.13", "python3.12", "python3.11", "python3", "python")

foreach ($cmd in $candidates) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        try {
            $verStr = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            $parts  = $verStr.Trim().Split(".")
            $major  = [int]$parts[0]
            $minor  = [int]$parts[1]
            if ($major -ge 3 -and $minor -ge 11) {
                $PythonExe = $found.Source
                Write-Ok "Found Python $verStr at $PythonExe"
                break
            }
        } catch {}
    }
}

if (-not $PythonExe) {
    Write-Err "Python 3.11+ is required but not found."
    Write-Host ""
    Write-Warn "Download Python from: https://www.python.org/downloads/"
    Write-Warn "  Tick 'Add Python to PATH' during installation."
    Write-Warn "  Then re-run this script."
    Write-Warn ""
    Write-Warn "Or install via winget:"
    Write-Warn "  winget install -e --id Python.Python.3.13"
    exit 1
}

# ── Git detection ─────────────────────────────────────────────────────────────
$GitExe = Get-Command git -ErrorAction SilentlyContinue
if (-not $GitExe) {
    Write-Err "git is required."
    Write-Warn "Install via winget:   winget install -e --id Git.Git"
    Write-Warn "Or download from:     https://git-scm.com/download/win"
    exit 1
}

# ── Install / Update ──────────────────────────────────────────────────────────
if ($Mode -eq "start") {
    if (-not (Test-Path "$InstallDir")) {
        Die "GhostClaw not found at $InstallDir. Run without -StartOnly to install first."
    }
} else {
    Write-Section "Installing GhostClaw → $InstallDir"

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

    $RepoDir = "$InstallDir\repo"
    if (Test-Path "$RepoDir\.git") {
        Write-Info "Updating repository…"
        git -C $RepoDir fetch --quiet origin
        $branch = git -C $RepoDir rev-parse --abbrev-ref --symbolic-full-name "@`{u`}" 2>$null
        if (-not $branch) { $branch = "origin/main" }
        git -C $RepoDir reset --hard $branch --quiet 2>$null
        if ($LASTEXITCODE -ne 0) {
            git -C $RepoDir reset --hard origin/master --quiet
        }
        Write-Ok "Repository updated"
    } else {
        Write-Info "Cloning repository…"
        git clone --depth=1 $RepoUrl $RepoDir --quiet
        Write-Ok "Repository cloned"
    }

    # Virtual environment
    $VenvDir = "$InstallDir\venv"
    if (-not (Test-Path $VenvDir)) {
        Write-Info "Creating virtual environment…"
        & $PythonExe -m venv $VenvDir
        Write-Ok "Virtual environment created"
    }

    $PipExe   = "$VenvDir\Scripts\pip.exe"
    $GcExe    = "$VenvDir\Scripts\ghostclaw.exe"

    Write-Info "Installing/upgrading packages…"
    & $PipExe install --upgrade pip --quiet
    & $PipExe install -e "$RepoDir[server]" --quiet
    Write-Ok "GhostClaw installed"

    # Create a batch launcher
    $LauncherBat = "$InstallDir\ghostclaw-start.bat"
    @"
@echo off
set GHOSTCLAW_PORT=$Port
set GHOSTCLAW_HOST=$BindHost
"$GcExe" serve --host %GHOSTCLAW_HOST% --port %GHOSTCLAW_PORT% %*
"@ | Set-Content $LauncherBat -Encoding ASCII
    Write-Ok "Launcher created: $LauncherBat"
}

$GcExe = "$InstallDir\venv\Scripts\ghostclaw.exe"
if (-not (Test-Path $GcExe)) {
    Die "ghostclaw.exe not found at $GcExe. Installation may have failed."
}

# ── Network info ──────────────────────────────────────────────────────────────
Write-Section "Network Addresses"

# Local IP (prefer non-loopback IPv4)
$LocalIP = ""
try {
    $ips = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
           Where-Object { $_.IPAddress -notmatch '^127\.' -and $_.IPAddress -notmatch '^169\.254\.' } |
           Sort-Object -Property PrefixLength |
           Select-Object -First 1 -ExpandProperty IPAddress
    if ($ips) { $LocalIP = $ips }
} catch {
    try {
        $LocalIP = ([System.Net.Dns]::GetHostAddresses([System.Net.Dns]::GetHostName()) |
                    Where-Object { $_.AddressFamily -eq "InterNetwork" -and
                                   $_.ToString() -notmatch '^127\.' } |
                    Select-Object -First 1).ToString()
    } catch {}
}

# Public IP (best-effort)
$PublicIP = ""
try {
    $PublicIP = (Invoke-WebRequest -Uri "https://api.ipify.org" -UseBasicParsing -TimeoutSec 4).Content.Trim()
} catch {}

Write-Host ""
Write-Host "  ●  Local (this machine)" -ForegroundColor Green
Write-Host "     http://127.0.0.1:$Port" -ForegroundColor Cyan

if ($LocalIP) {
    Write-Host ""
    Write-Host "  ●  LAN (same network)" -ForegroundColor Green
    Write-Host "     http://${LocalIP}:${Port}" -ForegroundColor Cyan
}

if ($PublicIP) {
    Write-Host ""
    Write-Host "  ●  Internet (public IP — only if port $Port is open in firewall)" -ForegroundColor Yellow
    Write-Host "     http://${PublicIP}:${Port}" -ForegroundColor Cyan
}

Write-Host ""
Write-Warn "Tip: On first launch the browser will open a setup wizard to configure your API key."
Write-Warn "     Press Ctrl-C to stop the server."
Write-Host ""

# ── Open browser ──────────────────────────────────────────────────────────────
if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($url)
        Start-Sleep -Seconds 2
        Start-Process $url
    } -ArgumentList "http://127.0.0.1:$Port" | Out-Null
}

# ── Start server ──────────────────────────────────────────────────────────────
Write-Section "Starting GhostClaw Server"
Write-Host "  Listening on http://${BindHost}:${Port}  (Ctrl-C to stop)" -ForegroundColor Cyan
Write-Host ""

& $GcExe serve --host $BindHost --port $Port
