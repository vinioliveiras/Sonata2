#!/usr/bin/env bash
# wayfire-config.sh <in.ini> <out.ini>: the session's Wayfire config with
# @SONATA@ replaced by this Sonata folder, the pixdecor plugin swapped in for
# "decoration" when it is installed (else Wayfire's own title bars), and the
# options changed in Sonata's Settings (~/.config/sonata2/wayfire-overrides.ini)
# applied on top.
set -euo pipefail
in="$1" out="$2"
here="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
pix=""
for d in "${XDG_DATA_HOME:-$HOME/.local/share}/wayfire/plugin-manager/install/lib/wayfire" \
         $(pkg-config --variable=plugindir wayfire 2>/dev/null) /usr/lib/wayfire /usr/lib64/wayfire \
         /usr/local/lib/wayfire /usr/lib/x86_64-linux-gnu/wayfire; do
    [ -f "$d/libpixdecor.so" ] && { pix=1; break; }
done
sed -e "s|@SONATA@|$here|g" "$in" > "$out"
if [ -n "$pix" ]; then
    sed -i -E '/^plugins *=/ s/(^| )decoration( |$)/\1pixdecor\2/' "$out"
fi
# Apps asking for focus (xdg-activation: a link from a chat app raises the
# browser -- Vini's links opened in Chrome behind the web app). Also for a
# wayfire.ini kept from before (its plugins line edited by hand).
if ! grep -qE '^plugins *=.*(^| )xdg-activation( |$)' "$out"; then
    sed -i -E '/^plugins *=/ s/$/ xdg-activation/' "$out"
fi
# Sonata's own plugin (install.sh builds it): rounded corners for windows
# Wayfire decorates. Loaded only when built, like pixdecor.
corners="${XDG_DATA_HOME:-$HOME/.local/share}/wayfire/plugin-manager/install/lib/wayfire/libsonata-corners.so"
if [ -f "$corners" ]; then
    sed -i -E '/^plugins *=/ s/$/ sonata-corners/' "$out"
fi
# Sonata's resize (Wayfire's, plus "Show window contents while resizing"):
# in place of "resize" once built (it reads resize's own options)
sresize="${XDG_DATA_HOME:-$HOME/.local/share}/wayfire/plugin-manager/install/lib/wayfire/libsonata-resize.so"
if [ -f "$sresize" ]; then
    sed -i -E '/^plugins *=/ s/(^| )resize( |$)/\1sonata-resize\2/' "$out"
fi
# Privileged protocols (screen capture, window control, clipboard, virtual
# input, overlays) only for Sonata and the portal: sonata-privacy, once built
sprivacy="${XDG_DATA_HOME:-$HOME/.local/share}/wayfire/plugin-manager/install/lib/wayfire/libsonata-privacy.so"
if [ -f "$sprivacy" ] && ! grep -qE '^plugins *=.*(^| )sonata-privacy( |$)' "$out"; then
    sed -i -E '/^plugins *=/ s/$/ sonata-privacy/' "$out"
fi
# Plugins this Wayfire doesn't have (Fedora's 0.10 has no ext-toplevel): left
# out, not a load error at every login. Only when the plugin folders are
# known (some plugin found there), so nothing is dropped by mistake.
pdirs=("${XDG_DATA_HOME:-$HOME/.local/share}/wayfire/plugin-manager/install/lib/wayfire"
       $(pkg-config --variable=plugindir wayfire 2>/dev/null || true) /usr/lib/wayfire /usr/lib64/wayfire
       /usr/local/lib/wayfire /usr/lib/x86_64-linux-gnu/wayfire
       "$HOME"/.local/opt/sonata-wayfire/lib*/wayfire "$HOME"/.local/opt/sonata-wayfire/lib/*/wayfire)
has_plugin() {
    local d
    for d in "${pdirs[@]}"; do
        if [ -f "$d/lib$1.so" ]; then return 0; fi
    done
    return 1
}
if has_plugin autostart; then
    keep=""
    for p in $(sed -n -E 's/^plugins *= *//p' "$out" | head -n1); do
        if has_plugin "$p"; then keep="$keep $p"; fi
    done
    sed -i -E "0,/^plugins *=.*/ s//plugins =$keep/" "$out"
fi
# Window frame (corners, traffic lights) from tokens.FRAME -- one place for
# every window: the title bars Wayfire draws follow Sonata's own windows.
PYTHONPATH="$here" python3 - "$out" <<'PY' || true
import importlib.util, os, sys
from sonata2 import wfconfig
spec = importlib.util.spec_from_file_location("tokens", os.path.join(os.environ["PYTHONPATH"], "sonata2", "ui", "tokens.py"))
tokens = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tokens)
wfconfig._wayfire_files = lambda: [sys.argv[1]]       # write only the resolved copy
for sec, key, val in wfconfig.frame_options(tokens.frame()):     # the user's radius
    wfconfig.wayfire_set(sec, key, val)
PY
over="${XDG_CONFIG_HOME:-$HOME/.config}/sonata2/wayfire-overrides.ini"
# Keyboard: the system's layout (localectl) until one is picked in Settings.
if ! grep -qs '^xkb_layout' "$over" && command -v localectl >/dev/null; then
    st="$(localectl status 2>/dev/null || true)"
    lay="$(printf '%s\n' "$st" | sed -n 's/^ *X11 Layout: *//p')"
    var="$(printf '%s\n' "$st" | sed -n 's/^ *X11 Variant: *//p')"
    mdl="$(printf '%s\n' "$st" | sed -n 's/^ *X11 Model: *//p')"
    if [ -n "$lay" ]; then
        sed -i "0,/^\[input\]/s//[input]\nxkb_layout = $lay${var:+\nxkb_variant = $var}${mdl:+\nxkb_model = $mdl}/" "$out"
    fi
fi
if [ -f "$over" ]; then
    PYTHONPATH="$here" python3 - "$out" "$over" <<'PY'
import sys
from sonata2 import wfconfig as system
out, over = sys.argv[1], sys.argv[2]
system._wayfire_files = lambda: [out]          # write only the resolved copy
sec = None
for line in open(over, encoding="utf-8"):
    s = line.strip()
    if s.startswith("[") and s.endswith("]"):
        sec = s[1:-1]
    elif sec and "=" in s and not s.startswith("#"):
        k, v = s.split("=", 1)
        system.wayfire_set(sec, k.strip(), v.strip())
PY
fi
