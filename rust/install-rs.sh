#!/bin/sh
# Install the Rust build of Sonata NEXT TO the installed (Python) Sonata,
# for trying it (docs/RUST_PORT.md, "Side by side"). It touches nothing of
# the Python install and never writes ~/.config/sonata2:
#
#   ~/.local/bin/sonata2-rs                       the binary
#   ~/.local/bin/sonata2-rs-terminal              Terminal's own (only it needs libvte)
#   ~/.local/bin/sonata2-rs-camera                Camera's own (only it needs GStreamer)
#   ~/.local/bin/sonata2-rs-videos                Videos' own (the same)
#   ~/.local/bin/sonata2-rs-assistant             the Assistant's own (libsoup, libsecret)
#   ~/.local/share/sonata2-rs/locale/             the translations (a copy)
#   ~/.local/share/sonata2-rs/data/sounds/        the alarm sounds (a copy, for Clock's preview)
#   ~/.local/share/sonata2-rs/data/wallpapers/    Sonata's wallpapers (a copy: the default one)
#   ~/.local/share/sonata2-rs/data/avatars/       the stock user pictures (a copy, Settings > Users)
#   ~/.local/share/sonata2-rs/config/wayfire.ini  Wayfire's default config (the shortcuts' defaults)
#   ~/.local/share/sonata2-rs/data/icons          Sonata's icon themes: a link to this clone's
#                                                 (125 MB; keep the clone while trying it)
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
    rm -f "$BIN/sonata-rs-session"
    if [ -e /usr/share/wayland-sessions/sonata-rs.desktop ]; then
        sudo rm -f /usr/share/wayland-sessions/sonata-rs.desktop
    fi
    rm -f "$BIN/sonata2-rs" "$BIN/sonata2-rs-terminal" "$BIN/sonata2-rs-camera" "$BIN/sonata2-rs-videos" "$BIN/sonata2-rs-assistant"
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
cargo build --release --bin sonata2-rs --bin sonata2-rs-terminal --bin sonata2-rs-camera --bin sonata2-rs-videos --bin sonata2-rs-assistant

mkdir -p "$BIN" "$SHARE/sonata2-rs"
install -m 755 target/release/sonata2-rs "$BIN/sonata2-rs"
install -m 755 target/release/sonata2-rs-terminal "$BIN/sonata2-rs-terminal"
install -m 755 target/release/sonata2-rs-camera "$BIN/sonata2-rs-camera"
install -m 755 target/release/sonata2-rs-videos "$BIN/sonata2-rs-videos"
install -m 755 target/release/sonata2-rs-assistant "$BIN/sonata2-rs-assistant"
rm -rf "$SHARE/sonata2-rs/locale"
cp -r "$REPO/locale" "$SHARE/sonata2-rs/locale"
mkdir -p "$SHARE/sonata2-rs/data"
rm -rf "$SHARE/sonata2-rs/data/sounds"
cp -r "$REPO/data/sounds" "$SHARE/sonata2-rs/data/sounds"
rm -rf "$SHARE/sonata2-rs/data/wallpapers"
cp -r "$REPO/data/wallpapers" "$SHARE/sonata2-rs/data/wallpapers"
# Settings > Users' stock pictures (sonata_config::data_path("avatars")):
# without the copy the installed build only found them while the clone stayed
rm -rf "$SHARE/sonata2-rs/data/avatars"
cp -r "$REPO/data/avatars" "$SHARE/sonata2-rs/data/avatars"
# Wayfire's default config: Settings' shortcuts read their defaults from it
mkdir -p "$SHARE/sonata2-rs/config"
cp "$REPO/config/wayfire.ini" "$SHARE/sonata2-rs/config/wayfire.ini"
# The icon themes are ~16,000 symlinks (one artwork, many names). A clone
# made with core.symlinks=false holds them as small text files naming the
# target, which GTK can't draw: Files, Terminal, Spotify... went invisible
# in the Dock (Vini). Check them out as real links again.
if [ -f "$REPO/data/icons/Sonata-MacTahoe/apps/scalable/system-file-manager.svg" ] \
    && [ ! -L "$REPO/data/icons/Sonata-MacTahoe/apps/scalable/system-file-manager.svg" ]; then
    git -C "$REPO" -c core.symlinks=true checkout -- data/icons
