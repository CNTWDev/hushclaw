# ─────────────────────────────────────────────────────────────────────────────
# HushClaw Uninstaller  —  Windows (PowerShell 5.1+)
#
# Usage (run in PowerShell as normal user):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\uninstall.ps1
#   .\uninstall.ps1 -KeepData     # remove program files only, keep memory/config
#   .\uninstall.ps1 -Yes          # skip confirmation prompts
#   .\uninstall.ps1 -Yes -KeepData
#
# One-liner:
#   powershell -ExecutionPolicy Bypass -File .\uninstall.ps1
# ─────────────────────────────────────────────────────────────────────────────

param(
    [switch]$KeepData,
    [switch]$Yes,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

# ── Config ────────────────────────────────────────────────────────────────────
$InstallDir = if ($env:HUSHCLAW_HOME) { $env:HUSHCLAW_HOME } else { "$HOME\.hushclaw" }
$Port       = if ($env:HUSHCLAW_PORT) { $env:HUSHCLAW_PORT } else { "8765" }
$PidFile    = "$InstallDir\hushclaw.pid"

# Data/config paths (mirror hushclaw/config/loader.py)
$DataDir    = "$env:LOCALAPPDATA\hushclaw"
$ConfigDir  = "$env:APPDATA\hushclaw"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Write-Section($msg) {
    Write-Host ""
    Write-Host "══ $msg " -ForegroundColor Blue
}
function Write-Ok($msg)   { Write-Host "  ✓  $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  ▸  $msg" -ForegroundColor Cyan }
function Write-Warn($msg) { Write-Host "  !  $msg" -ForegroundColor Yellow }
function Write-Skip($msg) { Write-Host "  ○  $msg" -ForegroundColor DarkGray }

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "    __  __           __    ________" -ForegroundColor Cyan
Write-Host "   / / / /_  _______/ /_  / ____/ /___ __      __" -ForegroundColor Cyan
Write-Host "  / /_/ / / / / ___/ __ \/ /   / / __ ``/ | /| / /" -ForegroundColor Cyan
Write-Host " / __  / /_/ (__  ) / / / /___/ / /_/ /| |/ |/ /" -ForegroundColor Cyan
Write-Host "/_/ /_/\__,_/____/_/ /_/\____/_/\__,_/ |__/|__/" -ForegroundColor Cyan
Write-Host ""
Write-Host "  HushClaw Uninstaller" -ForegroundColor White
Write-Host ""

if ($Help) {
    Write-Host "Usage: .\uninstall.ps1 [-KeepData] [-Yes] [-Help]"
    Write-Host "  (no flag)   Interactive uninstall"
    Write-Host "  -KeepData   Remove program files only; preserve memory, config, and notes"
    Write-Host "  -Yes        Skip all confirmation prompts"
    exit 0
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Section "What will be removed"

Write-Host "  Program files:" -ForegroundColor White
Write-Host "    $InstallDir  (repo + venv + launcher + logs)" -ForegroundColor Cyan
Write-Host "    %APPDATA%\Microsoft\Windows\Start Menu  (shortcuts, if any)" -ForegroundColor Cyan

if (-not $KeepData) {
    Write-Host ""
    Write-Host "  Data & config:" -ForegroundColor White
    Write-Host "    $DataDir  (memory.db, notes, skills, browser)" -ForegroundColor Cyan
    Write-Host "    $ConfigDir  (hushclaw.toml)" -ForegroundColor Cyan
} else {
    Write-Host ""
    Write-Host "  Preserved (-KeepData):" -ForegroundColor White
    Write-Host "    $DataDir" -ForegroundColor DarkGray
    Write-Host "    $ConfigDir" -ForegroundColor DarkGray
}
Write-Host ""

# ── Confirmation ──────────────────────────────────────────────────────────────
if (-not $Yes) {
    if (-not $KeepData) {
        Write-Warn "This will permanently delete HushClaw including all memory, notes, and config."
    } else {
        Write-Warn "This will remove HushClaw program files. Your memory, notes, and config will be kept."
    }
    Write-Host ""
    $confirm = Read-Host "  Are you sure? Type 'yes' to continue"
    Write-Host ""
    if ($confirm -ne "yes") {
        Write-Host "  Aborted."
        exit 0
    }
}

# ── Step 1: Stop running server ───────────────────────────────────────────────
Write-Section "Stopping Server"

$taskName = "HushClaw"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue

if ($task) {
    if ($task.State -eq "Running") {
        Write-Info "Stopping scheduled task '$taskName'…"
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
    Write-Info "Removing scheduled task '$taskName'…"
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Ok "Scheduled task removed"
} else {
    # Fallback: check PID file / process scan
    $savedPid = $null
    if (Test-Path $PidFile) {
        $savedPid = [int](Get-Content $PidFile -ErrorAction SilentlyContinue)
        if ($savedPid -gt 0) {
            $proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
            if (-not $proc) { $savedPid = $null }
        }
    }
    if (-not $savedPid) {
        try {
            $procs = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue |
                     Where-Object { $_.CommandLine -match "hushclaw.*serve" } |
                     Select-Object -First 1
            if ($procs) { $savedPid = [int]$procs.ProcessId }
        } catch {}
    }

    if ($savedPid) {
        Write-Info "Stopping HushClaw (PID $savedPid)…"
        Stop-Process -Id $savedPid -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds(10)
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Id $savedPid -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 500
        }
        Write-Ok "Server stopped"
    } else {
        Write-Skip "No running HushClaw instance found"
    }
}

if (Test-Path $PidFile) {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# ── Step 2: Remove from user PATH ────────────────────────────────────────────
Write-Section "Cleaning PATH"

$ScriptsDir = "$InstallDir\venv\Scripts"
$currentUserPath = [Environment]::GetEnvironmentVariable("PATH", "User")

if ($currentUserPath -and ($currentUserPath -split ";" | Where-Object { $_ -eq $ScriptsDir })) {
    $newPath = ($currentUserPath -split ";" | Where-Object { $_ -ne $ScriptsDir }) -join ";"
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    $env:PATH = ($env:PATH -split ";" | Where-Object { $_ -ne $ScriptsDir }) -join ";"
    Write-Ok "Removed '$ScriptsDir' from user PATH"
} else {
    Write-Skip "HushClaw not found in user PATH"
}

# ── Step 3: Remove Windows Firewall rule ─────────────────────────────────────
Write-Section "Removing Firewall Rule"

$RuleName = "HushClaw-Port-$Port"
$existingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existingRule) {
    try {
        Remove-NetFirewallRule -DisplayName $RuleName -ErrorAction Stop
        Write-Ok "Firewall rule '$RuleName' removed"
    } catch {
        Write-Warn "Could not remove firewall rule (may need to run as Administrator)."
        Write-Info "To remove manually, run as Admin:"
        Write-Info "  Remove-NetFirewallRule -DisplayName '$RuleName'"
    }
} else {
    Write-Skip "Firewall rule '$RuleName' not found"
}

# ── Step 4: Remove installation directory ────────────────────────────────────
Write-Section "Removing Program Files"

if (Test-Path $InstallDir) {
    Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok "Removed $InstallDir"
} else {
    Write-Skip "Installation directory not found: $InstallDir"
}

# ── Step 5: Remove data / config (unless -KeepData) ──────────────────────────
if (-not $KeepData) {
    Write-Section "Removing Data & Config"

    if (Test-Path $DataDir) {
        Remove-Item $DataDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Ok "Removed $DataDir"
    } else {
        Write-Skip "Data directory not found: $DataDir"
    }

    if ((Test-Path $ConfigDir) -and ($ConfigDir -ne $DataDir)) {
        Remove-Item $ConfigDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Ok "Removed $ConfigDir"
    }
} else {
    Write-Section "Data & Config (preserved)"
    Write-Skip "Skipped $DataDir  (-KeepData)"
    if ($ConfigDir -ne $DataDir) { Write-Skip "Skipped $ConfigDir  (-KeepData)" }
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  HushClaw has been uninstalled." -ForegroundColor Green
Write-Host ""
if ($KeepData) {
    Write-Host "  Your memory, notes, and config are still at:" -ForegroundColor White
    Write-Host "  $DataDir" -ForegroundColor Cyan
    Write-Host ""
}
Write-Host "  To reinstall later:" -ForegroundColor White
Write-Host "  powershell -ExecutionPolicy Bypass -Command `"irm https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.ps1 | iex`"" -ForegroundColor Cyan
Write-Host ""
