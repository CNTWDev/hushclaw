# ─────────────────────────────────────────────────────────────────────────────
# HushClaw Installer  —  Windows (PowerShell 5.1+)
#
# Usage (run in PowerShell as normal user):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\install.ps1
#   .\install.ps1 -Update       # stop old process, pull latest code, restart in background
#   .\install.ps1 -StartOnly    # skip install, start existing installation in background
#   .\install.ps1 -Stop         # stop the running server and exit
#   .\install.ps1 -Foreground   # install and start server in foreground (debug mode)
#
# One-liner (auto-bypasses execution policy for this session only):
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#
# Environment overrides (set before running):
#   $env:HUSHCLAW_HOME   = "C:\Users\you\.hushclaw"  (default: ~\.hushclaw)
#   $env:HUSHCLAW_PORT   = "8765"
#   $env:HUSHCLAW_HOST   = "0.0.0.0"
#   $env:HUSHCLAW_NO_BROWSER = "1"   (skip auto-open browser)
# ─────────────────────────────────────────────────────────────────────────────

param(
    [switch]$Update,
    [switch]$StartOnly,
    [switch]$Stop,
    [switch]$Foreground,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

# ── Config ────────────────────────────────────────────────────────────────────
$RepoUrl    = "https://github.com/CNTWDev/hushclaw.git"
$InstallDir = if ($env:HUSHCLAW_HOME) { $env:HUSHCLAW_HOME } else { "$HOME\.hushclaw" }
$Port       = if ($env:HUSHCLAW_PORT) { $env:HUSHCLAW_PORT } else { "8765" }
$BindHost   = if ($env:HUSHCLAW_HOST) { $env:HUSHCLAW_HOST } else { "0.0.0.0" }
$NoBrowser  = $env:HUSHCLAW_NO_BROWSER -eq "1"

$PidFile    = "$InstallDir\hushclaw.pid"
$LogFile    = "$InstallDir\hushclaw.log"

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

# Refresh PATH in current session after winget/system installs
function Refresh-EnvPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
    $userPath    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
    $env:PATH    = "$machinePath;$userPath"
}

# ── Process management helpers ────────────────────────────────────────────────

