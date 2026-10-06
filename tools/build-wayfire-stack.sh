#!/usr/bin/env bash
# build-wayfire-stack.sh: Wayfire 0.12 (the version Sonata's plugins are made
# for) with the wlroots it needs, from source, into its own folder -- for
# distros whose own Wayfire is older (Fedora 44: 0.10, Debian 13 / Ubuntu
# 25.04: 0.9). Never over the system's Wayfire: tools/sonata-session starts
# this one ($PREFIX/standalone), and install.sh builds Sonata's plugins and
# pixdecor against it. Takes several minutes; run alone to retry, it prints
# why a build failed. install.sh installs the build dependencies first.
#
#   tools/build-wayfire-stack.sh            # build (skipped when already built at these versions)
#   SONATA_WAYFIRE_PREFIX=/opt/x tools/build-wayfire-stack.sh
set -uo pipefail
SRC="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
PREFIX="${SONATA_WAYFIRE_PREFIX:-$HOME/.local/opt/sonata-wayfire}"
# what Arch / CachyOS run, and Sonata's plugins are built and tested against
WLROOTS_TAG=0.20.2
WAYFIRE_COMMIT=d181484c
STAMP="wlroots-$WLROOTS_TAG wayfire-$WAYFIRE_COMMIT"
if [ -x "$PREFIX/bin/wayfire" ] && [ "$(cat "$PREFIX/standalone" 2>/dev/null)" = "$STAMP" ]; then
    echo "  Wayfire $WAYFIRE_COMMIT already built in $PREFIX"
    exit 0
fi
for t in git patch meson ninja pkg-config c++; do
    command -v "$t" >/dev/null || { echo "  wayfire: needs $t"; exit 1; }
done
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
# (--wrap-mode=default: what the distro's own is too old for -- Debian 13's
#  Wayland for wlroots 0.20 -- is fetched and built along, into $PREFIX;
#  Debian's meson won't download it otherwise)
# everything below looks in $PREFIX first (wlroots, then Wayfire's own wf-config)
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib64/pkgconfig:$PREFIX/share/pkgconfig:${PKG_CONFIG_PATH:-}"
rpath=(-Dcpp_link_args="-Wl,-rpath,$PREFIX/lib" -Dc_link_args="-Wl,-rpath,$PREFIX/lib")
# staged, then moved into place file by file: a running Wayfire keeps its
# loaded copies (overwriting a loaded library in place would crash it)
put_in_place() {
    local stage="$1"
    (cd "$stage$PREFIX" && find . -type f -o -type l) | while read -r f; do
        mkdir -p "$(dirname "$PREFIX/$f")"
        if [ -L "$stage$PREFIX/$f" ]; then
            ln -sfn "$(readlink "$stage$PREFIX/$f")" "$PREFIX/$f"
        else
            cp -p "$stage$PREFIX/$f" "$PREFIX/$f.new" && mv -f "$PREFIX/$f.new" "$PREFIX/$f"
        fi
    done
}

# wlroots' pixman renderer wants pixman 0.46 with no fallback of its own
# (Debian 13 / Ubuntu 25.04 have 0.44): its wrap is used from the start then
fallback=()
pkg-config --atleast-version=0.46.0 pixman-1 2>/dev/null || fallback+=(--force-fallback-for=pixman-1)

echo "  wlroots $WLROOTS_TAG..."
step git clone -q --depth 1 --branch "$WLROOTS_TAG" https://gitlab.freedesktop.org/wlroots/wlroots.git "$wdir/wlroots"
step meson setup "$wdir/wlroots-build" "$wdir/wlroots" --prefix "$PREFIX" --libdir lib --buildtype release \
    --wrap-mode=default "${fallback[@]}" -Dwerror=false -Dexamples=false -Dxwayland=enabled -Dbackends=drm,libinput -Drenderers=gles2,vulkan "${rpath[@]}"
step ninja -C "$wdir/wlroots-build"
step meson install -C "$wdir/wlroots-build" --destdir "$wdir/stage-wlroots"
put_in_place "$wdir/stage-wlroots"

echo "  Wayfire $WAYFIRE_COMMIT..."
step git clone -q --filter=blob:none https://github.com/WayfireWM/wayfire.git "$wdir/wayfire"
step git -C "$wdir/wayfire" checkout -q "$WAYFIRE_COMMIT"
step git -C "$wdir/wayfire" submodule update -q --init subprojects/wf-json subprojects/wf-utils subprojects/wf-touch subprojects/wf-config
step patch -d "$wdir/wayfire" -p1 -i "$SRC/wayfire-plugin/wayfire-buffer-failures.patch"
step patch -d "$wdir/wayfire" -p1 -i "$SRC/wayfire-plugin/wayfire-cursor-focus.patch"
step meson setup "$wdir/wayfire-build" "$wdir/wayfire" --prefix "$PREFIX" --libdir lib --buildtype release \
    --wrap-mode=default -Duse_system_wlroots=enabled -Duse_system_wfconfig=disabled -Dtests=disabled "${rpath[@]}"
step ninja -C "$wdir/wayfire-build"
step meson install -C "$wdir/wayfire-build" --destdir "$wdir/stage-wayfire"
put_in_place "$wdir/stage-wayfire"
echo "$WAYFIRE_COMMIT" > "$PREFIX/sonata-commit"
echo "$STAMP" > "$PREFIX/standalone"       # (tools/sonata-session: use it whatever the system's Wayfire is)
rm -f "$PREFIX/failed"
rm -rf "$wdir" "$log"
echo "  Wayfire $WAYFIRE_COMMIT (wlroots $WLROOTS_TAG) installed to $PREFIX"
