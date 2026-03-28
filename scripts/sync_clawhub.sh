#!/usr/bin/env bash
# Sync / browse skills from the ClawHub registry into clawhub/ (git-ignored).
# Usage:
#   ./scripts/sync_clawhub.sh search "keyword"
#   ./scripts/sync_clawhub.sh install <slug>
#   ./scripts/sync_clawhub.sh explore
#   ./scripts/sync_clawhub.sh update
#   ./scripts/sync_clawhub.sh list
#   ./scripts/sync_clawhub.sh stage <slug>   # normalize + audit before adding to skill-packages/
#
# Skills land in:      <repo-root>/clawhub/<slug>/
# Staged skills go to: <repo-root>/skill-packages/staging/<slug>/  (git-ignored)
# Lockfile at:         <repo-root>/clawhub/.clawhub/lock.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SKILLS_DIR="$REPO_ROOT/clawhub"
STAGING_DIR="$REPO_ROOT/skill-packages/staging"

mkdir -p "$SKILLS_DIR"

CMD="${1:-help}"
shift || true

CLAWHUB_OPTS=(--workdir "$SKILLS_DIR" --dir .)

# ---------------------------------------------------------------------------
# stage <slug> — copy from clawhub/ to skill-packages/staging/, normalize,
# and print an audit checklist for human review before final promotion.
# ---------------------------------------------------------------------------
_stage() {
  local slug="${1:-}"
  if [[ -z "$slug" ]]; then
    echo "Usage: $0 stage <slug>"
    exit 1
  fi

  local src="$SKILLS_DIR/$slug"
  if [[ ! -d "$src" ]]; then
    echo "Error: '$src' not found. Run: $0 install $slug"
    exit 1
  fi

  local dst="$STAGING_DIR/$slug"
  mkdir -p "$STAGING_DIR"

  # Fresh copy
  rm -rf "$dst"
  cp -r "$src" "$dst"

  local skill_md="$dst/SKILL.md"
  if [[ ! -f "$skill_md" ]]; then
    echo "Error: No SKILL.md found in $src"
    exit 1
  fi

  echo ""
  echo "=== Stage: $slug ==="
  echo "Source:  $src"
  echo "Staging: $dst"
  echo ""

  # ── Automatic normalizations ──────────────────────────────────────────────

  # 1. Rename command-tool: → direct_tool: (HushClaw alias)
  if grep -q "^command-tool:" "$skill_md" 2>/dev/null; then
    sed -i.bak 's/^command-tool:/direct_tool:/' "$skill_md"
    rm -f "${skill_md}.bak"
    echo "[auto-fixed]  command-tool: → direct_tool:"
  fi

  # 2. Remove agents/ directory (OpenClaw provider routing, not used by HushClaw)
  if [[ -d "$dst/agents" ]]; then
    rm -rf "$dst/agents"
    echo "[auto-removed] agents/ (OpenClaw-only provider config)"
  fi

  echo ""
  echo "=== Audit checklist ==="

  local issues=0

  # ── metadata: JSON present (new-style OpenClaw requires) ─────────────────
  if grep -q "^metadata:" "$skill_md" 2>/dev/null; then
    local meta_line
    meta_line="$(grep "^metadata:" "$skill_md" | head -1)"
    echo "[info] metadata JSON detected — HushClaw loader now parses this natively."
    echo "       $meta_line"
    # Extract os hint if present
    if echo "$meta_line" | grep -q '"os"'; then
      echo "       ^ contains 'os' restriction — loader will enforce platform check automatically."
    fi
  fi

  # ── scripts/*.py — must be manually reviewed before running ───────────────
  local py_scripts
  py_scripts="$(find "$dst/scripts" -name "*.py" 2>/dev/null || true)"
  if [[ -n "$py_scripts" ]]; then
    echo ""
    echo "[REVIEW REQUIRED] Python scripts found — read before promoting:"
    while IFS= read -r f; do
      echo "  - ${f#"$dst/"}"
    done <<< "$py_scripts"
    echo "  → These run via 'run_shell'; check for network calls, file writes, or"
    echo "    credential access. Consider wrapping as a native tools/*.py tool instead."
    issues=$((issues + 1))
  fi

  # ── assets/*.html — check for external CDN / tracking ────────────────────
  local html_assets
  html_assets="$(find "$dst/assets" -name "*.html" 2>/dev/null || true)"
  if [[ -n "$html_assets" ]]; then
    echo ""
    echo "[REVIEW REQUIRED] HTML assets found — check for external CDN / tracking:"
    while IFS= read -r f; do
      echo "  - ${f#"$dst/"}"
      # Quick scan for common CDN / tracking patterns
      local cdn_hits
      cdn_hits="$(grep -oE 'https?://[^"'"'"' >]+' "$f" 2>/dev/null | grep -v "localhost" | head -5 || true)"
      if [[ -n "$cdn_hits" ]]; then
        echo "    External URLs:"
        while IFS= read -r url; do
          echo "      $url"
        done <<< "$cdn_hits"
      fi
    done <<< "$html_assets"
    issues=$((issues + 1))
  fi

  # ── references/*.md — suggest include_files ──────────────────────────────
  local ref_files
  ref_files="$(find "$dst/references" -name "*.md" 2>/dev/null || true)"
  if [[ -n "$ref_files" ]]; then
    echo ""
    echo "[SUGGEST] references/ docs found — consider adding to SKILL.md front-matter:"
    local refs_list=""
    while IFS= read -r f; do
      local rel="${f#"$dst/"}"
      echo "  - $rel"
      if [[ -z "$refs_list" ]]; then
        refs_list="\"$rel\""
      else
        refs_list="$refs_list, \"$rel\""
      fi
    done <<< "$ref_files"
    echo ""
    echo "  Add to SKILL.md front-matter (between the --- markers):"
    echo "    include_files: [$refs_list]"
    echo "  HushClaw will inline these files into the skill context automatically."
  fi

  # ── Unrecognised top-level directories ───────────────────────────────────
  local extra_dirs
  extra_dirs="$(find "$dst" -mindepth 1 -maxdepth 1 -type d \
    ! -name "scripts" ! -name "assets" ! -name "references" ! -name "tools" \
    2>/dev/null || true)"
  if [[ -n "$extra_dirs" ]]; then
    echo ""
    echo "[info] Other directories (not automatically handled):"
    while IFS= read -r d; do
      echo "  - ${d#"$dst/"}"
    done <<< "$extra_dirs"
  fi

  # ── Summary ───────────────────────────────────────────────────────────────
  echo ""
  echo "=== Summary ==="
  if [[ $issues -eq 0 ]]; then
    echo "No blocking issues found."
  else
    echo "$issues item(s) require manual review (see above)."
  fi
  echo ""
  echo "Next steps:"
  echo "  1. Review the staged skill:  $dst"
  echo "  2. Edit SKILL.md if needed (add include_files, adjust description, etc.)"
  echo "  3. When satisfied, promote to skill-packages/:"
  echo "     cp -r \"$dst\" \"$REPO_ROOT/skill-packages/$slug\""
  echo "     git add skill-packages/$slug"
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

case "$CMD" in
  search)
    clawhub "${CLAWHUB_OPTS[@]}" search "$@"
    ;;
  install)
    clawhub "${CLAWHUB_OPTS[@]}" install "$@"
    echo ""
    echo "Installed to: $SKILLS_DIR/${1:-}"
    echo "Next: run '$0 stage ${1:-}' to normalize and audit before promoting."
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
  stage)
    _stage "$@"
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
    echo "  stage <slug>      Normalize + audit a clawhub/ skill before promoting"
    echo "                    to skill-packages/. Output goes to skill-packages/staging/"
    ;;
esac
