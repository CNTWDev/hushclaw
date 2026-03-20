#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# HushClaw Installer  —  macOS & Linux
#
# Usage:
#   bash install.sh              # install + start in background
#   bash install.sh --update     # stop old process, update, restart in background
#   bash install.sh --start-only # skip install, start existing installation in background
#   bash install.sh --stop       # stop the running server and exit
#   bash install.sh --foreground # install + start in foreground (debug mode)
#
# Environment overrides:
#   HUSHCLAW_HOME=<dir>   installation directory  (default: ~/.hushclaw)
#   HUSHCLAW_PORT=<port>  server port             (default: 8765)
#   HUSHCLAW_HOST=<host>  bind address            (default: 0.0.0.0)
#   HUSHCLAW_NO_BROWSER=1 skip browser auto-open
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO_URL="https://github.com/CNTWDev/hushclaw.git"
INSTALL_DIR="${HUSHCLAW_HOME:-$HOME/.hushclaw}"
PORT="${HUSHCLAW_PORT:-8765}"
BIND="${HUSHCLAW_HOST:-0.0.0.0}"
NO_BROWSER="${HUSHCLAW_NO_BROWSER:-}"

PID_FILE="$INSTALL_DIR/hushclaw.pid"
LOG_FILE="$INSTALL_DIR/hushclaw.log"

# ── Terminal colours ──────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

info()    { echo -e "${CYAN}  ▸${NC}  $*"; }
ok()      { echo -e "${GREEN}  ✓${NC}  $*"; }
warn()    { echo -e "${YELLOW}  !${NC}  $*"; }
error()   { echo -e "${RED}  ✗${NC}  $*" >&2; }
die()     { error "$*"; exit 1; }
section() { echo -e "\n${BOLD}${BLUE}══ $* ${NC}"; }

# ── Parse args ────────────────────────────────────────────────────────────────
MODE="install"
FOREGROUND=false
for arg in "$@"; do
  case "$arg" in
    --update)     MODE="update" ;;
    --start-only) MODE="start"  ;;
    --stop)       MODE="stop"   ;;
    --foreground) FOREGROUND=true ;;
    --help|-h)
      echo "Usage: $0 [--update | --start-only | --stop | --foreground]"
      echo "  (no flag)    Install HushClaw and start server in background"
      echo "  --update     Stop old process, pull latest code, restart in background"
      echo "  --start-only Skip install, start existing installation in background"
      echo "  --stop       Stop the running HushClaw server and exit"
      echo "  --foreground Install and start server in foreground (debug mode)"
      exit 0
      ;;
    *) die "Unknown argument: $arg. Use --help for usage." ;;
  esac
done

# ── Process management helpers ────────────────────────────────────────────────

find_running_pid() {
  # 1. Check PID file first, verify process is alive
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "$pid"
      return
    fi
    rm -f "$PID_FILE"   # stale PID file
  fi
  # 2. Fallback: scan by process name (handles cross-script restarts)
  pgrep -f "hushclaw serve" 2>/dev/null | head -1 || true
}

stop_server() {
  local pid="$1"
  info "Stopping HushClaw (PID $pid)…"
  kill -SIGTERM "$pid" 2>/dev/null || true
  local i=0
  while kill -0 "$pid" 2>/dev/null && (( i++ < 20 )); do sleep 0.5; done
  if kill -0 "$pid" 2>/dev/null; then
    kill -SIGKILL "$pid" 2>/dev/null || true
    warn "Force-killed PID $pid"
  else
    ok "Server stopped gracefully"
  fi
  rm -f "$PID_FILE"
}

