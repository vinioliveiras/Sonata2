#!/usr/bin/env bash
# build-wayfire.sh: builds the Wayfire you have installed (the same commit)
# with Sonata's fix for buffers the GPU refuses (wayfire-plugin/wayfire-
# buffer-failures.patch: skip a frame or an animation instead of aborting --
# a full NVIDIA card took the whole session down). It goes to its own
# folder, never over the system's Wayfire; tools/sonata-session starts it
# while it matches the installed version (after a Wayfire update, run this
# again). Takes a few minutes. Run alone to retry; it prints why a build failed.
set -uo pipefail
SRC="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PREFIX="${SONATA_WAYFIRE_PREFIX:-$HOME/.local/opt/sonata-wayfire}"
for t in git patch meson ninja pkg-config wayfire; do
    command -v "$t" >/dev/null || { echo "  wayfire: needs $t"; exit 1; }
done
# "0.12.0-d181484c (Sep 29 2026) branch master wlroots-0.20.2" -> d181484c
commit="$(wayfire --version 2>/dev/null | sed -n 's/^[0-9.]*-\([0-9a-f]\{7,\}\).*/\1/p' | head -n1)"
[ -n "$commit" ] || { echo "  wayfire: the installed Wayfire has no commit in its version (a release build): nothing to match"; exit 1; }
missing=""
for p in wlroots-0.20 wf-config wayland-server wayland-protocols libinput pixman-1 xkbcommon cairo pango libdrm egl glesv2; do
    pkg-config --exists "$p" || missing="$missing $p"
done
if [ -n "$missing" ]; then
    echo "  wayfire: needs the development files of:$missing"
    exit 1
fi
wdir="$(mktemp -d)"
log="$wdir.log"
step() {
    "$@" >>"$log" 2>&1 && return
    echo "  wayfire didn't build ($1 failed); from $log:"
    if grep -qE "error:|ERROR:" "$log"; then
        grep -E -A3 "error:|ERROR:" "$log" | head -n 40 | sed 's/^/    /'
    else
        tail -n 15 "$log" | sed 's/^/    /'
    fi
    exit 1
}
step git clone -q --filter=blob:none https://github.com/WayfireWM/wayfire.git "$wdir/src"
step git -C "$wdir/src" checkout -q "$commit"
step git -C "$wdir/src" submodule update -q --init --depth 1 subprojects/wf-json subprojects/wf-utils subprojects/wf-touch
step patch -d "$wdir/src" -p1 -i "$SRC/wayfire-plugin/wayfire-buffer-failures.patch"
step meson setup "$wdir/build" "$wdir/src" --prefix "$PREFIX" --libdir lib --buildtype release \
    -Duse_system_wlroots=enabled -Duse_system_wfconfig=enabled -Dtests=disabled \
    -Dcpp_link_args="-Wl,-rpath,$PREFIX/lib" -Dc_link_args="-Wl,-rpath,$PREFIX/lib"
step ninja -C "$wdir/build"
step meson install -C "$wdir/build" --destdir "$wdir/stage"
# staged, then renamed into place: a running Wayfire keeps its loaded copy
(cd "$wdir/stage$PREFIX" && find . -type f) | while read -r f; do
    mkdir -p "$(dirname "$PREFIX/$f")"
    cp -p "$wdir/stage$PREFIX/$f" "$PREFIX/$f.new" && mv -f "$PREFIX/$f.new" "$PREFIX/$f"
done
echo "$commit" > "$PREFIX/sonata-commit"
echo "  Wayfire $commit with Sonata's fix installed to $PREFIX (log out and back in to use it)"
