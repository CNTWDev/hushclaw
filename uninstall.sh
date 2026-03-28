#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# HushClaw Uninstaller  —  macOS & Linux
#
# Usage:
#   bash uninstall.sh              # interactive uninstall (asks for confirmation)
#   bash uninstall.sh --keep-data  # remove program files only, keep memory/config
#   bash uninstall.sh -y           # skip confirmation prompts (non-interactive)
#   bash uninstall.sh -y --keep-data
#
# Environment overrides:
#   HUSHCLAW_HOME=<dir>   installation directory  (default: ~/.hushclaw)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

INSTALL_DIR="${HUSHCLAW_HOME:-$HOME/.hushclaw}"
PID_FILE="$INSTALL_DIR/hushclaw.pid"

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
skip()    { echo -e "${BLUE}  ○${NC}  $*"; }
section() { echo -e "\n${BOLD}${BLUE}══ $* ${NC}"; }

# ── Parse args ────────────────────────────────────────────────────────────────
KEEP_DATA=false
YES=false

for arg in "$@"; do
  case "$arg" in
    --keep-data) KEEP_DATA=true ;;
    -y|--yes)    YES=true ;;
    --help|-h)
      echo "Usage: $0 [--keep-data] [-y]"
      echo "  (no flag)    Interactive uninstall — asks before each destructive step"
      echo "  --keep-data  Remove program files only; preserve memory, config, and notes"
      echo "  -y           Skip all confirmation prompts"
      exit 0
      ;;
    *) echo "Unknown argument: $arg. Use --help for usage." >&2; exit 1 ;;
  esac
done

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
echo -e "  ${BOLD}HushClaw Uninstaller${NC}"
echo ""

# ── OS detection ──────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Darwin) OS_NAME="macOS" ;;
  Linux)  OS_NAME="Linux" ;;
  *)      echo "Unsupported OS: $OS" >&2; exit 1 ;;
esac

# ── Data directory paths (mirror loader.py) ───────────────────────────────────
if [[ "$OS_NAME" == "macOS" ]]; then
  DATA_DIR="$HOME/Library/Application Support/hushclaw"
  CONFIG_DIR="$HOME/Library/Application Support/hushclaw"
else
  DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hushclaw"
  CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hushclaw"
fi

# ── Summary of what will be removed ──────────────────────────────────────────
section "What will be removed"

echo -e "  ${BOLD}Program files:${NC}"
echo -e "    ${CYAN}$INSTALL_DIR${NC}  (repo + venv + launcher + logs)"
echo -e "    ${CYAN}$HOME/.local/bin/hushclaw${NC}  (symlink)"

if [[ "$KEEP_DATA" == false ]]; then
  echo ""
  echo -e "  ${BOLD}Data & config:${NC}"
  if [[ "$DATA_DIR" == "$CONFIG_DIR" ]]; then
    echo -e "    ${CYAN}$DATA_DIR${NC}  (memory.db, notes/, skills/, browser/, hushclaw.toml)"
  else
    echo -e "    ${CYAN}$DATA_DIR${NC}  (memory.db, notes/, skills/, browser/)"
    echo -e "    ${CYAN}$CONFIG_DIR${NC}  (hushclaw.toml)"
  fi
else
  echo ""
  echo -e "  ${BOLD}Preserved (--keep-data):${NC}"
  echo -e "    ${CYAN}$DATA_DIR${NC}"
  [[ "$DATA_DIR" != "$CONFIG_DIR" ]] && echo -e "    ${CYAN}$CONFIG_DIR${NC}"
fi

echo ""

# ── Confirmation ──────────────────────────────────────────────────────────────
if [[ "$YES" == false ]]; then
  if [[ "$KEEP_DATA" == false ]]; then
    warn "This will permanently delete HushClaw including all memory, notes, and config."
  else
    warn "This will remove HushClaw program files. Your memory, notes, and config will be kept."
  fi
  echo ""
  read -r -p "  Are you sure? Type 'yes' to continue: " CONFIRM
  echo ""
  if [[ "$CONFIRM" != "yes" ]]; then
    echo "  Aborted."
    exit 0
  fi
fi

# ── Step 1: Stop running server ───────────────────────────────────────────────
section "Stopping Server"

stop_server() {
  local pid="$1"

  # systemd (Linux)
  if [[ "${OS_NAME:-}" == "Linux" ]] && command -v systemctl &>/dev/null; then
    if [[ "$(id -u)" -eq 0 ]] && systemctl is-active --quiet hushclaw 2>/dev/null; then
      info "Stopping systemd system service…"
      systemctl stop hushclaw 2>/dev/null || true
      systemctl disable hushclaw 2>/dev/null || true
      rm -f /etc/systemd/system/hushclaw.service
      systemctl daemon-reload 2>/dev/null || true
      ok "System service stopped and removed"
      return
    fi
    if systemctl --user is-active --quiet hushclaw 2>/dev/null; then
      info "Stopping systemd user service…"
      systemctl --user stop hushclaw 2>/dev/null || true
      systemctl --user disable hushclaw 2>/dev/null || true
      rm -f "$HOME/.config/systemd/user/hushclaw.service"
      systemctl --user daemon-reload 2>/dev/null || true
      ok "User service stopped and removed"
      return
    fi
  fi

  if [[ -n "$pid" ]]; then
    info "Stopping HushClaw (PID $pid)…"
    kill -SIGTERM "$pid" 2>/dev/null || true
    local i=0
    while kill -0 "$pid" 2>/dev/null && (( i++ < 20 )); do sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -SIGKILL "$pid" 2>/dev/null || true
    ok "Server stopped"
  fi
}