# ── --stop mode: early exit ───────────────────────────────────────────────────
if [[ "$MODE" == "stop" ]]; then
  pid=$(find_running_pid)
  if [[ -z "$pid" ]]; then
    die "HushClaw is not running."
  fi
  stop_server "$pid"
  ok "HushClaw stopped."
  exit 0
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
cat <<'EOF'
    __  __           __    ________
   / / / /_  _______/ /_  / ____/ /___ __      __
  / /_/ / / / / ___/ __ \/ /   / / __ `/ | /| / /
 / __  / /_/ (__  ) / / / /___/ / /_/ /| |/ |/ /
/_/ /_/\__,_/____/_/ /_/\____/_/\__,_/ |__/|__/
EOF
echo -e "${NC}"
echo -e "  ${BOLD}Lightweight AI Agent Framework with Persistent Memory${NC}"
echo -e ""
echo -e "  ${BLUE}───────────────────────────────────────────────────────${NC}"
echo -e "  ${CYAN}https://github.com/CNTWDev/hushclaw${NC}  ${BLUE}·${NC}  tuanweishi@gmail.com"
echo -e "  ${BLUE}───────────────────────────────────────────────────────${NC}"
echo -e ""

# ── OS detection ──────────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin)  OS_NAME="macOS" ;;
  Linux)   OS_NAME="Linux" ;;
  *)       die "Unsupported OS: $OS. Use install.ps1 for Windows." ;;
esac
info "Platform: ${BOLD}$OS_NAME${NC} ($ARCH)"

# ── Linux: show distro info ───────────────────────────────────────────────────
if [[ "$OS_NAME" == "Linux" ]] && [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  PRETTY_NAME=""
  source /etc/os-release 2>/dev/null || true
  [[ -n "${PRETTY_NAME:-}" ]] && info "Distro:   ${BOLD}${PRETTY_NAME}${NC}"
fi

# ── Linux: detect package manager ─────────────────────────────────────────────
PKG_MGR=""
if [[ "$OS_NAME" == "Linux" ]]; then
  if   command -v apt-get &>/dev/null; then PKG_MGR="apt"
  elif command -v dnf     &>/dev/null; then PKG_MGR="dnf"
  elif command -v pacman  &>/dev/null; then PKG_MGR="pacman"
  elif command -v zypper  &>/dev/null; then PKG_MGR="zypper"
  fi
  [[ -n "$PKG_MGR" ]] && info "Package manager: ${BOLD}${PKG_MGR}${NC}"
fi

# ── Privilege wrapper: use sudo only when not already root ────────────────────
run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo &>/dev/null; then
    sudo "$@"
  else
    die "Root privileges required but 'sudo' not found. Run as root or install sudo."
  fi
}

# ── Headless detection ────────────────────────────────────────────────────────
is_headless() {
  [[ "$OS_NAME" == "Linux" && -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]
}

# ── Helpers: auto-install dependencies ────────────────────────────────────────

# macOS: ensure Homebrew is present (installs silently if missing)
# Returns 0 on success, 1 if install failed (e.g. no sudo) — caller decides what to do.
ensure_homebrew() {
  if command -v brew &>/dev/null; then
    ok "Homebrew $(brew --version 2>/dev/null | head -1)"
    return 0
  fi
  info "Homebrew not found — attempting install (requires admin rights)…"
  if ! NONINTERACTIVE=1 /bin/bash -c \
      "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
      </dev/null 2>&1; then
    warn "Homebrew install failed (no sudo access?). Will try to continue without it."
    return 1
  fi
  # Activate brew in the current shell
  if   [[ -x /opt/homebrew/bin/brew ]]; then eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew    ]]; then eval "$(/usr/local/bin/brew shellenv)"
  fi
  ok "Homebrew installed"
  return 0
}

# macOS: install Python via Homebrew
install_python_macos() {
  info "Installing Python 3.13 via Homebrew…"
  brew install python@3.13 --quiet
  # Homebrew Python is keg-only; add to PATH explicitly
  local brew_python
  brew_python="$(brew --prefix python@3.13 2>/dev/null)/bin"
  export PATH="$brew_python:$PATH"
  ok "Python 3.13 installed"
}

# Linux: install Python via the detected package manager
install_python_linux() {
  info "Installing Python 3.11+ via ${PKG_MGR}…"
  case "$PKG_MGR" in
    apt)
      run_as_root apt-get update -y -qq
      # Probe available versions: try installing each, stop on first success
      local installed=false
      for v in 3.13 3.12 3.11; do
        if run_as_root apt-get install -y -qq --no-install-recommends \
             "python${v}" "python${v}-venv" 2>/dev/null; then
          installed=true
          ok "Python ${v} installed"
          break
        fi
      done
      if [[ "$installed" == false ]]; then
        # Fall back to deadsnakes PPA (Ubuntu/Debian)
        warn "python3.11–3.13 not in default apt repos — adding deadsnakes PPA…"
        run_as_root apt-get install -y -qq --no-install-recommends \
          software-properties-common 2>/dev/null || true
        if command -v add-apt-repository &>/dev/null; then
          run_as_root add-apt-repository -y ppa:deadsnakes/ppa
          run_as_root apt-get update -y -qq
          run_as_root apt-get install -y -qq --no-install-recommends \
            python3.11 python3.11-venv
          ok "Python 3.11 (deadsnakes) installed"
        else
          die "Cannot add deadsnakes PPA. Please install Python 3.11+ manually: https://www.python.org/downloads/"
        fi
      fi
      ;;
    dnf)
      run_as_root dnf install -y python3.11 python3.11-devel 2>/dev/null || \
        run_as_root dnf install -y python3
      ;;
    pacman)
      run_as_root pacman -Sy --noconfirm python
      ;;
    zypper)
      run_as_root zypper install -y python311 2>/dev/null || \
        run_as_root zypper install -y python3
      ;;
    *)
      die "Cannot auto-install Python: no supported package manager found.\nPlease install Python 3.11+ manually from https://www.python.org/downloads/"
      ;;
  esac
}

# macOS: install Git via Homebrew
install_git_macos() {
  info "Installing Git via Homebrew…"
  brew install git --quiet
  ok "Git installed"
}

# Linux: install Git via the detected package manager
install_git_linux() {
  info "Installing Git via ${PKG_MGR}…"
  case "$PKG_MGR" in
    apt)    run_as_root apt-get install -y -qq --no-install-recommends git ;;
    dnf)    run_as_root dnf install -y git ;;
    pacman) run_as_root pacman -Sy --noconfirm git ;;
    zypper) run_as_root zypper install -y git ;;
    *)      die "Cannot auto-install Git: no supported package manager found." ;;
  esac
  ok "Git installed"
}

# Linux: ensure curl is available (needed for public IP detection)
ensure_curl_linux() {
  command -v curl &>/dev/null && return
  info "Installing curl…"
  case "$PKG_MGR" in
    apt)    run_as_root apt-get install -y -qq --no-install-recommends curl ;;
    dnf)    run_as_root dnf install -y --quiet curl ;;
    pacman) run_as_root pacman -Sy --noconfirm curl ;;
    zypper) run_as_root zypper install -y curl ;;
    *)      warn "curl not found; public IP detection skipped"; return ;;
  esac
  ok "curl installed"
}

# ── Step 1: Python ────────────────────────────────────────────────────────────
# On macOS we check for an existing Python first; Homebrew is only installed
# when Python is actually missing.  This lets users without sudo admin rights
# complete the install if Python is already present (e.g. from python.org).
section "Checking Python"

PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3; do
  if command -v "$cmd" &>/dev/null; then
    major=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null) || continue
    minor=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || continue
    if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
      PYTHON="$cmd"
      ok "Found Python $("$cmd" --version 2>&1 | awk '{print $2}') at $(command -v "$cmd")"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  warn "Python 3.11+ not found — installing automatically…"
  if [[ "$OS_NAME" == "macOS" ]]; then
    # Need Homebrew to install Python on macOS
    section "Checking Homebrew"
    if ensure_homebrew; then
      install_python_macos
      # Re-scan after installation
      for cmd in python3.13 python3.12 python3.11 python3; do
        if command -v "$cmd" &>/dev/null; then
          major=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null) || continue
          minor=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || continue
          if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON="$cmd"
            ok "Using Python $("$cmd" --version 2>&1 | awk '{print $2}')"
            break
          fi
        fi
      done
    fi
    [[ -n "$PYTHON" ]] || die "Python 3.11+ not found and could not be installed automatically.\nPlease install it from https://www.python.org/downloads/ then re-run this script."
  else
    install_python_linux
    # Re-scan after installation (also check /usr/bin directly for apt installs)
    for cmd in python3.13 python3.12 python3.11 python3 \
               /usr/bin/python3.13 /usr/bin/python3.12 /usr/bin/python3.11; do
      if command -v "$cmd" &>/dev/null 2>&1 || [[ -x "$cmd" ]]; then
        major=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null) || continue
        minor=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || continue
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
          PYTHON="$cmd"
          ok "Using Python $("$cmd" --version 2>&1 | awk '{print $2}')"
          break
        fi
      fi
    done
    [[ -n "$PYTHON" ]] || die "Python 3.11+ installation failed. Please install it manually from https://www.python.org/downloads/"
  fi
fi

# ── Step 2: Git ───────────────────────────────────────────────────────────────
section "Checking Git"

if command -v git &>/dev/null; then
  ok "Git $(git --version | awk '{print $3}')"
else
  warn "Git not found — installing automatically…"
  if [[ "$OS_NAME" == "macOS" ]]; then
    install_git_macos
  else
    install_git_linux
  fi
  command -v git &>/dev/null || die "Git installation failed. Please install it manually."
  ok "Git $(git --version | awk '{print $3}')"
fi

# ── Process Check ─────────────────────────────────────────────────────────────
section "Process Check"
mkdir -p "$INSTALL_DIR"
RUNNING_PID=$(find_running_pid)
if [[ -n "$RUNNING_PID" ]]; then
  if [[ "$MODE" == "start" ]]; then
    warn "HushClaw is already running (PID $RUNNING_PID)."
    ok "Server is up — nothing to do."
    exit 0
  else
    info "Stopping running server (PID $RUNNING_PID) before ${MODE}…"
    stop_server "$RUNNING_PID"
  fi
else
  ok "No running HushClaw instance detected"
fi

# ── Install / Update ──────────────────────────────────────────────────────────
if [[ "$MODE" == "start" ]]; then
  [[ -d "$INSTALL_DIR" ]] || die "HushClaw not found at $INSTALL_DIR. Run without --start-only to install first."
else
  section "Installing HushClaw → $INSTALL_DIR"

  mkdir -p "$INSTALL_DIR"

  if [[ -d "$INSTALL_DIR/repo/.git" ]]; then
    if [[ "$MODE" == "update" ]] || [[ "$MODE" == "install" ]]; then
      info "Updating repository…"
      (cd "$INSTALL_DIR/repo" && git fetch --quiet origin)
      (cd "$INSTALL_DIR/repo" && git reset --hard origin/main --quiet 2>/dev/null \
        || git reset --hard origin/master --quiet)
      ok "Repository updated"
    fi
  else
    info "Cloning repository…"
    git clone --depth=1 "$REPO_URL" "$INSTALL_DIR/repo" --quiet
    ok "Repository cloned"
  fi

  # ── Ensure python3.X-venv is installed on apt systems ────────────────────
  # Ubuntu/Debian ship python3.X without venv support by default; the
  # -venv package must be installed separately even for the system Python.
  if [[ "$OS_NAME" == "Linux" && "$PKG_MGR" == "apt" ]]; then
    if ! "$PYTHON" -c "import ensurepip" &>/dev/null 2>&1; then
      PY_VER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
      info "Installing python${PY_VER}-venv (required for venv support on Debian/Ubuntu)…"
      run_as_root apt-get install -y -qq --no-install-recommends "python${PY_VER}-venv"
      ok "python${PY_VER}-venv installed"
    fi
  fi

  # ── Virtual environment ────────────────────────────────────────────────────
  # Recreate venv if it doesn't exist or is broken (e.g. pip missing after a failed install)
  if [[ ! -x "$INSTALL_DIR/venv/bin/pip" ]]; then
    [[ -d "$INSTALL_DIR/venv" ]] && { warn "Broken venv detected — recreating…"; rm -rf "$INSTALL_DIR/venv"; }
  fi

  if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    info "Creating virtual environment…"
    if "$PYTHON" -m venv "$INSTALL_DIR/venv" 2>/tmp/_hushclaw_venv_err; then
      ok "Virtual environment created"
    else
      warn "Standard venv failed: $(cat /tmp/_hushclaw_venv_err 2>/dev/null | head -1)"
      # Clean up any partial directory before retrying
      rm -rf "$INSTALL_DIR/venv"
      info "Retrying without pip (will bootstrap separately)…"
      "$PYTHON" -m venv --without-pip "$INSTALL_DIR/venv"
      # Bootstrap pip via ensurepip or get-pip.py
      if "$INSTALL_DIR/venv/bin/python" -m ensurepip --upgrade 2>/dev/null; then
        ok "pip bootstrapped via ensurepip"
      elif command -v curl &>/dev/null; then
        curl -fsSL https://bootstrap.pypa.io/get-pip.py \
          | "$INSTALL_DIR/venv/bin/python" --quiet
        ok "pip bootstrapped via get-pip.py"
      else
        die "Cannot bootstrap pip. Try: apt-get install python3-pip"
      fi
    fi
    rm -f /tmp/_hushclaw_venv_err
  fi

  info "Installing/upgrading packages…"
  "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
  "$INSTALL_DIR/venv/bin/pip" install -e "$INSTALL_DIR/repo[server]" --quiet
  ok "HushClaw installed"

  # ── Create helper launcher scripts ────────────────────────────────────────
  LAUNCHER="$INSTALL_DIR/hushclaw-start.sh"
  cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
# HushClaw quick-start launcher
export HUSHCLAW_PORT="\${HUSHCLAW_PORT:-$PORT}"
export HUSHCLAW_HOST="\${HUSHCLAW_HOST:-$BIND}"
exec "$INSTALL_DIR/venv/bin/hushclaw" serve --host "\$HUSHCLAW_HOST" --port "\$HUSHCLAW_PORT" "\$@"
LAUNCHER_EOF
  chmod +x "$LAUNCHER"

  # ── Sync bundled skill packages → skill_dir ───────────────────────────────
  # skill-packages/ in the repo are not loaded until copied into the
  # runtime skill_dir (mirrors the config loader's default path logic).
  REPO_SKILLS="$INSTALL_DIR/repo/skill-packages"
  if [[ "$OS_NAME" == "macOS" ]]; then
    SKILL_DIR="$HOME/Library/Application Support/hushclaw/skills"
  else
    SKILL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hushclaw/skills"
  fi

  if [[ -d "$REPO_SKILLS" ]]; then
    section "Syncing Bundled Skills"
    mkdir -p "$SKILL_DIR"
    synced=0
    for pkg in "$REPO_SKILLS"/*/; do
      [[ -d "$pkg" ]] || continue
      name=$(basename "$pkg")
      rm -rf "$SKILL_DIR/$name"
      cp -r "$pkg" "$SKILL_DIR/$name"
      synced=$((synced + 1))
    done
    ok "$synced bundled skill package(s) synced → $SKILL_DIR"
  fi

fi

# ── Add hushclaw to PATH ────────────────────────────────────────────────
if [[ "$MODE" != "start" ]]; then
  section "Setting Up PATH"

  LOCAL_BIN="$HOME/.local/bin"
  mkdir -p "$LOCAL_BIN"

  # 1. Create symlink (always, overwrite old)
  if ln -sf "$INSTALL_DIR/venv/bin/hushclaw" "$LOCAL_BIN/hushclaw" 2>/dev/null; then
    ok "'hushclaw' command → $LOCAL_BIN/hushclaw"
  else
    warn "Could not create symlink in $LOCAL_BIN"
  fi

  # 2. Check if ~/.local/bin is already in PATH
  PATH_ENTRY='export PATH="$HOME/.local/bin:$PATH"'
  NEEDS_EXPORT=false
  if ! echo "$PATH" | tr ':' '\n' | grep -qxF "$LOCAL_BIN"; then
    NEEDS_EXPORT=true
  fi

  # 3. Write to shell rc file (idempotent)
  add_to_shell_rc() {
    local rc="$1"
    if [[ -f "$rc" ]]; then
      if grep -qF '.local/bin' "$rc"; then
        ok "PATH already configured in $rc"
      else
        echo "" >> "$rc"
        echo '# HushClaw' >> "$rc"
        echo "$PATH_ENTRY" >> "$rc"
        ok "Added ~/.local/bin to PATH in $rc"
      fi
    fi
  }

  if [[ "$NEEDS_EXPORT" == true ]]; then
    case "${SHELL:-}" in
      */zsh)  add_to_shell_rc "$HOME/.zshrc" ;;
      */bash) add_to_shell_rc "$HOME/.bashrc"; add_to_shell_rc "$HOME/.bash_profile" ;;
      *)
        add_to_shell_rc "$HOME/.zshrc"
        add_to_shell_rc "$HOME/.bashrc"
        ;;
    esac
    warn "PATH updated. Run:  source ~/.zshrc  (or ~/.bashrc)  — or open a new terminal."
  else
    ok "'hushclaw' is already available in PATH"
  fi
fi

# ── Firewall: open port ───────────────────────────────────────────────────────
if [[ "$OS_NAME" == "Linux" ]]; then
  section "Firewall"

  open_firewall_port() {
    local port="$1"

    if command -v ufw &>/dev/null; then
      local ufw_status
      ufw_status=$(ufw status 2>/dev/null)
      if echo "$ufw_status" | grep -q "Status: active"; then
        if echo "$ufw_status" | grep -qw "$port"; then
          ok "ufw: port $port already open"
        else
          info "Opening port $port in ufw…"
          run_as_root ufw allow "$port/tcp" >/dev/null
          ok "ufw: port $port opened"
        fi
        return
      fi
    fi

    if command -v firewall-cmd &>/dev/null; then
      if firewall-cmd --state 2>/dev/null | grep -q "running"; then
        if firewall-cmd --list-ports 2>/dev/null | grep -qw "$port/tcp"; then
          ok "firewalld: port $port already open"
        else
          info "Opening port $port in firewalld…"
          run_as_root firewall-cmd --permanent --add-port="$port/tcp" >/dev/null
          run_as_root firewall-cmd --reload >/dev/null
          ok "firewalld: port $port opened"
        fi
        return
      fi
    fi

    if command -v iptables &>/dev/null; then
      if iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
        ok "iptables: port $port already open"
      else
        info "Opening port $port in iptables…"
        run_as_root iptables -A INPUT -p tcp --dport "$port" -j ACCEPT
        ok "iptables: port $port opened"
        warn "iptables rules are not persisted across reboots."
        warn "Install iptables-persistent to save rules permanently:"
        warn "  apt-get install iptables-persistent"
      fi
      return
    fi

    warn "No active firewall detected — skipping port configuration."
    warn "If using a cloud provider (Aliyun / AWS / GCP), open port $port"
    warn "in the security group / firewall rules of your instance."
  }

  open_firewall_port "$PORT"
fi

# ── Network info ──────────────────────────────────────────────────────────────
section "Network Addresses"

# Ensure curl is available for public IP fetch (Linux)
if [[ "$OS_NAME" == "Linux" ]]; then
  ensure_curl_linux
fi

# Local LAN IP
LOCAL_IP=""
if [[ "$OS_NAME" == "macOS" ]]; then
  for iface in en0 en1 en2 utun0; do
    ip=$(ipconfig getifaddr "$iface" 2>/dev/null || true)
    if [[ -n "$ip" && "$ip" != "127."* ]]; then
      LOCAL_IP="$ip"; break
    fi
  done
else
  # Try multiple methods in order of reliability
  LOCAL_IP=$(
    ip -4 route get 1.1.1.1 2>/dev/null \
      | awk '/src/{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' \
    || ip -4 addr show scope global 2>/dev/null \
      | awk '/inet /{split($2,a,"/"); print a[1]; exit}' \
    || hostname -I 2>/dev/null | awk '{print $1}' \
    || true
  )
fi

# Public IP (best-effort, non-blocking)
PUBLIC_IP=""
if command -v curl &>/dev/null; then
  PUBLIC_IP=$(curl -s --connect-timeout 4 https://api.ipify.org 2>/dev/null || true)
fi

echo ""
echo -e "  ${BOLD}${GREEN}●  Local (this machine)${NC}"
echo -e "     ${CYAN}http://127.0.0.1:${PORT}${NC}"

if [[ -n "$LOCAL_IP" ]]; then
  echo ""
  echo -e "  ${BOLD}${GREEN}●  LAN (same network)${NC}"
  echo -e "     ${CYAN}http://${LOCAL_IP}:${PORT}${NC}"
fi

if [[ -n "$PUBLIC_IP" ]]; then
  echo ""
  echo -e "  ${BOLD}${YELLOW}●  Internet (public IP — only if port $PORT is open in firewall)${NC}"
  echo -e "     ${CYAN}http://${PUBLIC_IP}:${PORT}${NC}"
fi

echo ""
warn "Tip: On first launch the browser opens the ${BOLD}Settings modal${NC} to configure your API key."
warn "     Use the ${BOLD}⚙ Settings${NC} button at any time to adjust Model, Channels, System, or Memory settings."
echo ""

# ── Background launch helpers ─────────────────────────────────────────────────

start_with_nohup() {
  mkdir -p "$INSTALL_DIR"
  nohup "$INSTALL_DIR/venv/bin/hushclaw" serve \
    --host "$BIND" --port "$PORT" \
    >> "$LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" > "$PID_FILE"
  ok "Server started in background (PID $pid)"
  info "Logs: $LOG_FILE"
  info "Stop: bash install.sh --stop"
}

start_with_systemd() {
  local service_name="hushclaw"
  local hushclaw_bin="$INSTALL_DIR/venv/bin/hushclaw"

  if [[ "$(id -u)" -eq 0 ]]; then
    # System-wide service
    local service_file="/etc/systemd/system/${service_name}.service"
    cat > "$service_file" <<SERVICE_EOF
[Unit]
Description=HushClaw AI Agent Server
After=network.target

[Service]
Type=simple
ExecStart=${hushclaw_bin} serve --host ${BIND} --port ${PORT}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE_EOF
    systemctl daemon-reload
    systemctl enable --now "$service_name"
    ok "HushClaw registered as system service and started"
    info "Check status: systemctl status $service_name"
    info "View logs:    journalctl -u $service_name -f"
    info "Stop:         systemctl stop $service_name"
  else
    # User-level service (no root required)
    local user_systemd_dir="$HOME/.config/systemd/user"
    local service_file="$user_systemd_dir/${service_name}.service"
    mkdir -p "$user_systemd_dir"
    cat > "$service_file" <<SERVICE_EOF
[Unit]
Description=HushClaw AI Agent Server
After=network.target

[Service]
Type=simple
ExecStart=${hushclaw_bin} serve --host ${BIND} --port ${PORT}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
SERVICE_EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$service_name"
    # Allow service to survive user logout
    loginctl enable-linger "$USER" 2>/dev/null || true
    ok "HushClaw registered as user service and started"
    info "Check status: systemctl --user status $service_name"
    info "View logs:    journalctl --user -u $service_name -f"
    info "Stop:         systemctl --user stop $service_name"
  fi
}

start_background() {
  if [[ "$OS_NAME" == "Linux" ]] && command -v systemctl &>/dev/null; then
    start_with_systemd
  else
    start_with_nohup
  fi
}

# ── Open browser ──────────────────────────────────────────────────────────────
open_browser() {
  local url="$1"
  # Skip if explicitly disabled or on a headless Linux server
  if [[ -n "$NO_BROWSER" ]]; then return; fi
  if is_headless; then
    warn "Headless server detected — browser auto-open skipped."
    warn "Connect from a client machine using the addresses above."
    return
  fi
  # Wait briefly for the server to bind
  sleep 1.5
  if [[ "$OS_NAME" == "macOS" ]]; then
    open "$url" 2>/dev/null &
  elif command -v xdg-open &>/dev/null; then
    xdg-open "$url" 2>/dev/null &
  elif command -v sensible-browser &>/dev/null; then
    sensible-browser "$url" 2>/dev/null &
  fi
}

# ── Start server ──────────────────────────────────────────────────────────────
section "Starting HushClaw Server"
echo -e "  Listening on ${CYAN}http://${BIND}:${PORT}${NC}\n"

if [[ "$FOREGROUND" == true ]]; then
  warn "Running in foreground mode (Ctrl-C to stop)"
  open_browser "http://127.0.0.1:${PORT}" &
  exec "$INSTALL_DIR/venv/bin/hushclaw" serve \
    --host "$BIND" \
    --port "$PORT"
else
  start_background
  # Open browser after background server starts
  open_browser "http://127.0.0.1:${PORT}" &
  ok "Installation complete. HushClaw is running in the background."
fi