fi
rm -rf "$SHARE/sonata2-rs/data/icons"
ln -s "$REPO/data/icons" "$SHARE/sonata2-rs/data/icons"
# The "Calculator (Rust)"... entries were for trying the apps next to the
# Python ones (Vini: removed once the port was done). The Rust session
# opens the Rust apps through Sonata's own entries (the sonata2 launcher);
# an older install's are taken away.
rm -f "$APPS"/io.github.vinioliveiras.sonata2rs.*.desktop
update-desktop-database "$APPS" 2>/dev/null || true

echo
echo "Installed: sonata2-rs. Log in to \"Sonata (Rust preview)\" (rust/install-rs.sh --session) for the whole desktop in Rust,"
echo "or run: sonata2-rs calculator | clock | textedit [FILE] | preview [FILE] | notes | terminal | feedback | diskutil | camera | videos [FILE] | assistant | calendar | activity"
echo "Remove it with: rust/install-rs.sh --uninstall"

# --session: a "Sonata (Rust preview)" entry on the login screen. The whole
# shell and every app run as Rust there, with their real app ids and the
# real settings (~/.config/sonata2, shared with the Python Sonata); what
# isn't ported yet (the portal's D-Bus start, a few helpers) still comes
# from the Python install. The "Sonata" entry stays as it was.
if [ "${1:-}" = "--session" ]; then
    cat > "$BIN/sonata-rs-session" <<EOF
#!/bin/sh
# "Sonata (Rust preview)" (rust/install-rs.sh --session)
logdir="\${XDG_RUNTIME_DIR:-/tmp}/sonata2-logs"
mkdir -p "\$logdir"
exec > "\$logdir/login.log" 2>&1
export PATH="$BIN:\$PATH" SONATA2_LAUNCHER="$BIN/sonata2" SONATA2_RS_SIDE_BY_SIDE=0 SONATA2_RS_SESSION=1
exec bash -x "$REPO/tools/sonata-session" "\$@"
EOF
    chmod 755 "$BIN/sonata-rs-session"
    # the installed Python launcher hands what's ported to sonata2-rs in
    # that session (install.sh writes this into new installs), by its path:
    # D-Bus starts the portal with a PATH without ~/.local/bin
    if [ -f "$BIN/sonata2" ] && ! grep -qF "\"$BIN/sonata2-rs\" --handles" "$BIN/sonata2"; then
        sed -i '/sonata2-rs.* --handles/,/^fi$/d' "$BIN/sonata2"
        awk -v bin="$BIN" '/^exec python3 -m sonata2 / {
            print "if [ \"${SONATA2_RS_SESSION:-}\" = 1 ] && [ -x \"" bin "/sonata2-rs\" ] && \"" bin "/sonata2-rs\" --handles \"${1:-}\"; then"
            print "    exec \"" bin "/sonata2-rs\" \"$@\""
            print "fi"
        } { print }' "$BIN/sonata2" > "$BIN/sonata2.new" && cat "$BIN/sonata2.new" > "$BIN/sonata2" && rm -f "$BIN/sonata2.new"
    fi
    entry=$(mktemp)
    printf '[Desktop Entry]\nName=Sonata (Rust preview)\nComment=Sonata 2 with the Rust shell and apps\nExec=%s\nTryExec=%s\nType=Application\nDesktopNames=Sonata\n' \
        "$BIN/sonata-rs-session" "$BIN/sonata-rs-session" > "$entry"
    sudo install -D -m 644 "$entry" /usr/share/wayland-sessions/sonata-rs.desktop
    rm -f "$entry"
    echo 'Added "Sonata (Rust preview)" to the login screen.'
fi
