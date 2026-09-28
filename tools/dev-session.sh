#!/usr/bin/env bash
# Try Sonata 2 without logging out: runs Wayfire as a window inside your
# current desktop (GNOME/KDE on Wayland or X11) with config/wayfire.ini,
# a wallpaper, the Dock from this repo and a terminal.
#   tools/dev-session.sh                 # default wallpaper
#   SONATA2_WALLPAPER=~/Pictures/x.jpg tools/dev-session.sh
# Close the Wayfire window (or Ctrl+C here) to stop. Needs: wayfire;
# optional: python-pillow (generated sample wallpaper; or pick one in
# System Settings > Wallpaper).
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
# (the session's `sonata2 ...` commands become this clone's python -m sonata2;
# no dbus-update-activation-environment here: it would leak into GNOME)
sed -e '/^\[autostart\]/,$d' -e "s|= sonata2 |= env PYTHONPATH=$REPO python3 -m sonata2 |" \
    "$REPO/config/wayfire.ini" > "$run/wayfire.ini"
{
    echo "[autostart]"
    echo "autostart_wf_shell = false"
    echo "wallpaper = env PYTHONPATH='$REPO' python3 -m sonata2 wallpaper"
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

# Sample wallpaper only if none is set (Sonata's dconf layer, see session-env.sh).
cur="$(gsettings get org.gnome.desktop.background picture-uri 2>/dev/null)"
if [ -n "$wall" ] && { [ -z "$cur" ] || [ "$cur" = "''" ]; }; then
    keep="$HOME/.config/sonata2/sample-wallpaper.png"; cp "$wall" "$keep"
    gsettings set org.gnome.desktop.background picture-uri "file://$keep" 2>/dev/null || true
fi
echo "Wayfire config: $run/wayfire.ini"
[ -z "$wall" ] && echo "(no wallpaper: install python-pillow or set SONATA2_WALLPAPER to see the glass)"
wayfire -c "$run/wayfire.ini"
