#!/usr/bin/env bash
# Screenshot a component in preview mode inside a headless Wayland compositor
# (sway, pixman renderer) -- real alpha for popovers, unlike Xvfb.
#   tools/wl-preview.sh dock [--dark|--light] [--label N]  -> screenshots/*.png
# Needs: sway, grim, GTK4 + libadwaita for python3.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
comp="$1"; shift
name="$comp$(printf '%s' "$*" | tr -c 'a-z0-9' '-' | tr -s '-')"; name="${name%-}"
mkdir -p "$REPO/screenshots"
out="$REPO/screenshots/$name.png"
PY="${PYTHON:-python3}"
run="$(mktemp -d)"; chmod 700 "$run"
cfg="$run/sway.conf"
cat > "$cfg" <<CONF
output HEADLESS-1 resolution 960x260 bg #000000 solid_color
default_border none
for_window [title="Sonata 2 preview"] floating enable, move position 0 0
CONF
export XDG_RUNTIME_DIR="$run" WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1
sway -c "$cfg" >"$run/sway.log" 2>&1 & sway_pid=$!
for _ in $(seq 50); do sock=$(ls "$run"/wayland-* 2>/dev/null | grep -v lock | head -1) && [ -n "$sock" ] && break; sleep 0.1; done
export WAYLAND_DISPLAY="$(basename "$sock")"
(cd "$REPO" && GDK_BACKEND=wayland "$PY" -m sonata2 "$comp" --preview "$@" 2>"$run/app.log") & app=$!
sleep 2.5; cat "$run"/app.log >&2 || true
grim "$out"
kill $app $sway_pid 2>/dev/null || true
rm -rf "$run"
echo "$out"
