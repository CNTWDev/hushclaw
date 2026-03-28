#!/usr/bin/env bash
# Sync / browse skills from the ClawHub registry into clawhub/ (git-ignored).
# Usage:
#   ./scripts/sync_clawhub.sh search "keyword"
#   ./scripts/sync_clawhub.sh install <slug>
#   ./scripts/sync_clawhub.sh explore
#   ./scripts/sync_clawhub.sh update
#   ./scripts/sync_clawhub.sh list
#
# Skills land in:  <repo-root>/clawhub/<slug>/
# Lockfile at:     <repo-root>/clawhub/.clawhub/lock.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILLS_DIR="$REPO_ROOT/clawhub"

mkdir -p "$SKILLS_DIR"

CMD="${1:-help}"
shift || true

CLAWHUB_OPTS=(--workdir "$SKILLS_DIR" --dir .)

case "$CMD" in
  search)
    clawhub "${CLAWHUB_OPTS[@]}" search "$@"
    ;;
  install)
    clawhub "${CLAWHUB_OPTS[@]}" install "$@"
    echo ""
    echo "Installed to: $SKILLS_DIR/$1"
    ;;
  update)
    clawhub "${CLAWHUB_OPTS[@]}" update --all
    ;;
  explore)
    clawhub "${CLAWHUB_OPTS[@]}" explore "$@"
    ;;
  inspect)
    clawhub inspect "$@"
    ;;
  list)
    clawhub "${CLAWHUB_OPTS[@]}" list
    ;;
  uninstall)
    clawhub "${CLAWHUB_OPTS[@]}" uninstall "$@"
    ;;
  help|*)
    echo "Usage: $0 <command> [args]"
    echo ""
    echo "  search <query>    Vector-search ClawHub registry"
    echo "  install <slug>    Install a skill into clawhub/"
    echo "  update            Update all installed skills"
    echo "  explore           Browse latest updated skills"
    echo "  inspect <slug>    Preview metadata + files without installing"
    echo "  list              List installed skills (from lockfile)"
    echo "  uninstall <slug>  Remove an installed skill"
    ;;
esac
