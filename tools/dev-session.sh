#!/usr/bin/env bash
# Try Sonata 2 without logging out: runs Wayfire as a window inside your
# current desktop (GNOME/KDE on Wayland or X11) with config/wayfire.ini,
# a wallpaper, the Dock from this repo and a terminal.
#   tools/dev-session.sh                 # default wallpaper
#   SONATA2_WALLPAPER=~/Pictures/x.jpg tools/dev-session.sh
# Close the Wayfire window (or Ctrl+C here) to stop. Needs: wayfire;
# optional: swaybg (wallpaper), python-pillow (generated wallpaper).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
source "$REPO/tools/session-env.sh"     # themes, cursors, GTK_THEME for apps started inside
command -v wayfire >/dev/null || { echo "wayfire is not installed (sudo pacman -S wayfire)"; exit 1; }

run="$(mktemp -d)"
trap 'rm -rf "$run"' EXIT
wall="${SONATA2_WALLPAPER:-}"
if [ -z "$wall" ] && python3 -c "import PIL" 2>/dev/null; then
    wall="$run/wall.png"
    PYTHONPATH="$REPO" python3 - "$wall" <<'PY'
import shutil, sys
from sonata2.shell.preview import _wallpaper
shutil.copy(_wallpaper(1600, 1000, False)[0], sys.argv[1])
PY
fi

term=""
for t in kgx gnome-terminal konsole kitty alacritty foot ghostty xfce4-terminal; do
    command -v "$t" >/dev/null && { term="$t"; break; }
done

# The repo's config, with autostart pointed at this clone.
sed -e '/^\[autostart\]/,$d' -e "s|\$HOME/GitHub/sonata2|$REPO|g" "$REPO/config/wayfire.ini" > "$run/wayfire.ini"
{
    echo "[autostart]"
    echo "autostart_wf_shell = false"
    [ -n "$wall" ] && command -v swaybg >/dev/null && echo "wallpaper = swaybg -m fill -i '$wall'"
    echo "dock = env PYTHONPATH='$REPO' python3 -m sonata2 dock"
    echo "launchpad = env PYTHONPATH='$REPO' python3 -m sonata2 launchpad --background"
    echo "topbar = env PYTHONPATH='$REPO' python3 -m sonata2 topbar"
    [ -n "$term" ] && echo "terminal = $term"
    echo
    echo "[output:WL-1]"
    echo "mode = 1600x1000"
    echo "[output:X11-1]"
    echo "mode = 1600x1000"
} >> "$run/wayfire.ini"

echo "Wayfire config: $run/wayfire.ini"
[ -z "$wall" ] && echo "(no wallpaper: install python-pillow or set SONATA2_WALLPAPER to see the glass)"
command -v swaybg >/dev/null || echo "(swaybg not installed: no wallpaper -- sudo pacman -S swaybg)"
wayfire -c "$run/wayfire.ini"
