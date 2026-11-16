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
# Window frame (corners, traffic lights) from the tokens' frame -- one place
# for every window: the title bars Wayfire draws follow Sonata's own
# windows. Also grid's crossfade off with sonata-resize, the old Mission
# Control and screenshot keys migrated, XWayland's XSETTINGS manager.
sonata2-rs wayfire-config frame "$out" "$here" || true
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
# The options changed in Settings, on top (old key names migrated in the
# overrides file too).
if [ -f "$over" ]; then
    sonata2-rs wayfire-config overrides "$out" "$over"
fi
# Sonata's commands run its binary by name (`sonata2 x` -> `sonata2-rs x`):
# whatever `sonata2` on the PATH is -- a Python Sonata's launcher left
# behind by an older install -- the keys, the supervisor and the login
# items are this Sonata. The XSETTINGS line of a wayfire.ini kept from the
# Python Sonata the same (the frame step rewrites it too).
sed -i -E 's/(^|[=[:space:]])sonata2 /\1sonata2-rs /g' "$out"
sed -i -E 's/^(xwayland_startup_script *= *)env PYTHONPATH=[^ ]* python3 -m sonata2\.xsettings$/\1sonata2-rs xsettings/' "$out"
# Title-bar pictures named by another place: Sonata's data lived inside
# its Python package (sonata2/data/), and a build once wrote the tree it
# was built from (Settings' title-bar colours, in wayfire-overrides.ini; a
# binary built elsewhere left the window buttons invisible). Whatever
# folder a wayfire.ini or the overrides name, this Sonata's data/decor is
# used. Only the resolved copy changes; your files stay as they are.
sed -i -E "s#[^ =]*/data/decor/#$here/data/decor/#g" "$out"
