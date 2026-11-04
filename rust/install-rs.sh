#!/bin/sh
# Install the Rust build of Sonata NEXT TO the installed (Python) Sonata,
# for trying it (docs/RUST_PORT.md, "Side by side"). It touches nothing of
# the Python install and never writes ~/.config/sonata2:
#
#   ~/.local/bin/sonata2-rs                       the binary
#   ~/.local/bin/sonata2-rs-terminal              Terminal's own (only it needs libvte)
#   ~/.local/bin/sonata2-rs-camera                Camera's own (only it needs GStreamer)
#   ~/.local/share/sonata2-rs/locale/             the translations (a copy)
#   ~/.local/share/sonata2-rs/sounds/             the alarm sounds (a copy, for Clock's preview)
#   ~/.local/share/sonata2-rs/wallpapers/         Sonata's wallpapers (a copy: the default one)
#   ~/.local/share/sonata2-rs/icons               Sonata's icon themes: a link to this clone's
#                                                 (125 MB; keep the clone while trying it)
#   ~/.local/share/applications/io.github.vinioliveiras.sonata2rs.*.desktop
#                                                 "Calculator (Rust)"... in Launchpad
#   ~/.config/sonata2-rs/                         what the Rust build saves
# One exception, Vini's call: Clock (Rust) edits the same alarms and timers
# as the Python Clock (~/.local/share/sonata2-data/clock), so they ring.
# TextEdit and Notes keep theirs in ~/.local/share/sonata2-rs-data.
#
#   rust/install-rs.sh               build (release) and install
#   rust/install-rs.sh --uninstall   remove all of the above but ~/.config/sonata2-rs
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$HERE")
BIN="$HOME/.local/bin"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$SHARE/applications"

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$BIN/sonata2-rs" "$BIN/sonata2-rs-terminal" "$BIN/sonata2-rs-camera"
    rm -rf "$SHARE/sonata2-rs"
    rm -f "$APPS"/io.github.vinioliveiras.sonata2rs.*.desktop
    update-desktop-database "$APPS" 2>/dev/null || true
    echo "Removed the Rust build. Its settings stay in ~/.config/sonata2-rs (delete the folder to forget them)."
    exit 0
fi

if ! command -v cargo >/dev/null 2>&1; then
    echo "cargo is missing: install Rust first (Arch/CachyOS: sudo pacman -S rust)." >&2
    exit 1
fi

cd "$HERE"
cargo build --release --bin sonata2-rs --bin sonata2-rs-terminal --bin sonata2-rs-camera

mkdir -p "$BIN" "$SHARE/sonata2-rs"
install -m 755 target/release/sonata2-rs "$BIN/sonata2-rs"
install -m 755 target/release/sonata2-rs-terminal "$BIN/sonata2-rs-terminal"
install -m 755 target/release/sonata2-rs-camera "$BIN/sonata2-rs-camera"
rm -rf "$SHARE/sonata2-rs/locale"
cp -r "$REPO/sonata2/locale" "$SHARE/sonata2-rs/locale"
rm -rf "$SHARE/sonata2-rs/sounds"
cp -r "$REPO/sonata2/data/sounds" "$SHARE/sonata2-rs/sounds"
rm -rf "$SHARE/sonata2-rs/wallpapers"
cp -r "$REPO/sonata2/data/wallpapers" "$SHARE/sonata2-rs/wallpapers"
rm -rf "$SHARE/sonata2-rs/icons"
ln -s "$REPO/sonata2/data/icons" "$SHARE/sonata2-rs/icons"
# the desktop entries name this binary; on trial they are the "(Rust)" ones
SONATA2_RS_LAUNCHER="$BIN/sonata2-rs" "$BIN/sonata2-rs" desktop-files
update-desktop-database "$APPS" 2>/dev/null || true

echo
echo "Installed next to Sonata: the Rust Calculator, Clock, TextEdit, Preview, Notes, Terminal, Feedbacker, Disk Manager and Camera are in Launchpad,"
echo "or run: sonata2-rs calculator | clock | textedit [FILE] | preview [FILE] | notes | terminal | feedback | diskutil | camera"
echo "Remove it with: rust/install-rs.sh --uninstall"