function Get-HushClawPid {
    # 1. Check PID file first
    if (Test-Path $PidFile) {
        $savedPid = [int](Get-Content $PidFile -ErrorAction SilentlyContinue)
        if ($savedPid -gt 0) {
            $proc = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
            if ($proc) {
                return $savedPid
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    # 2. Fallback: scan processes by command line
    try {
        $procs = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue |
                 Where-Object { $_.CommandLine -match "hushclaw.*serve" } |
                 Select-Object -First 1
        if ($procs) { return [int]$procs.ProcessId }
    } catch {}
    return $null
}

function Stop-HushClaw($pidToStop) {
    Write-Info "Stopping HushClaw (PID $pidToStop)…"
    Stop-Process -Id $pidToStop -ErrorAction SilentlyContinue
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $pidToStop -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
    Write-Ok "Server stopped"
}

function Start-HushClawBackground($gcExe) {
    $taskName = "HushClaw"
    # Try Windows Task Scheduler first (survives reboots, no window)
    try {
        $action   = New-ScheduledTaskAction -Execute $gcExe `
                        -Argument "serve --host $BindHost --port $Port"
        $trigger  = New-ScheduledTaskTrigger -AtStartup
        $settings = New-ScheduledTaskSettingsSet `
                        -RestartCount 3 `
                        -RestartInterval (New-TimeSpan -Minutes 1) `
                        -ExecutionTimeLimit ([TimeSpan]::Zero)
        Register-ScheduledTask -TaskName $taskName -Action $action `
            -Trigger $trigger -Settings $settings `
            -RunLevel Highest -Force -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        Write-Ok "Registered and started as Windows Scheduled Task '$taskName'"
        Write-Info "Manage: Task Scheduler → '$taskName'"
        Write-Info "Stop:   .\install.ps1 -Stop"
    } catch {
        # Fallback: hidden background process + PID file
        Write-Warn "Task Scheduler registration failed ($($_.Exception.Message)) — using hidden process fallback"
        $proc = Start-Process -FilePath $gcExe `
                    -ArgumentList "serve","--host",$BindHost,"--port",$Port `
                    -WindowStyle Hidden -PassThru -RedirectStandardOutput $LogFile `
                    -ErrorAction Stop
        $proc.Id | Set-Content $PidFile
        Write-Ok "Server started in background (PID $($proc.Id))"
        Write-Info "Logs: $LogFile"
        Write-Info "Stop: .\install.ps1 -Stop"
    }
}

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "    __  __           __    ________" -ForegroundColor Cyan
Write-Host "   / / / /_  _______/ /_  / ____/ /___ __      __" -ForegroundColor Cyan
Write-Host "  / /_/ / / / / ___/ __ \/ /   / / __ ``/ | /| / /" -ForegroundColor Cyan
Write-Host " / __  / /_/ (__  ) / / / /___/ / /_/ /| |/ |/ /" -ForegroundColor Cyan
Write-Host "/_/ /_/\__,_/____/_/ /_/\____/_/\__,_/ |__/|__/" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Lightweight AI Agent Framework with Persistent Memory" -ForegroundColor White
Write-Host ""
Write-Host "  ───────────────────────────────────────────────────────" -ForegroundColor Blue
Write-Host "  https://github.com/CNTWDev/hushclaw  ·  tuanweishi@gmail.com" -ForegroundColor DarkCyan
Write-Host "  ───────────────────────────────────────────────────────" -ForegroundColor Blue
Write-Host ""

if ($Help) {
    Write-Host "Usage: .\install.ps1 [-Update] [-StartOnly] [-Stop] [-Foreground] [-Help]"
    Write-Host "  (no flag)   Install HushClaw and start server in background"
    Write-Host "  -Update     Stop old process, pull latest code, restart in background"
    Write-Host "  -StartOnly  Skip install, start existing installation in background"
    Write-Host "  -Stop       Stop the running HushClaw server and exit"
    Write-Host "  -Foreground Install and start server in foreground (debug mode)"
    exit 0
}

$Mode = if ($StartOnly) { "start" } elseif ($Update) { "update" } elseif ($Stop) { "stop" } else { "install" }

# ── --Stop mode: early exit ───────────────────────────────────────────────────
if ($Mode -eq "stop") {
    # Check Task Scheduler first
    $taskName = "HushClaw"
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task -and $task.State -eq "Running") {
        Write-Info "Stopping HushClaw scheduled task…"
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Write-Ok "Scheduled task stopped"
        exit 0
    }
    # Fallback: PID file / process scan
    $runningPid = Get-HushClawPid
    if (-not $runningPid) {
        Die "HushClaw is not running."
    }
    Stop-HushClaw $runningPid
    Write-Ok "HushClaw stopped."
    exit 0
}

# ── Auto-install helpers (winget) ─────────────────────────────────────────────

function Get-WingetCmd {
    # winget is available on Windows 10 1709+ via App Installer
    $wg = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $wg) {
        # Also check common install location
        $wgPath = "$env:LOCALAPPDATA\Microsoft\WindowsApps\winget.exe"
        if (Test-Path $wgPath) { return $wgPath }
    }
    return $wg
}

function Install-PythonAuto {
    Write-Warn "Python 3.11+ not found — attempting auto-install…"
    $wg = Get-WingetCmd
    if (-not $wg) {
        Write-Err "winget is not available on this system."
        Write-Host ""
        Write-Info "Please install Python 3.13 manually:"
        Write-Info "  https://www.python.org/downloads/"
        Write-Info "  ► Tick 'Add Python to PATH' during installation."
        Write-Info "  ► Then re-run this script."
        exit 1
    }
    $installed = $false
    foreach ($pyId in @("Python.Python.3.13", "Python.Python.3.12", "Python.Python.3.11")) {
        Write-Info "Installing $pyId via winget…"
        & winget install -e --id $pyId --silent `
            --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "$pyId installed"
            $installed = $true
            break
        }
    }
    if (-not $installed) {
        Write-Err "Automatic Python installation failed."
        Write-Info "Download manually: https://www.python.org/downloads/"
        exit 1
    }
    Refresh-EnvPath
}

function Install-GitAuto {
    Write-Warn "Git not found — attempting auto-install…"
    $wg = Get-WingetCmd
    if (-not $wg) {
        Write-Err "winget is not available on this system."
        Write-Info "Download Git from: https://git-scm.com/download/win"
        exit 1
    }
    Write-Info "Installing Git.Git via winget…"
    & winget install -e --id Git.Git --silent `
        --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Git installation failed."
        Write-Info "Download manually: https://git-scm.com/download/win"
        exit 1
    }
    Write-Ok "Git installed"
    Refresh-EnvPath
}

# ── Python detection ──────────────────────────────────────────────────────────
function Find-Python {
    # Check py launcher first (standard Windows Python Launcher, resolves to actual exe)
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        try {
            $exePath = & py -3 -c "import sys; print(sys.executable)" 2>$null
            $verStr  = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($exePath -and $verStr) {
                $parts = $verStr.Trim().Split(".")
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
                    return $exePath.Trim()
                }
            }
        } catch {}
    }
    # Fall back to named commands
    foreach ($cmd in @("python3.13", "python3.12", "python3.11", "python3", "python")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            try {
                $verStr = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
                $parts  = $verStr.Trim().Split(".")
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 11) {
                    return $found.Source
                }
            } catch {}
        }
    }
    return $null
}

Write-Section "Checking Python"
$PythonExe = Find-Python

if (-not $PythonExe) {
    Install-PythonAuto
    # Re-scan after install (winget adds to PATH; current session needs refresh)
    $PythonExe = Find-Python
    if (-not $PythonExe) {
        Write-Warn "Python installed but not yet visible in this session."
        Write-Warn "Please open a NEW terminal window and re-run this script."
        exit 1
    }
}

$pyVer = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
Write-Ok "Found Python $pyVer at $PythonExe"

# ── Git detection ─────────────────────────────────────────────────────────────
Write-Section "Checking Git"
$GitExe = Get-Command git -ErrorAction SilentlyContinue
if (-not $GitExe) {
    Install-GitAuto
    $GitExe = Get-Command git -ErrorAction SilentlyContinue
    if (-not $GitExe) {
        Write-Warn "Git installed but not yet visible in this session."
        Write-Warn "Please open a NEW terminal window and re-run this script."
        exit 1
    }
}
Write-Ok "Git $((git --version) -replace 'git version ','')"

# ── Process Check ─────────────────────────────────────────────────────────────
Write-Section "Process Check"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# Check Task Scheduler task first
$taskName = "HushClaw"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$runningPid   = Get-HushClawPid

if ($Mode -eq "start") {
    if (($existingTask -and $existingTask.State -eq "Running") -or $runningPid) {
        Write-Warn "HushClaw is already running — nothing to do."
        exit 0
    }
} elseif ($existingTask -and $existingTask.State -eq "Running") {
    Write-Info "Stopping running scheduled task for $Mode…"
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Write-Ok "Scheduled task stopped"
} elseif ($runningPid) {
    Write-Info "Stopping running server (PID $runningPid) for $Mode…"
    Stop-HushClaw $runningPid
} else {
    Write-Ok "No running HushClaw instance detected"
}

# ── Install / Update ──────────────────────────────────────────────────────────
if ($Mode -eq "start") {
    if (-not (Test-Path "$InstallDir")) {
        Die "HushClaw not found at $InstallDir. Run without -StartOnly to install first."
    }
} else {
    Write-Section "Installing HushClaw → $InstallDir"

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

    $RepoDir = "$InstallDir\repo"
    if (Test-Path "$RepoDir\.git") {
        Write-Info "Updating repository…"
        git -C $RepoDir fetch --quiet origin
        git -C $RepoDir reset --hard origin/main --quiet 2>$null
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

    $PipExe = "$VenvDir\Scripts\pip.exe"
    $GcExe  = "$VenvDir\Scripts\hushclaw.exe"

    Write-Info "Installing/upgrading packages…"
    & $PipExe install --upgrade pip --quiet
    # Use Push-Location so pip sees ".[server]" with no path quoting issues
    Push-Location $RepoDir
    & $PipExe install -e ".[server]" --quiet
    Pop-Location
    Write-Ok "HushClaw installed"

    # ── Add hushclaw to user PATH ────────────────────────────────────────────
    Write-Section "Setting Up PATH"

    $ScriptsDir = "$VenvDir\Scripts"
    $currentUserPath = [Environment]::GetEnvironmentVariable("PATH", "User")

    if ($currentUserPath -split ";" | Where-Object { $_ -eq $ScriptsDir }) {
        Write-Ok "'hushclaw' is already in user PATH"
    } else {
        $newPath = if ($currentUserPath) { "$currentUserPath;$ScriptsDir" } else { $ScriptsDir }
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
        # Also update current session
        $env:PATH = "$env:PATH;$ScriptsDir"
        Write-Ok "Added to user PATH: $ScriptsDir"
        Write-Warn "PATH updated — open a NEW terminal window to use 'hushclaw' command globally."
    }

    # Create a batch launcher
    $LauncherBat = "$InstallDir\hushclaw-start.bat"
    @"
@echo off
set HUSHCLAW_PORT=$Port
set HUSHCLAW_HOST=$BindHost
"$GcExe" serve --host %HUSHCLAW_HOST% --port %HUSHCLAW_PORT% %*
"@ | Set-Content $LauncherBat -Encoding ASCII
    Write-Ok "Launcher created: $LauncherBat"
}

$GcExe = "$InstallDir\venv\Scripts\hushclaw.exe"
if (-not (Test-Path $GcExe)) {
    Die "hushclaw.exe not found at $GcExe. Installation may have failed."
}

# ── Sync bundled skill packages → skill_dir ───────────────────────────────────
if ($Mode -ne "start") {
    Write-Section "Syncing Bundled Skills"

    $RepoSkills = "$InstallDir\repo\skill-packages"
    $SkillDir   = "$env:LOCALAPPDATA\hushclaw\skills"

    if (Test-Path $RepoSkills) {
        New-Item -ItemType Directory -Force -Path $SkillDir | Out-Null
        $synced = 0
        foreach ($pkg in Get-ChildItem $RepoSkills -Directory) {
            $dest = "$SkillDir\$($pkg.Name)"
            if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
            Copy-Item $pkg.FullName -Destination $dest -Recurse
            $synced++
        }
        Write-Ok "$synced bundled skill package(s) synced → $SkillDir"
    }
}

# ── Firewall: open port ───────────────────────────────────────────────────────
Write-Section "Firewall"

$RuleName = "HushClaw-Port-$Port"
$existingRule = Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue
if ($existingRule) {
    Write-Ok "Windows Firewall: rule '$RuleName' already exists (port $Port)"
} else {
    Write-Info "Adding Windows Firewall inbound rule for port $Port…"
    try {
        New-NetFirewallRule `
            -DisplayName $RuleName `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $Port `
            -Action Allow `
            -ErrorAction Stop | Out-Null
        Write-Ok "Windows Firewall: port $Port opened (rule: $RuleName)"
    } catch {
        Write-Warn "Could not add firewall rule (may need to run as Administrator)."
        Write-Info "To open the port manually, run as Admin:"
        Write-Info "  New-NetFirewallRule -DisplayName '$RuleName' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow"
    }
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
Write-Warn "Tip: On first launch the browser opens the Settings modal to configure your API key."
Write-Warn "     Use the Settings button (top-right) to adjust Model, Channels, System, or Memory settings."
Write-Host ""

# ── Open browser ──────────────────────────────────────────────────────────────
function Open-Browser($url) {
    if ($NoBrowser) { return }
    $hasDisplay = [System.Environment]::UserInteractive
    if (-not $hasDisplay) {
        Write-Warn "Non-interactive session detected — browser auto-open skipped."
        return
    }
    Start-Job -ScriptBlock {
        param($u)
        Start-Sleep -Seconds 2
        Start-Process $u
    } -ArgumentList $url | Out-Null
}

# ── Start server ──────────────────────────────────────────────────────────────
Write-Section "Starting HushClaw Server"
Write-Host "  Listening on http://${BindHost}:${Port}" -ForegroundColor Cyan
Write-Host ""

if ($Foreground) {
    Write-Warn "Running in foreground mode (Ctrl-C to stop)"
    Open-Browser "http://127.0.0.1:$Port"
    & $GcExe serve --host $BindHost --port $Port
} else {
    Start-HushClawBackground $GcExe
    Open-Browser "http://127.0.0.1:$Port"
    Write-Ok "Installation complete. HushClaw is running in the background."
}
