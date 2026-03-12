#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# GhostClaw Installer  —  macOS & Linux
#
# Usage:
#   bash install.sh              # install + start
#   bash install.sh --update     # pull latest code and restart
#   bash install.sh --start-only # skip install, just start server
#
# Environment overrides:
#   GHOSTCLAW_HOME=<dir>   installation directory  (default: ~/.ghostclaw)
#   GHOSTCLAW_PORT=<port>  server port             (default: 8765)
#   GHOSTCLAW_HOST=<host>  bind address            (default: 0.0.0.0)
#   GHOSTCLAW_NO_BROWSER=1 skip browser auto-open
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REPO_URL="https://github.com/CNTWDev/ghostclaw.git"
INSTALL_DIR="${GHOSTCLAW_HOME:-$HOME/.ghostclaw}"
PORT="${GHOSTCLAW_PORT:-8765}"
BIND="${GHOSTCLAW_HOST:-0.0.0.0}"
NO_BROWSER="${GHOSTCLAW_NO_BROWSER:-}"

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
for arg in "$@"; do
  case "$arg" in
    --update)     MODE="update" ;;
    --start-only) MODE="start"  ;;
    --help|-h)
      echo "Usage: $0 [--update | --start-only]"
      echo "  (no flag)    Install GhostClaw and start server"
      echo "  --update     Pull latest code and restart"
      echo "  --start-only Skip install, start existing installation"
      exit 0
      ;;
    *) die "Unknown argument: $arg. Use --help for usage." ;;
  esac
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${BLUE}"
cat <<'EOF'
   _____ _               _      _____ _
  / ____| |             | |    / ____| |
 | |  __| |__   ___  ___| |_  | |    | | __ ___      __
 | | |_ | '_ \ / _ \/ __| __| | |    | |/ _` \ \ /\ / /
 | |__| | | | | (_) \__ \ |_  | |____| | (_| |\ V  V /
  \_____|_| |_|\___/|___/\__|  \_____|_|\__,_| \_/\_/
EOF
echo -e "${NC}"
echo -e "  ${NC}Lightweight AI Agent Framework with Persistent Memory"
echo -e "  ${CYAN}https://github.com/CNTWDev/ghostclaw${NC}"
echo -e ""
echo -e "  ${BLUE}┌─────────────────────────────────────────────────┐${NC}"
echo -e "  ${BLUE}│${NC}  ${BOLD}Created by${NC}  TW  ${BLUE}·${NC}  tuanweishi@gmail.com        ${BLUE}│${NC}"
echo -e "  ${BLUE}└─────────────────────────────────────────────────┘${NC}"
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

# ── Python detection ──────────────────────────────────────────────────────────
section "Checking Python"

PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3; do
  if command -v "$cmd" &>/dev/null; then
    version=$("$cmd" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null)
    major=$("$cmd" -c 'import sys; print(sys.version_info.major)')
    minor=$("$cmd" -c 'import sys; print(sys.version_info.minor)')
    if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
      PYTHON="$cmd"
      ok "Found Python $("$cmd" --version 2>&1 | awk '{print $2}') at $(command -v $cmd)"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  error "Python 3.11+ is required but not found."
  echo ""
  if [[ "$OS_NAME" == "macOS" ]]; then
    warn "Install Python via Homebrew:  ${BOLD}brew install python@3.13${NC}"
    warn "Or download from:             ${BOLD}https://www.python.org/downloads/${NC}"
  else
    warn "Install via your package manager:"
    warn "  Ubuntu/Debian:  ${BOLD}sudo apt install python3.11 python3.11-venv${NC}"
    warn "  Fedora:         ${BOLD}sudo dnf install python3.11${NC}"
    warn "  Arch:           ${BOLD}sudo pacman -S python${NC}"
    warn "Or download from: ${BOLD}https://www.python.org/downloads/${NC}"
  fi
  exit 1
fi

# ── Git detection ─────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
  die "git is required. Install it via your package manager (brew install git / apt install git)."
fi

# ── Install / Update ──────────────────────────────────────────────────────────
if [[ "$MODE" == "start" ]]; then
  [[ -d "$INSTALL_DIR" ]] || die "GhostClaw not found at $INSTALL_DIR. Run without --start-only to install first."
else
  section "Installing GhostClaw → $INSTALL_DIR"

  mkdir -p "$INSTALL_DIR"

  if [[ -d "$INSTALL_DIR/repo/.git" ]]; then
    if [[ "$MODE" == "update" ]] || [[ "$MODE" == "install" ]]; then
      info "Updating repository…"
      git -C "$INSTALL_DIR/repo" fetch --quiet origin
      git -C "$INSTALL_DIR/repo" reset --hard origin/main --quiet 2>/dev/null \
        || git -C "$INSTALL_DIR/repo" reset --hard origin/master --quiet
      ok "Repository updated"
    fi
  else
    info "Cloning repository…"
    git clone --depth=1 "$REPO_URL" "$INSTALL_DIR/repo" --quiet
    ok "Repository cloned"
  fi

  # ── Virtual environment ────────────────────────────────────────────────────
  if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    info "Creating virtual environment…"
    "$PYTHON" -m venv "$INSTALL_DIR/venv"
    ok "Virtual environment created"
  fi

  info "Installing/upgrading packages…"
  "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
  "$INSTALL_DIR/venv/bin/pip" install -e "$INSTALL_DIR/repo[server]" --quiet
  ok "GhostClaw installed"

  # ── Create helper launcher scripts ────────────────────────────────────────
  LAUNCHER="$INSTALL_DIR/ghostclaw-start.sh"
  cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
# GhostClaw quick-start launcher
export GHOSTCLAW_PORT="\${GHOSTCLAW_PORT:-$PORT}"
export GHOSTCLAW_HOST="\${GHOSTCLAW_HOST:-$BIND}"
exec "$INSTALL_DIR/venv/bin/ghostclaw" serve --host "\$GHOSTCLAW_HOST" --port "\$GHOSTCLAW_PORT" "\$@"
LAUNCHER_EOF
  chmod +x "$LAUNCHER"

  # Offer to link into PATH
  BIN_CANDIDATES=("$HOME/.local/bin" "/usr/local/bin")
  LINK_DIR=""
  for d in "${BIN_CANDIDATES[@]}"; do
    if [[ -d "$d" ]] && echo "$PATH" | grep -q "$d"; then
      LINK_DIR="$d"; break
    fi
  done

  if [[ -n "$LINK_DIR" ]]; then
    ln -sf "$INSTALL_DIR/venv/bin/ghostclaw" "$LINK_DIR/ghostclaw" 2>/dev/null && \
      ok "'ghostclaw' command linked to $LINK_DIR/ghostclaw" || \
      warn "Could not symlink to $LINK_DIR (continuing anyway)"
  fi
fi

# ── Network info ──────────────────────────────────────────────────────────────
section "Network Addresses"

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
  LOCAL_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '/src/{print $7; exit}' || hostname -I 2>/dev/null | awk '{print $1}' || true)
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
warn "Tip: On first launch the browser will open a ${BOLD}setup wizard${NC} to configure your API key."
warn "     Press ${BOLD}Ctrl-C${NC} to stop the server."
echo ""

# ── Open browser ──────────────────────────────────────────────────────────────
open_browser() {
  local url="$1"
  if [[ -n "$NO_BROWSER" ]]; then return; fi
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

# Start browser in background before blocking on server
open_browser "http://127.0.0.1:${PORT}" &

# ── Start server (blocking) ───────────────────────────────────────────────────
section "Starting GhostClaw Server"
echo -e "  Listening on ${CYAN}http://${BIND}:${PORT}${NC}  (Ctrl-C to stop)\n"

exec "$INSTALL_DIR/venv/bin/ghostclaw" serve \
  --host "$BIND" \
  --port "$PORT"
