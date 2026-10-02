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
for p in wayfire pango cairo; do
    pkg-config --exists "$p" || { echo "  pixdecor: needs the $p development files"; exit 1; }
done
pdir="$(mktemp -d)"
log="$pdir.log"
step() { "$@" >>"$log" 2>&1 || { echo "  pixdecor didn't build ($1 failed); the last lines of $log:"; tail -n 15 "$log" | sed 's/^/    /'; exit 1; }; }
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
echo "  pixdecor installed to $PLUG_PREFIX (log out and back in to load it)"
