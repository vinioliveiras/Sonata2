#!/usr/bin/env bash
# Screenshot a component in preview mode inside a headless Wayland compositor
# (sway, pixman renderer) -- real alpha for popovers, unlike Xvfb.
#   tools/wl-preview.sh dock [--dark|--light] [--label N]  -> screenshots/*.png
# PREVIEW_APPS="org.gnome.Console foo.bar" opens test windows with those
# app_ids first (tests/fake_app.py), to check running-app dots.
# Needs: sway, grim, GTK4 + libadwaita for python3.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
comp="$1"; shift
name="$comp$(printf '%s' "$*" | tr -c 'a-z0-9' '-' | tr -s '-')"; name="${name%-}"
mkdir -p "$REPO/screenshots"
out="$REPO/screenshots/$name.png"
PY="${PYTHON:-python3}"
# SONATA_SESSION_ENV=1: preview with the session look (GTK_THEME etc.)
[ -n "${SONATA_SESSION_ENV:-}" ] && source "$REPO/tools/session-env.sh" 2>/dev/null
run="$(mktemp -d)"; chmod 700 "$run"
cfg="$run/sway.conf"
cat > "$cfg" <<CONF
output HEADLESS-1 resolution ${PREVIEW_SIZE:-960x260} bg #000000 solid_color
default_border none
default_floating_border none
for_window [title="Sonata 2 preview"] floating enable, move position 0 0
CONF
export PREVIEW_SIZE="${PREVIEW_SIZE:-960x260}"
export XDG_RUNTIME_DIR="$run" WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1
sway -c "$cfg" >"$run/sway.log" 2>&1 & sway_pid=$!
for _ in $(seq 50); do sock=$(ls "$run"/wayland-* 2>/dev/null | grep -v lock | head -1) && [ -n "$sock" ] && break; sleep 0.1; done
export WAYLAND_DISPLAY="$(basename "$sock")"
fakes=""
for id in ${PREVIEW_APPS:-}; do GDK_BACKEND=wayland "$PY" "$REPO/tests/fake_app.py" "$id" 2>/dev/null & fakes="$fakes $!"; done
[ -n "${PREVIEW_APPS:-}" ] && sleep 1
(cd "$REPO" && GDK_BACKEND=wayland "$PY" -m sonata2 "$comp" --preview "$@" 2>"$run/app.log") & app=$!
sleep "${PREVIEW_WAIT:-2.5}"; [ -n "${PROBE:-}" ] && "$PY" $PROBE; cat "$run"/app.log >&2 || true
grim "$out"
kill $app $sway_pid 2>/dev/null || true; kill $fakes 2>/dev/null || true
rm -rf "$run"
echo "$out"
