#!/usr/bin/env bash
# build-pixdecor.sh: builds the pixdecor Wayfire plugin (title bars) with
# Sonata's fix for long titles (wayfire-plugin/pixdecor-title.patch) into the
# user's plugin folder, which Wayfire searches before the system's. install.sh
# runs it; run it alone to retry, it prints why a build failed.
set -uo pipefail
SRC="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PLUG_PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/wayfire/plugin-manager/install"
PIXDECOR_COMMIT=5a6f590a368e249b00a71c56e67c1a32a40eb38d
for t in curl patch meson ninja pkg-config; do
    command -v "$t" >/dev/null || { echo "  pixdecor: needs $t"; exit 1; }
done
missing=""
for p in wayfire pango cairo; do
    pkg-config --exists "$p" || missing="$missing $p"
done
# glm: Arch's package ships no pkg-config file, only CMake's -- meson then
# finds it through cmake
if ! pkg-config --exists glm; then
    [ -f /usr/include/glm/glm.hpp ] || missing="$missing glm"
    command -v cmake >/dev/null || missing="$missing cmake"
fi
# wlroots' headers include Vulkan's (Arch: vulkan-headers, not pulled in by wlroots)
[ -f /usr/include/vulkan/vulkan_core.h ] || missing="$missing vulkan-headers"
if [ -n "$missing" ]; then
    echo "  pixdecor: needs the development files of:$missing"
    command -v pacman >/dev/null && echo "  Arch/CachyOS: sudo pacman -S --needed$missing"
    exit 1
fi
pdir="$(mktemp -d)"
log="$pdir.log"
# a failed step shows its errors (compiler warnings would bury them), else the log's end
step() {
    "$@" >>"$log" 2>&1 && return
    echo "  pixdecor didn't build ($1 failed); from $log:"
    if grep -qE "error:|ERROR:" "$log"; then
        grep -E -A3 "error:|ERROR:" "$log" | head -n 40 | sed 's/^/    /'
    else
        tail -n 15 "$log" | sed 's/^/    /'
    fi
    exit 1
}
curl -fsSL "https://codeload.github.com/soreau/pixdecor/tar.gz/$PIXDECOR_COMMIT" -o "$pdir.tgz" >>"$log" 2>&1 ||
    { echo "  pixdecor: couldn't download it"; exit 1; }
step tar xzf "$pdir.tgz" -C "$pdir" --strip-components=1
step patch -d "$pdir" -p1 -i "$SRC/wayfire-plugin/pixdecor-title.patch"
step meson setup "$pdir/build" "$pdir" --prefix "$PLUG_PREFIX" --libdir lib --buildtype release
step ninja -C "$pdir/build"
step meson install -C "$pdir/build" --destdir "$pdir/stage"
# staged, then renamed into place: a running Wayfire keeps its loaded copy
(cd "$pdir/stage$PLUG_PREFIX" && find . -type f) | while read -r f; do
    mkdir -p "$(dirname "$PLUG_PREFIX/$f")"
    cp "$pdir/stage$PLUG_PREFIX/$f" "$PLUG_PREFIX/$f.new" && mv -f "$PLUG_PREFIX/$f.new" "$PLUG_PREFIX/$f"
done
# pixdecor puts its settings (pixdecor.xml) where the installed Wayfire keeps
# metadata (/usr/share/wayfire/metadata), outside the prefix: copied in next
# to the plugin. Without it Wayfire aborted as pixdecor loaded -- every login
# went back to the login screen (a friend's laptop; with a system pixdecor
# installed, its own pixdecor.xml had hidden it).
find "$pdir/stage" -name '*.xml' -path '*/wayfire/metadata/*' ! -path "$pdir/stage$PLUG_PREFIX/*" | while read -r f; do
    mkdir -p "$PLUG_PREFIX/share/wayfire/metadata"
    cp "$f" "$PLUG_PREFIX/share/wayfire/metadata/${f##*/}.new" &&
        mv -f "$PLUG_PREFIX/share/wayfire/metadata/${f##*/}.new" "$PLUG_PREFIX/share/wayfire/metadata/${f##*/}"
done
echo "  pixdecor installed to $PLUG_PREFIX (log out and back in to load it)"
