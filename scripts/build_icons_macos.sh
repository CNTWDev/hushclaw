#!/usr/bin/env bash
set -euo pipefail

# Render every product icon from the single SVG source of truth. This script
# uses macOS system tools so release assets remain reproducible without a
# checked-in graphics toolchain.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_SVG="$ROOT_DIR/hushclaw/web/icon.svg"
WEB_DIR="$ROOT_DIR/hushclaw/web"
MACOS_DIR="$ROOT_DIR/assets/macos"
TEMP_DIR="$(mktemp -d /private/tmp/hushclaw-icons.XXXXXX)"

cleanup() {
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "build_icons_macos.sh requires macOS." >&2
  exit 1
fi

mkdir -p "$MACOS_DIR" "$TEMP_DIR/render" "$TEMP_DIR/HushClaw.iconset"
qlmanage -t -s 1024 -o "$TEMP_DIR/render" "$SOURCE_SVG" >/dev/null
MASTER_PNG="$TEMP_DIR/render/icon.svg.png"

sips -z 512 512 "$MASTER_PNG" --out "$WEB_DIR/favicon-512.png" >/dev/null
sips -z 192 192 "$MASTER_PNG" --out "$WEB_DIR/favicon-192.png" >/dev/null
sips -z 32 32 "$MASTER_PNG" --out "$WEB_DIR/favicon-32.png" >/dev/null
sips -s format ico "$WEB_DIR/favicon-32.png" --out "$WEB_DIR/favicon.ico" >/dev/null

make_icon() {
  local size="$1"
  local name="$2"
  sips -z "$size" "$size" "$MASTER_PNG" --out "$TEMP_DIR/HushClaw.iconset/$name" >/dev/null
}

make_icon 16 icon_16x16.png
make_icon 32 icon_16x16@2x.png
make_icon 32 icon_32x32.png
make_icon 64 icon_32x32@2x.png
make_icon 128 icon_128x128.png
make_icon 256 icon_128x128@2x.png
make_icon 256 icon_256x256.png
make_icon 512 icon_256x256@2x.png
make_icon 512 icon_512x512.png
make_icon 1024 icon_512x512@2x.png

iconutil -c icns "$TEMP_DIR/HushClaw.iconset" -o "$MACOS_DIR/HushClaw.icns"
echo "Updated Web icons and $MACOS_DIR/HushClaw.icns"
