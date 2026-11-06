#!/usr/bin/env bash
# Try Sonata 2 without logging out: runs Wayfire as a window inside your
# current desktop (GNOME/KDE on Wayland or X11) with config/wayfire.ini,
# the Dock, menu bar and Launchpad built from this clone, and a terminal.
#   tools/dev-session.sh                 # builds rust/ (debug) first
#   SONATA2_BIN=/path/to/sonata2-rs tools/dev-session.sh
# Close the Wayfire window (or Ctrl+C here) to stop. Needs: wayfire, cargo.
# The wallpaper is the one set in Sonata's Settings, else Sonata's Mountains.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
command -v wayfire >/dev/null || { echo "wayfire is not installed (sudo pacman -S wayfire)"; exit 1; }
bin="${SONATA2_BIN:-}"
if [ -z "$bin" ]; then
    (cd "$REPO/rust" && cargo build --bin sonata2-rs)
    bin="$REPO/rust/target/debug/sonata2-rs"
fi
bindir="$(mktemp -d)"
# `sonata2-rs` / `sonata2` on the PATH are this build (the config and the
# session's scripts call them by name)
ln -s "$bin" "$bindir/sonata2-rs" && ln -s "$bin" "$bindir/sonata2"
export PATH="$bindir:$PATH" SONATA2_LAUNCHER="$bindir/sonata2"
source "$REPO/tools/session-env.sh"     # themes, cursors, fonts for apps started inside

# Unset what a Sonata/GTK host session may leak into this terminal.
unset GDK_BACKEND
run="$(mktemp -d)"
logs="$REPO/.dev-logs"; mkdir -p "$logs"; rm -f "$logs"/*.log
trap 'rm -rf "${run:?}" "${bindir:?}"' EXIT

term=""
for t in kgx gnome-terminal konsole kitty alacritty foot ghostty xfce4-terminal; do
    command -v "$t" >/dev/null && { term="$t"; break; }
done

# The repo's config, with autostart pointed at this clone's build.
# (no dbus-update-activation-environment here: it would leak into GNOME)
"$REPO/tools/wayfire-config.sh" "$REPO/config/wayfire.ini" "$run/resolved.ini"
# (at the path Settings edits live)
cfg="${XDG_RUNTIME_DIR:-$run}/sonata2-wayfire.ini"
sed -e '/^\[autostart\]/,$d' "$run/resolved.ini" > "$cfg"
{
    echo "[autostart]"
    echo "autostart_wf_shell = false"
    # each component logs to .dev-logs/<name>.log (read them if something is missing)
    for c in wallpaper dock "launchpad --background" "spotlight --background" topbar; do
        n="${c%% *}"
        echo "$n = sh -c '$bin $c > $logs/$n.log 2>&1'"
    done
    [ -n "$term" ] && echo "terminal = $term"
    echo
    echo "[output:WL-1]"
    echo "mode = 1600x1000"
    echo "[output:X11-1]"
    echo "mode = 1600x1000"
} >> "$cfg"
echo "Wayfire config: $cfg"
wayfire -c "$cfg" > "$logs/wayfire.log" 2>&1 || true
echo "Logs: $logs"