RUNNING_PID=""
if [[ -f "$PID_FILE" ]]; then
  RUNNING_PID=$(cat "$PID_FILE" 2>/dev/null || true)
  if ! kill -0 "$RUNNING_PID" 2>/dev/null; then
    RUNNING_PID=""
  fi
fi
if [[ -z "$RUNNING_PID" ]]; then
  RUNNING_PID=$(pgrep -f "hushclaw serve" 2>/dev/null | head -1 || true)
fi

if [[ -n "$RUNNING_PID" ]]; then
  stop_server "$RUNNING_PID"
else
  skip "No running HushClaw instance found"
fi

# ── Step 2: Remove symlink ────────────────────────────────────────────────────
section "Removing Command Symlink"

SYMLINK="$HOME/.local/bin/hushclaw"
if [[ -L "$SYMLINK" ]]; then
  rm -f "$SYMLINK"
  ok "Removed $SYMLINK"
else
  skip "Symlink not found at $SYMLINK"
fi

# ── Step 3: Remove PATH lines from shell rc files ────────────────────────────
section "Cleaning Shell Config"

clean_rc() {
  local rc="$1"
  if [[ ! -f "$rc" ]]; then return; fi
  # Remove the two lines we added: '# HushClaw' and the export PATH line
  if grep -qF '# HushClaw' "$rc" 2>/dev/null; then
    # Use a temp file to edit in-place portably (macOS sed -i needs extension)
    local tmp
    tmp=$(mktemp)
    grep -v '# HushClaw' "$rc" \
      | grep -v 'export PATH=.*\.local/bin.*PATH' \
      > "$tmp" || true
    # Only overwrite if content actually changed
    if ! diff -q "$tmp" "$rc" >/dev/null 2>&1; then
      cp "$tmp" "$rc"
      ok "Cleaned PATH entry from $rc"
    fi
    rm -f "$tmp"
  else
    skip "No HushClaw PATH entry found in $rc"
  fi
}

for rc in "$HOME/.zshrc" "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
  clean_rc "$rc"
done

# ── Step 4: Remove firewall rules (Linux) ────────────────────────────────────
if [[ "$OS_NAME" == "Linux" ]]; then
  section "Removing Firewall Rules"

  remove_fw() {
    local port="$1"
    if command -v ufw &>/dev/null; then
      local ufw_status
      ufw_status=$(ufw status 2>/dev/null)
      if echo "$ufw_status" | grep -q "Status: active"; then
        if echo "$ufw_status" | grep -qw "$port"; then
          ufw delete allow "$port/tcp" >/dev/null 2>&1 || true
          ok "ufw: removed rule for port $port"
          return
        fi
      fi
    fi
    if command -v firewall-cmd &>/dev/null; then
      if firewall-cmd --state 2>/dev/null | grep -q "running"; then
        firewall-cmd --permanent --remove-port="${port}/tcp" >/dev/null 2>&1 || true
        firewall-cmd --reload >/dev/null 2>&1 || true
        ok "firewalld: removed rule for port $port"
        return
      fi
    fi
    skip "No active ufw/firewalld found — skipping firewall cleanup"
  }

  PORT="${HUSHCLAW_PORT:-8765}"
  remove_fw "$PORT"
fi

# ── Step 5: Remove installation directory ────────────────────────────────────
section "Removing Program Files"

if [[ -d "$INSTALL_DIR" ]]; then
  rm -rf "$INSTALL_DIR"
  ok "Removed $INSTALL_DIR"
else
  skip "Installation directory not found: $INSTALL_DIR"
fi

# ── Step 6: Remove data / config (unless --keep-data) ────────────────────────
if [[ "$KEEP_DATA" == false ]]; then
  section "Removing Data & Config"

  if [[ -d "$DATA_DIR" ]]; then
    rm -rf "$DATA_DIR"
    ok "Removed $DATA_DIR"
  else
    skip "Data directory not found: $DATA_DIR"
  fi

  if [[ "$DATA_DIR" != "$CONFIG_DIR" && -d "$CONFIG_DIR" ]]; then
    rm -rf "$CONFIG_DIR"
    ok "Removed $CONFIG_DIR"
  fi
else
  section "Data & Config (preserved)"
  skip "Skipped $DATA_DIR  (--keep-data)"
  [[ "$DATA_DIR" != "$CONFIG_DIR" ]] && skip "Skipped $CONFIG_DIR  (--keep-data)"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}  HushClaw has been uninstalled.${NC}"
echo ""
if [[ "$KEEP_DATA" == true ]]; then
  echo -e "  Your memory, notes, and config are still at:"
  echo -e "  ${CYAN}$DATA_DIR${NC}"
  echo ""
fi
echo -e "  To reinstall later:"
echo -e "  ${CYAN}bash <(curl -fsSL https://raw.githubusercontent.com/CNTWDev/hushclaw/master/install.sh)${NC}"
echo ""
