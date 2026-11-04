#!/bin/sh
# Install the Rust build of Sonata NEXT TO the installed (Python) Sonata,
# for trying it (docs/RUST_PORT.md, "Side by side"). It touches nothing of
# the Python install and never writes ~/.config/sonata2:
#
#   ~/.local/bin/sonata2-rs                       the binary
#   ~/.local/share/sonata2-rs/locale/             the translations (a copy)
#   ~/.local/share/applications/io.github.vinioliveiras.sonata2rs.*.desktop
#                                                 "Calculator (Rust)"... in Launchpad
#   ~/.config/sonata2-rs/                         what the Rust build saves
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
    rm -f "$BIN/sonata2-rs"
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
cargo build --release --bin sonata2-rs

mkdir -p "$BIN" "$SHARE/sonata2-rs"
install -m 755 target/release/sonata2-rs "$BIN/sonata2-rs"
rm -rf "$SHARE/sonata2-rs/locale"
cp -r "$REPO/sonata2/locale" "$SHARE/sonata2-rs/locale"
# the desktop entries name this binary; on trial they are the "(Rust)" ones
SONATA2_RS_LAUNCHER="$BIN/sonata2-rs" "$BIN/sonata2-rs" desktop-files
update-desktop-database "$APPS" 2>/dev/null || true

echo
echo "Installed next to Sonata: \"Calculator (Rust)\" is in Launchpad,"
echo "or run: sonata2-rs calculator"
echo "Remove it with: rust/install-rs.sh --uninstall"
