#!/usr/bin/env bash
# Sonata 2 installer -- works on any distro (no distro-specific paths).
# Sonata is one Rust program (rust/: `sonata2-rs`, called `sonata2`); this
# builds it with cargo and installs it, with its data, the session and the
# login screen entry. Run over an older Sonata -- the Python one, or the
# Rust preview next to it -- it is an upgrade: nothing of yours is lost
# (`sonata2 migrate`, below).
#
#   ./install.sh              install for this user (~/.local), ask for sudo
#                             only to add "Sonata" to the login screen
#   ./install.sh --system     install for all users (/usr/local, needs sudo)
#   (dependencies)            always checked and installed with the distro's
#                             package manager (pacman/apt/dnf/zypper/xbps/apk):
#                             Rust (cargo) and the libraries to build with,
#                             then every optional one (Wi-Fi, screenshots,
#                             Night Shift...); pixdecor built from source
#   ./install.sh --no-deps    only check them (print what's missing)
#   ./install.sh --yes        don't ask (login-screen entry included)
#   ./install.sh --uninstall  remove it again (your settings and data stay)
#   ./install.sh --dev        link to this clone instead of copying it: the
#                             session runs this clone's data and scripts and
#                             the binaries cargo builds here (`cargo build
#                             --release` in rust/, then `sonata2 restart`)
#   (login screen)            Sonata's own login screen (greetd in place of
#                             GDM/SDDM, from the next boot) is set up too:
#                             asked once, then kept up to date
#   ./install.sh --no-greeter keep the current login screen
#   ./install.sh --gdm        back to the previous login screen (only that)
#   ./install.sh --no-mount-without-password
#                             system disks and system LUKS ask for the
#                             administrator password (otherwise administrators
#                             mount them without one: a polkit rule, added
#                             without asking)
#   ./install.sh --no-firewall  don't turn the firewall on (otherwise on the
#                             first install: ufw, nothing comes in unasked;
#                             turned off later in Settings, it stays off)
#   ./install.sh --no-bazaar  don't install Bazaar (the app store, from Flathub)
#   ./install.sh --no-usbguard  leave USBGuard as it is (otherwise, when it's
#                             installed, set up like Settings' Set Up: every
#                             device allowed while unlocked, new ones blocked
#                             while locked)
#
# Installs: the binaries sonata2-rs (+ -terminal, -camera, -videos,
# -assistant: the apps that need libraries the rest doesn't) and the
# `sonata2` link to it -> <prefix>/bin; Sonata's data, translations,
# default config and session scripts -> <prefix>/share/sonata2-rs; the
# `sonata-session` launcher -> <prefix>/bin; the session entry ->
# /usr/share/wayland-sessions/sonata.desktop; the session's Wayfire config
# -> ~/.config/sonata2/wayfire.ini (an edited copy is kept); portal
# preferences for the "Sonata" desktop.
#
# Upgrading (Software Update runs this too, from the release it downloaded):
# nothing is removed until the new build is installed. Then `sonata2 migrate`
# carries the Rust preview's settings (~/.config/sonata2-rs) and app data
# (~/.local/share/sonata2-rs-data) into the real ones (the newer file wins,
# the other one is kept in the backup) and moves the old Python install
# (<prefix>/share/sonata2, its `sonata2` launcher), the preview's launcher and
# its loose copies to ~/.local/share/sonata2-backups/upgrade-<date>.
# ~/.config/sonata2 and ~/.local/share/sonata2-data are never replaced.
set -euo pipefail
# a step that fails stops the install: say which (a friend's install ended without a word)
trap 's=$?; echo "install.sh stopped at line $LINENO (exit $s): $BASH_COMMAND" >&2; echo "Please send this line to the Sonata developers." >&2' ERR
SRC="$(cd "$(dirname "$0")" && pwd)"
MODE=user DEPS=1 YES=0 UNINSTALL=0 DEV=0 GREETER=ask MOUNTRULE=1 USBGUARD=1 BAZAAR=1 FIREWALL=1
for a in "$@"; do
    case "$a" in
        --system) MODE=system ;; --deps) DEPS=1 ;; --no-deps) DEPS=0 ;; --yes|-y) YES=1 ;; --uninstall) UNINSTALL=1 ;; --dev) DEV=1 ;;
        --greeter) GREETER=1 ;; --no-greeter) GREETER=0 ;; --no-usbguard) USBGUARD=0 ;; --no-bazaar) BAZAAR=0 ;; --no-firewall) FIREWALL=0 ;;
        --mount-without-password) MOUNTRULE=1 ;; --no-mount-without-password) MOUNTRULE=0 ;; --gdm) exec "$(dirname "$0")/tools/greeter-setup.sh" revert ;;
        -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
        *) echo "unknown option: $a (see --help)"; exit 2 ;;
    esac
done

# `sudo ./install.sh` installed Sonata for root: the login screen then found
# none for the user, and every login went straight back to it (a friend's
# laptop). It asks for the password itself when it needs it.
if [ "$(id -u)" = 0 ] && [ "$MODE" != system ] && [ "$UNINSTALL" != 1 ]; then
    echo "Run ./install.sh without sudo: Sonata is installed for your user (it asks for your password when needed)." >&2
    echo "(--system installs it for every user, as root.)" >&2
    exit 1
fi
if [ "$MODE" = system ]; then PREFIX=/usr/local; SUDO=sudo; else PREFIX="$HOME/.local"; SUDO=""; fi
[ "$(id -u)" = 0 ] && SUDO=""
SHARE="$PREFIX/share/sonata2-rs"
BIN="$PREFIX/bin"
SESSION_FILE=/usr/share/wayland-sessions/sonata.desktop
# the "Sonata (Rust preview)" entry the Rust build added while it was on trial
PREVIEW_SESSION_FILE=/usr/share/wayland-sessions/sonata-rs.desktop
CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
if [ "$MODE" = system ]; then PORTAL_DIR=/usr/share/xdg-desktop-portal; else PORTAL_DIR="$CFG/xdg-desktop-portal"; fi
# the binaries: Sonata itself, and the apps whose libraries the rest doesn't need
BINS="sonata2-rs sonata2-rs-terminal sonata2-rs-camera sonata2-rs-videos sonata2-rs-assistant"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
ask() { [ "$YES" = 1 ] && return 0; read -r -p "$1 [Y/n] " r; [ -z "$r" ] || [[ "$r" =~ ^[YySs] ]]; }
# default No (security-relevant choices): --yes answers No, only an explicit flag says yes
ask_no() { [ "$YES" = 1 ] && return 1; read -r -p "$1 [y/N] " r; [[ "$r" =~ ^[YySs] ]]; }

# -- uninstall ---------------------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
    say "Removing Sonata 2 from $PREFIX"
    $SUDO rm -rf "${SHARE:?}"
    for b in $BINS sonata2 sonata-session sonata-filemanager1; do $SUDO rm -f "${BIN:?}/${b:?}"; done
    $SUDO rm -f "$PORTAL_DIR/sonata-portals.conf"
    if [ -f "$SESSION_FILE" ] && ask "Remove \"Sonata\" from the login screen (sudo)?"; then
        sudo rm -f "$SESSION_FILE" /usr/local/bin/sonata-login
    fi
    rm -f "$DATA/dbus-1/services/org.freedesktop.impl.portal.desktop.sonata.service" \
          "$DATA/dbus-1/services/org.freedesktop.FileManager1.service"
    sudo rm -f /usr/share/xdg-desktop-portal/portals/sonata.portal 2>/dev/null || true
    grep -qs "Sonata's Terminal" "$BIN/xdg-terminal-exec" && $SUDO rm -f "$BIN/xdg-terminal-exec"
    # Sonata's own launchers (written again by its next install)
    rm -f "$APPS"/io.github.vinioliveiras.sonata2.*.desktop "$APPS/sonata2-launchpad.desktop" \
          "$APPS/sonata2-settings.desktop" "$APPS/sonata2-screenshot.desktop"
    "$(dirname "$0")/tools/quiet-console.sh" revert || true
    sudo rm -f /etc/polkit-1/rules.d/50-sonata2-mount.rules /etc/polkit-1/rules.d/60-sonata2-updates.rules \
        2>/dev/null || true
    rm -rf "$HOME/.local/opt/sonata-wayfire"
    say "Done. Your settings are still in $CFG/sonata2 and your apps' data in $DATA/sonata2-data."
    exit 0
fi

# -- dependencies ------------------------------------------------------------------------------
# What building Sonata needs: Rust, and the libraries it links with their
# headers (pkg-config names; Terminal, Camera, Videos and the Assistant
# need theirs only for their own binaries).
REQUIRED_PC="gtk4 libadwaita-1 gtk4-layer-shell-0"
missing_build() {
    local m=""
    command -v cargo >/dev/null || m="$m cargo"
    command -v pkg-config >/dev/null || m="$m pkg-config"
    if command -v pkg-config >/dev/null; then
        for p in $REQUIRED_PC; do pkg-config --exists "$p" 2>/dev/null || m="$m $p"; done
    fi
    command -v wayfire >/dev/null || m="$m wayfire"
    echo "$m" | xargs
}
missing="$(missing_build)"

. /etc/os-release 2>/dev/null || true
family="${ID:-} ${ID_LIKE:-}"
# Package names per family. Needs: Wayfire >= 0.9, GTK >= 4.12,
# libadwaita >= 1.5, gtk4-layer-shell >= 1.0, Rust >= 1.80 (cargo). Known
# good: Arch and its derivatives (CachyOS, EndeavourOS, Manjaro). The other
# families' development packages follow each distro's naming; check them in
# a container with tools/test-install.sh before a release.
NI=""      # the package manager's "don't ask" flag, for optional packages one by one
case "$family" in
    *arch*)   PM="sudo pacman -S --needed"; NI="--noconfirm"
              PKGS="wayfire gtk4 libadwaita gtk4-layer-shell rust pkgconf gcc rsync"
              BUILD_OPT="vte4 gstreamer gst-plugins-base-libs libsoup3 libsecret gtksourceview5"
              OPT="networkmanager wireplumber brightnessctl bluez-utils wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk gnome-keyring keepassxc libpulse xorg-xwayland grim slurp wl-clipboard ffmpegthumbnailer ffmpeg bubblewrap webp-pixbuf-loader gamemode gst-plugins-good gst-plugins-bad gst-libav udisks2 wf-recorder gpu-screen-recorder wlsunset wtype swayidle openssl meson ninja ddcutil openrgb webkitgtk-6.0 wayvnc ufw cups usbguard flatpak gocryptfs" ;;
    *debian*|*ubuntu*) PM="sudo apt install"; NI="-y"
              PKGS="wayfire cargo rustc pkg-config build-essential rsync libgtk-4-dev libadwaita-1-dev libgtk4-layer-shell-dev"
              BUILD_OPT="libvte-2.91-gtk4-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libsoup-3.0-dev libsecret-1-dev libgtksourceview-5-dev"
              OPT="network-manager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk libpolkit-agent-1-0 gnome-keyring keepassxc pulseaudio-utils xwayland grim slurp wl-clipboard ffmpegthumbnailer ffmpeg bubblewrap webp-pixbuf-loader gamemode gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav udisks2 wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb libwebkitgtk-6.0-4 wayvnc ufw cups usbguard flatpak gocryptfs" ;;
    *fedora*|*rhel*) PM="sudo dnf install"; NI="-y"
              PKGS="wayfire cargo rust pkgconf-pkg-config gcc rsync gtk4-devel libadwaita-devel gtk4-layer-shell-devel"
              BUILD_OPT="vte291-gtk4-devel gstreamer1-devel gstreamer1-plugins-base-devel libsoup3-devel libsecret-devel gtksourceview5-devel"
              OPT="NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk gnome-keyring keepassxc pulseaudio-utils xorg-x11-server-Xwayland grim slurp wl-clipboard ffmpegthumbnailer ffmpeg-free bubblewrap webp-pixbuf-loader gamemode gstreamer1-plugins-good gstreamer1-plugins-bad-free udisks2 wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb webkitgtk6.0 wayvnc ufw cups usbguard flatpak gocryptfs" ;;
    *suse*)   PM="sudo zypper install"; NI="-y"
              PKGS="wayfire cargo rust pkgconf gcc rsync gtk4-devel libadwaita-devel gtk4-layer-shell-devel"
              BUILD_OPT="vte-devel gstreamer-devel gstreamer-plugins-base-devel libsoup-devel libsecret-devel gtksourceview5-devel"
              OPT="NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk grim slurp wl-clipboard ffmpegthumbnailer ffmpeg bubblewrap wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb cups usbguard flatpak gocryptfs" ;;
    *void*)   PM="sudo xbps-install"; NI="-y"
              PKGS="wayfire cargo rust pkg-config gcc rsync gtk4-devel libadwaita-devel gtk4-layer-shell-devel"
              BUILD_OPT="vte3-gtk4-devel gstreamer1-devel gst-plugins-base1-devel libsoup3-devel libsecret-devel gtksourceview5-devel"
              OPT="NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk grim slurp wl-clipboard ffmpegthumbnailer ffmpeg bubblewrap wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb ufw cups usbguard flatpak gocryptfs" ;;
    *alpine*) PM="sudo apk add"; NI=""
              PKGS="wayfire cargo rust pkgconf build-base rsync gtk4.0-dev libadwaita-dev gtk4-layer-shell-dev"
              BUILD_OPT="vte3-gtk4-dev gstreamer-dev gst-plugins-base-dev libsoup3-dev libsecret-dev gtksourceview5-dev"
              OPT="networkmanager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk grim slurp wl-clipboard ffmpegthumbnailer ffmpeg bubblewrap wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb ufw cups usbguard flatpak" ;;
    *)        PM=""; PKGS=""; BUILD_OPT=""; OPT="" ;;
esac
# What building Wayfire 0.12 with its wlroots (tools/build-wayfire-stack.sh),
# Sonata's plugins and pixdecor needs, where the distro's Wayfire is older
# than Sonata's plugins (Fedora 44: 0.10; Debian 13, Ubuntu 25.04: 0.9).
# Checked by building in each distro's container (tools/test-install.sh).
case "$family" in
    *debian*|*ubuntu*) BUILD_DEPS="git patch meson ninja-build g++ pkg-config cmake bison flex gettext \
        libwayland-dev wayland-protocols libffi-dev libexpat1-dev libinput-dev libxkbcommon-dev \
        libpixman-1-dev libdrm-dev libegl-dev libgbm-dev libgles-dev libvulkan-dev glslang-tools \
        libseat-dev hwdata libdisplay-info-dev libliftoff-dev libsystemd-dev libxcb1-dev \
        libxcb-icccm4-dev libxcb-render-util0-dev libxcb-errors-dev libxcb-composite0-dev \
        libxcb-ewmh-dev libxcb-res0-dev xwayland libcairo2-dev libpango1.0-dev libglm-dev \
        libjpeg-dev libpng-dev libevdev-dev libxml2-dev nlohmann-json3-dev libyyjson-dev" ;;
    *fedora*|*rhel*) BUILD_DEPS="git patch meson ninja-build gcc-c++ pkgconf-pkg-config cmake gettext \
        wayland-devel wayland-protocols-devel libinput-devel libxkbcommon-devel pixman-devel \
        libdrm-devel mesa-libEGL-devel mesa-libgbm-devel mesa-libGLES-devel libglvnd-devel \
        vulkan-loader-devel vulkan-headers glslang libseat-devel hwdata-devel libdisplay-info-devel \
        libliftoff-devel systemd-devel libxcb-devel xcb-util-wm-devel xcb-util-renderutil-devel \
        xcb-util-errors-devel xorg-x11-server-Xwayland-devel cairo-devel pango-devel glm-devel \
        libjpeg-turbo-devel libpng-devel libevdev-devel libxml2-devel nlohmann-json-devel yyjson-devel" ;;
    *)        BUILD_DEPS="" ;;
esac

[ "$DEPS" = 1 ] && [ -n "$PM" ] && [ "$(id -u)" != 0 ] && { say "Dependencies need your password (sudo)"; sudo -v || DEPS=0; }
if [ -n "$missing" ]; then
    say "Missing: $missing"
    if [ -n "$PM" ]; then
        echo "Install with:  $PM $PKGS"
        if [ "$DEPS" = 1 ]; then $PM $NI $PKGS || true; fi
    else
        echo "Install Wayfire, Rust (cargo), pkg-config, and GTK 4, libadwaita and"
        echo "gtk4-layer-shell with their development files, with your package manager."
    fi
fi
# The apps with libraries of their own (Terminal: VTE; Camera, Videos:
# GStreamer; Assistant: libsoup, libsecret; TextEdit: GtkSourceView): one
# by one when they don't all go in at once -- a release without one still
# gets the rest.
if [ "$DEPS" = 1 ] && [ -n "$BUILD_OPT" ]; then
    $PM $NI $BUILD_OPT >/dev/null 2>&1 || for p in $BUILD_OPT; do $PM $NI "$p" >/dev/null 2>&1 || echo "  (not available here: $p)"; done
fi
# Still missing once the packages are in: Sonata can't be built -- say so
# and stop before anything is changed (the Sonata already installed, if
# any, stays as it is and keeps working)
still="$(missing_build)"
if [ -n "$still" ]; then
    say "Sonata can't be built without: $still"
    case " $still " in *" cargo "*)
        echo "Sonata is built from its source with Rust: install it with your package manager"
        echo "(Arch: sudo pacman -S rust; Debian/Ubuntu: sudo apt install cargo; Fedora: sudo dnf install cargo),"
        echo "or from https://rustup.rs, then run ./install.sh again." ;;
    esac
    echo "Nothing was changed: the Sonata you have (if any) stays as it is."
    exit 1
fi
# Rust older than Sonata's code needs (a distro's can lag): rustup has newer
rust_ver="$(rustc --version 2>/dev/null | sed -n 's/^rustc \([0-9]*\)\.\([0-9]*\).*/\1 \2/p')"
if [ -n "$rust_ver" ] && [ "$(echo "$rust_ver" | awk '{print ($1 * 1000 + $2 < 1080) ? 1 : 0}')" = 1 ]; then
    say "Rust $(echo "$rust_ver" | tr ' ' .) is too old for Sonata (it needs 1.80 or newer): https://rustup.rs has one."
    echo "Nothing was changed: the Sonata you have (if any) stays as it is."
    exit 1
fi
# Versions (older ones are the usual reason something doesn't show up)
bad=""
pkg-config --atleast-version=4.12 gtk4 2>/dev/null || bad="$bad, GTK $(pkg-config --modversion gtk4 2>/dev/null) (needs 4.12+)"
pkg-config --atleast-version=1.5 libadwaita-1 2>/dev/null || bad="$bad, libadwaita $(pkg-config --modversion libadwaita-1 2>/dev/null) (needs 1.5+)"
if [ -n "$bad" ]; then
    printf '\033[1mToo old for Sonata:\033[0m%s -- a newer distro release is needed.\n' "${bad#,}"
fi
if [ -n "$OPT" ]; then
    say "Features (Wi-Fi, sound, brightness, Bluetooth, displays, energy, portals, password prompts,"
    say "saved passwords, screenshots, recording, Night Shift, emoji typing, auto-lock)"
    if [ "$DEPS" = 1 ]; then
        # all at once (one transaction); if the distro lacks one of them, one by
        # one so the others still get installed
        if ! $PM $NI $OPT; then
            for p in $OPT; do
                mkdir -p "$HOME/.cache"; $PM $NI "$p" >"$HOME/.cache/sonata-opt.log" 2>&1 && continue
                echo "  (not available here: $p)"
                grep -iE "^(E|Error|dpkg):" "$HOME/.cache/sonata-opt.log" | head -n 3 | sed 's/^/    /' || true
                # a package whose setup script failed leaves dpkg "interrupted",
                # and every later apt install (greetd, the Wayfire build's tools) refuses
                command -v dpkg >/dev/null && { sudo dpkg --configure -a >/dev/null 2>&1 || true; }
            done
        fi
        # services those features talk to
        for svc in NetworkManager bluetooth power-profiles-daemon; do
            if systemctl list-unit-files "$svc.service" >/dev/null 2>&1 && ! systemctl is-active -q "$svc"; then
                sudo systemctl enable --now "$svc" >/dev/null 2>&1 && echo "  started $svc"
            fi
        done
        # external monitors' brightness (DDC/CI): ddcutil talks over /dev/i2c-*,
        # which needs the i2c-dev module (its udev rule gives the user access)
        if command -v ddcutil >/dev/null && [ ! -e /etc/modules-load.d/sonata-i2c.conf ]; then
            echo i2c-dev | sudo tee /etc/modules-load.d/sonata-i2c.conf >/dev/null && \
                sudo modprobe i2c-dev 2>/dev/null && echo "  external monitor brightness ready (i2c-dev)"
        fi
    else
        echo "  $PM $OPT"
    fi
fi
# pixdecor (title bars for terminals / X11 apps) is built from source further
# down (tools/build-pixdecor.sh), against the Wayfire installed here. Never
# from the AUR: its package needs wayfire-git, which conflicts with the stable
# wayfire and stopped the install under --noconfirm (issue #1).

# -- the build -----------------------------------------------------------------------------------
# Built before anything is installed or moved: a build that fails leaves
# the Sonata already installed (the Python one, an older Rust one) as it was.
# CARGO_TARGET_DIR: Software Update keeps one between updates (much faster).
TARGET="${CARGO_TARGET_DIR:-$SRC/rust/target}"
say "Building Sonata (cargo build --release; the first build takes several minutes)"
if ! (cd "$SRC/rust" && CARGO_TARGET_DIR="$TARGET" cargo build --release --locked --bin sonata2-rs); then
    say "Sonata couldn't be built (the output above says why)."
    echo "Nothing was changed: the Sonata you have (if any) stays as it is."
    exit 1
fi
built="sonata2-rs"
for b in sonata2-rs-terminal sonata2-rs-camera sonata2-rs-videos sonata2-rs-assistant; do
    if (cd "$SRC/rust" && CARGO_TARGET_DIR="$TARGET" cargo build --release --locked --bin "$b" >"$TARGET/$b.build.log" 2>&1); then
        built="$built $b"
    else
        echo "  $b couldn't be built (a library it needs is missing: $TARGET/$b.build.log); the rest of Sonata works without it"
    fi
done

# -- files ----------------------------------------------------------------------------------------
say "Installing Sonata 2 to $PREFIX"
$SUDO mkdir -p "$BIN" "$(dirname "$SHARE")"
tmp="$(mktemp -d)"; trap 'rm -rf "${tmp:?}"' EXIT
for b in $built; do
    if [ "$DEV" = 1 ]; then
        $SUDO ln -sfn "$TARGET/release/$b" "$BIN/$b"      # what cargo builds here runs at once
    else
        # beside it, then renamed: a running Sonata keeps the binary it has
        $SUDO install -m 755 "$TARGET/release/$b" "$BIN/.$b.new" && $SUDO mv -f "$BIN/.$b.new" "$BIN/$b"
    fi
done
# Sonata's folder: data (icons, wallpapers, sounds, fonts, themes...), the
# translations, the default Wayfire config and the session's scripts
if [ "$DEV" = 1 ]; then
    [ -L "$SHARE" ] || [ ! -e "$SHARE" ] || $SUDO mv "$SHARE" "$SHARE.before-dev.$$"
    $SUDO ln -sfn "$SRC" "$SHARE"          # the session runs this clone's data and scripts
    echo "Dev install: $SHARE -> $SRC"
else
    if [ -L "$SHARE" ]; then $SUDO unlink "$SHARE"; fi     # (a --dev install's link before)
    $SUDO mkdir -p "$SHARE/tools/bin"
    # the icon themes are ~16,000 links (one artwork, many names), kept as
    # links: a clone made with core.symlinks=false holds them as small text
    # files GTK can't draw (Files, Terminal, Spotify... went invisible in the Dock)
    if [ -d "$SRC/.git" ] && [ -f "$SRC/data/icons/Sonata-MacTahoe/apps/scalable/system-file-manager.svg" ] &&
            [ ! -L "$SRC/data/icons/Sonata-MacTahoe/apps/scalable/system-file-manager.svg" ]; then
        git -C "$SRC" -c core.symlinks=true checkout -- data/icons
    fi
    # updated in place: unchanged files (the bundled font, icons, sounds) keep
    # their inode -- the running Dock lost its font when the copy was deleted
    # and every name label went blank (Vini)
    for d in data locale config; do
        if command -v rsync >/dev/null; then
            $SUDO rsync -a --delete "$SRC/$d/" "$SHARE/$d/"
        else
            $SUDO cp -a "$SRC/$d" "$SHARE/$d.new.$$"
            [ ! -e "$SHARE/$d" ] || $SUDO mv "$SHARE/$d" "$tmp/$d.old"
            $SUDO mv "$SHARE/$d.new.$$" "$SHARE/$d"
        fi
    done
    $SUDO install -m 755 "$SRC/tools/session-env.sh" "$SRC/tools/sonata-session" "$SRC/tools/wayfire-config.sh" "$SHARE/tools/"
    $SUDO install -m 755 "$SRC/tools/bin/xdg-terminal-exec" "$SHARE/tools/bin/"
    for f in VERSION LICENSE README.md; do [ -e "$SRC/$f" ] && $SUDO cp "$SRC/$f" "$SHARE/"; done
fi

# -- the upgrade: what older Sonatas kept, carried over (nothing lost) ---------------------------
# The new Sonata is in place: only now can the old one's files go -- to a
# backup, never deleted. ~/.config/sonata2 and sonata2-data stay as they are.
BACKUP="$DATA/sonata2-backups/upgrade-$(date +%Y%m%d-%H%M%S)"
"$BIN/sonata2-rs" migrate user --backup "$BACKUP"
if [ "$MODE" = system ]; then
    $SUDO "$BIN/sonata2-rs" migrate install --prefix "$PREFIX" --system --backup "$PREFIX/share/sonata2-backups/$(basename "$BACKUP")"
else
    "$BIN/sonata2-rs" migrate install --prefix "$PREFIX" --backup "$BACKUP"
fi
# the "Sonata (Rust preview)" login entry (its launcher went to the backup above)
if [ -f "$PREVIEW_SESSION_FILE" ]; then
    mkdir -p "$BACKUP" && cp "$PREVIEW_SESSION_FILE" "$BACKUP/"
    sudo rm -f "$PREVIEW_SESSION_FILE" && echo "  \"Sonata (Rust preview)\" is now simply \"Sonata\" on the login screen"
fi
# the preview's "Calculator (Rust)"... entries (generated; Sonata's own are written below)
for f in "$APPS"/io.github.vinioliveiras.sonata2rs.*.desktop; do
    [ -e "$f" ] || continue
    mkdir -p "$BACKUP/applications" && mv "$f" "$BACKUP/applications/"
done
if [ -d "$BACKUP" ]; then echo "  what the old install left is in $BACKUP"; fi

# `sonata2` is Sonata: the session, Wayfire's keys, D-Bus services and every
# desktop entry (web apps' too, written with `sonata2 webapp`) start it so
$SUDO ln -sf sonata2-rs "$BIN/sonata2"
cat > "$tmp/sonata-session-launcher" <<EOF
#!/bin/sh
# Starts a Sonata session (the login screen's "Sonata" entry runs this).
# Everything up to Wayfire's start is traced to login.log (Wayfire and the
# shell then log to session.log next to it): in memory, or ~/.cache/sonata2
# with Settings > About > Logs on.
logdir="\${XDG_RUNTIME_DIR:-/tmp}/sonata2-logs"
if [ "\${SONATA_DEBUG:-}" = 1 ] || [ -e "\${XDG_CONFIG_HOME:-\$HOME/.config}/sonata2/debug-logging" ]; then
    logdir="\${XDG_CACHE_HOME:-\$HOME/.cache}/sonata2"
fi
mkdir -p "\$logdir"
exec > "\$logdir/login.log" 2>&1
date; echo "XDG_SESSION_TYPE=\$XDG_SESSION_TYPE WAYLAND_DISPLAY=\$WAYLAND_DISPLAY"
export PATH="$BIN:\$PATH" SONATA2_LAUNCHER="$BIN/sonata2"
exec bash -x "$SHARE/tools/sonata-session" "\$@"
EOF
$SUDO install -m 755 "$tmp/sonata-session-launcher" "$BIN/sonata-session"
# Sonata's apps in Launchpad and the app menus (their entries start `sonata2`)
if [ "$MODE" != system ]; then
    SONATA2_LAUNCHER="$BIN/sonata2" "$BIN/sonata2-rs" desktop-files >/dev/null || echo "  (the apps' entries are written at the next login)"
fi

# -- a terminal for apps that need one (xdg-terminal-exec) -----------------------------------------
# Apps with Terminal=true (CachyOS' update: its tray icon and "Run"
# notification did nothing) and GLib/GIO look for xdg-terminal-exec, then a
# fixed list Sonata's Terminal isn't on. Only when the system has none.
if ! command -v xdg-terminal-exec >/dev/null || [ "$(command -v xdg-terminal-exec)" = "$BIN/xdg-terminal-exec" ]; then
    cat > "$tmp/xdg-terminal-exec" <<'EOF'
#!/bin/sh
# Sonata's Terminal for "run this in a terminal" (install.sh): xdg-terminal-exec [-e] [cmd args...]
case "${1:-}" in -e|--) shift ;; esac
[ $# -eq 0 ] && exec "@BIN@/sonata2" terminal --new-window
cmd=""
for a in "$@"; do cmd="$cmd '$(printf %s "$a" | sed "s/'/'\\\\''/g")'"; done
exec "@BIN@/sonata2" terminal --exec "$cmd"
EOF
    sed -i "s|@BIN@|$BIN|g" "$tmp/xdg-terminal-exec"
    $SUDO install -m 755 "$tmp/xdg-terminal-exec" "$BIN/xdg-terminal-exec"
fi

# -- "Show in Folder" (org.freedesktop.FileManager1) opens Sonata's Files -------------------------
# In the Sonata session only; any other desktop still gets its own file manager.
DBUS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services"
mkdir -p "$DBUS_DIR"
cat > "$tmp/sonata-filemanager1" <<EOF
#!/bin/sh
# D-Bus starts this for org.freedesktop.FileManager1 (install.sh).
case ":\${XDG_CURRENT_DESKTOP:-}:" in
    *:Sonata:*) exec "$BIN/sonata2" files --service ;;
esac
for fm in "nautilus --gapplication-service" "nemo" "dolphin" "thunar --daemon"; do
    command -v \${fm%% *} >/dev/null && exec \$fm
done
exit 1
EOF
$SUDO install -m 755 "$tmp/sonata-filemanager1" "$BIN/sonata-filemanager1"
printf '[D-BUS Service]\nName=org.freedesktop.FileManager1\nExec=%s\n' "$BIN/sonata-filemanager1" \
    > "$DBUS_DIR/org.freedesktop.FileManager1.service"

# -- Sonata's Wayfire plugin (rounded corners for Chrome, Spotify, terminals...) -----------------
# Built against the installed Wayfire; a Wayfire update needs a rebuild (run
# ./install.sh again) -- until then Wayfire skips it and corners stay square.
PLUG_PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/wayfire/plugin-manager/install"
own_wf="$HOME/.local/opt/sonata-wayfire"
# The distro's Wayfire older than 0.11 (Sonata's plugins need its API and
# wlroots 0.20; Arch's 0.11.0 has them):
# Wayfire 0.12 and its wlroots built from source into their own folder, the
# session starts that one (tools/sonata-session), and Sonata's plugins and
# pixdecor are built against it. Without them: no Sonata title bars, round
# corners, zoom, outline resize... (Vini: a friend's Fedora had none).
wf_ver="$(wayfire --version 2>/dev/null | sed -n 's/^\([0-9]*\)\.\([0-9]*\).*/\1 \2/p' | head -n1)"
if [ -n "$wf_ver" ] && [ "$(echo "$wf_ver" | awk '{print ($1 * 1000 + $2 < 11) ? 1 : 0}')" = 1 ] &&
        [ -n "$BUILD_DEPS" ] && [ ! -s "$own_wf/standalone" ] && [ "$DEPS" = 1 ]; then
    # (no question: without it Sonata has no title bars, corners or window
    #  effects -- Vini: "it should just go ahead")
    say "Your Wayfire is $(echo "$wf_ver" | tr ' ' .): building Wayfire 0.12 for Sonata (10-20 minutes; its own folder: $own_wf)"
    $PM $NI $BUILD_DEPS >/dev/null 2>&1 || $PM $NI $BUILD_DEPS || true
    bash "$SRC/tools/build-wayfire-stack.sh" || echo "  (the system's Wayfire is used; retry: tools/build-wayfire-stack.sh)"
elif [ -s "$own_wf/standalone" ] && [ "$DEPS" = 1 ] && [ -n "$BUILD_DEPS" ]; then
    bash "$SRC/tools/build-wayfire-stack.sh"      # (a newer pin in this Sonata: built again; else nothing to do)
fi
if [ -s "$own_wf/standalone" ]; then
    # Sonata's plugins and pixdecor against that Wayfire, not the system's
    export PKG_CONFIG_PATH="$own_wf/lib/pkgconfig:$own_wf/share/pkgconfig:${PKG_CONFIG_PATH:-}"
fi
if [ "$DEPS" = 1 ] && [[ "$family" == *arch* ]] && ! command -v meson >/dev/null; then
    $PM $NI meson ninja >/dev/null 2>&1 || true
fi
if [ "$DEPS" = 1 ] && [[ "$family" == *arch* ]] && ! pkg-config --exists glm 2>/dev/null; then
    $PM $NI glm cmake >/dev/null 2>&1 || true  # pixdecor (title bars): glm, found through cmake
fi
if [ "$DEPS" = 1 ] && [[ "$family" == *arch* ]] && [ ! -f /usr/include/vulkan/vulkan_core.h ]; then
    $PM $NI vulkan-headers >/dev/null 2>&1 || true   # wlroots' headers include them (pixdecor)
fi
if command -v meson >/dev/null && command -v ninja >/dev/null && pkg-config --exists wayfire 2>/dev/null; then
    say "Building Sonata's Wayfire plugin (rounded window corners)"
    bdir="$(mktemp -d)"
    # staged, then renamed into place: a running Wayfire keeps its loaded copy
    # (overwriting a loaded library in place would crash it)
    if meson setup "$bdir" "$SRC/wayfire-plugin" --prefix "$PLUG_PREFIX" --libdir lib --buildtype release >"$bdir.log" 2>&1 &&
       ninja -C "$bdir" >>"$bdir.log" 2>&1 && meson install -C "$bdir" --destdir "$bdir/stage" >>"$bdir.log" 2>&1; then
        (cd "$bdir/stage$PLUG_PREFIX" && find . -type f) | while read -r f; do
            mkdir -p "$(dirname "$PLUG_PREFIX/$f")"
            cp "$bdir/stage$PLUG_PREFIX/$f" "$PLUG_PREFIX/$f.new" && mv -f "$PLUG_PREFIX/$f.new" "$PLUG_PREFIX/$f"
        done
        echo "  installed to $PLUG_PREFIX (log out and back in to load a new version)"
    else
        echo "  couldn't build it (window corners stay square); log: $bdir.log"
        grep -m8 -E "error|FAILED" "$bdir.log" | sed 's/^/    /'      # the reason, right here
    fi
    # pixdecor with long titles cut before the buttons and bigger button targets
    # (wayfire-plugin/pixdecor-title.patch, pixdecor-buttons.patch):
    # built into the same user folder, which Wayfire searches before the system's
    say "Building pixdecor (title bars) with Sonata's fixes"
    bash "$SRC/tools/build-pixdecor.sh" || echo "  (the installed pixdecor stays; retry: tools/build-pixdecor.sh)"
    # Wayfire itself with Sonata's fix for buffers the GPU refuses (its own folder;
    # tools/sonata-session uses it while it matches the installed Wayfire)
    # (again when Sonata's patches changed: wayfire-plugin/wayfire-*.patch)
    wf_patches="$(cat "$SRC"/wayfire-plugin/wayfire-*.patch 2>/dev/null | sha256sum | cut -c1-12)"
    if [ ! -s "$own_wf/standalone" ] && { ! wayfire --version 2>/dev/null | grep -qs -- "-$(head -n1 "$own_wf/sonata-commit" 2>/dev/null) " ||
            [ "$(cat "$own_wf/sonata-patches" 2>/dev/null)" != "$wf_patches" ]; }; then
        # (no question: Sonata's fixes -- Electron resizes, buffers the GPU refuses -- come with it)
        say "Building Wayfire with Sonata's fixes (a few minutes)"
        bash "$SRC/tools/build-wayfire.sh" || echo "  (the system's Wayfire is used; retry: tools/build-wayfire.sh)"
    fi
else
    echo "Skipped Sonata's Wayfire plugin (needs meson, ninja, a C++ compiler and Wayfire's headers)."
fi

# -- session config (never overwrite your edits) ----------------------------------------------------
mkdir -p "$CFG/sonata2"
def="$SRC/config/wayfire.ini"
mark="$CFG/sonata2/.wayfire.ini.installed"
if [ ! -f "$CFG/sonata2/wayfire.ini" ] || { [ -f "$mark" ] && cmp -s "$CFG/sonata2/wayfire.ini" "$mark"; }; then
    cp "$def" "$CFG/sonata2/wayfire.ini"
else
    cp "$def" "$CFG/sonata2/wayfire.ini.new"
    echo "Kept your edited $CFG/sonata2/wayfire.ini; the new default is wayfire.ini.new"
fi
cp "$def" "$mark"

# -- portals (file chooser, screenshots/screen sharing) for XDG_CURRENT_DESKTOP=Sonata ---------------
$SUDO mkdir -p "$PORTAL_DIR"
printf '[preferred]\ndefault=sonata;gtk\norg.freedesktop.impl.portal.FileChooser=sonata\norg.freedesktop.impl.portal.Settings=sonata\norg.freedesktop.impl.portal.Access=sonata\norg.freedesktop.impl.portal.Screenshot=wlr\norg.freedesktop.impl.portal.ScreenCast=wlr\n' \
    > "$tmp/sonata-portals.conf"
$SUDO install -m 644 "$tmp/sonata-portals.conf" "$PORTAL_DIR/sonata-portals.conf"
# Sonata's Open/Save panels (`sonata2 portal`): a portal backend D-Bus starts
# on demand; xdg-desktop-portal lists backends only from its system folder
printf '[portal]\nDBusName=org.freedesktop.impl.portal.desktop.sonata\nInterfaces=org.freedesktop.impl.portal.FileChooser;org.freedesktop.impl.portal.Settings;org.freedesktop.impl.portal.Access;\nUseIn=Sonata\n' \
    > "$tmp/sonata.portal"
sudo install -D -m 644 "$tmp/sonata.portal" /usr/share/xdg-desktop-portal/portals/sonata.portal || true
printf '[D-BUS Service]\nName=org.freedesktop.impl.portal.desktop.sonata\nExec=%s portal\n' "$BIN/sonata2" \
    > "$DBUS_DIR/org.freedesktop.impl.portal.desktop.sonata.service"

# Screen sharing (Chrome, Firefox, Discord, OBS...): xdg-desktop-portal-wlr
# captures, Sonata's picker (`sonata2 share-picker`) asks which
# screen. Its config for XDG_CURRENT_DESKTOP=Sonata only (other desktops keep theirs).
mkdir -p "$CFG/xdg-desktop-portal-wlr"
printf '[screencast]\nchooser_type=dmenu\nchooser_cmd=%s share-picker\nmax_fps=60\n' "$BIN/sonata2" \
    > "$CFG/xdg-desktop-portal-wlr/Sonata"
systemctl --user try-restart xdg-desktop-portal-wlr.service 2>/dev/null || true
# the Open/Save panels (Sonata's portal) run all session: the new code on next use
pkill -f -- "sonata2(-rs)? portal( |$)" 2>/dev/null || true

# -- login screen entry ---------------------------------------------------------------------------------
# The login screen checks TryExec as its own user (gdm, sddm...), which can't
# look inside a private home folder: the entry points to a small system-wide
# starter that runs the user's own launcher once logged in.
LOGIN_BIN=/usr/local/bin/sonata-login
cat > "$tmp/sonata-login" <<'EOF'
#!/bin/sh
# "Sonata" on the login screen (install.sh): this user's Sonata, else a
# system-wide one (install.sh --system).
for s in "$HOME/.local/bin/sonata-session" /usr/local/bin/sonata-session; do
    [ -x "$s" ] && exec "$s" "$@"
done
echo "Sonata is not installed for $USER (run install.sh)" >&2
exit 1
EOF
cat > "$tmp/sonata.desktop" <<EOF
[Desktop Entry]
Name=Sonata
Comment=Sonata 2 desktop (Wayfire + Sonata shell)
Exec=$LOGIN_BIN
TryExec=$LOGIN_BIN
Type=Application
DesktopNames=Sonata
EOF
if [ -d /usr/share/wayland-sessions ] || [ "$MODE" = system ]; then
    # always: a Sonata you can't log in to isn't installed (sudo asks for the password)
    if sudo install -D -m 755 "$tmp/sonata-login" "$LOGIN_BIN" &&
        sudo install -D -m 644 "$tmp/sonata.desktop" "$SESSION_FILE"; then
        echo "Login screen: \"Sonata\" added."
    else
        echo "The login screen entry couldn't be added (sudo refused): run ./install.sh again to add it."
    fi
fi
# GDM lists Wayland sessions only when it runs on Wayland itself.
if grep -Eqs '^[[:space:]]*WaylandEnable[[:space:]]*=[[:space:]]*false' /etc/gdm/custom.conf /etc/gdm3/custom.conf /etc/gdm3/daemon.conf; then
    say "GDM has Wayland turned off (WaylandEnable=false in its custom.conf): Sonata won't be listed."
    echo "  Remove that line (or set it to true) and restart the computer."
fi

# -- a quiet console: no kernel messages or "[ OK ]" lines between screens ----------------------------
"$SRC/tools/quiet-console.sh" install || true

# -- disks: administrators mount system disks / unlock system LUKS without a password ---------------------
# (udisks' "mount a system disk" action, for wheel/sudo members at a local, active session). On by
# default, never asked (Vini's call, knowing it lowers security); --no-mount-without-password removes it.
MOUNT_RULES=/etc/polkit-1/rules.d/50-sonata2-mount.rules
rule='polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.udisks2.filesystem-mount-system" ||
         action.id == "org.freedesktop.udisks2.filesystem-mount" ||
         action.id == "org.freedesktop.udisks2.encrypted-unlock-system") &&
        subject.local && subject.active && (subject.isInGroup("wheel") || subject.isInGroup("sudo")))
        return polkit.Result.YES;
});'
if [ "$MOUNTRULE" = 0 ]; then
    sudo rm -f "$MOUNT_RULES" 2>/dev/null && echo "Disks: system disks ask for a password again."
elif [ -f "$MOUNT_RULES" ]; then
    echo "Disks: system disks mount without a password (--no-mount-without-password removes that)."
elif [ -d /etc/polkit-1/rules.d ] || sudo mkdir -p /etc/polkit-1/rules.d 2>/dev/null; then
    printf '%s\n' "$rule" | sudo tee "$MOUNT_RULES" >/dev/null && \
        echo "Disks: mounted without a password (polkit rule 50-sonata2-mount; --no-mount-without-password removes it)."
fi

# -- updates: the password once per update, not at every package (Vini) --------------------------------
# pkexec pacman (Settings > Software Update, and the AUR helper's every step) asked each time; this rule
# keeps the administrator's "yes" for a few minutes, for that caller only (backend/updates.py).
UPDATE_RULE=/etc/polkit-1/rules.d/60-sonata2-updates.rules
if command -v pacman >/dev/null 2>&1 && ! cmp -s "$SRC/config/polkit/60-sonata2-updates.rules" "$UPDATE_RULE"; then
    if [ -d /etc/polkit-1/rules.d ] || sudo mkdir -p /etc/polkit-1/rules.d 2>/dev/null; then
        sudo install -m 644 "$SRC/config/polkit/60-sonata2-updates.rules" "$UPDATE_RULE" && \
            echo "Updates: the password is asked once per update (polkit rule 60-sonata2-updates)."
    fi
fi

# -- what Settings would otherwise ask to install: printing on, USB protection set up -------------------
if command -v systemctl >/dev/null 2>&1; then
    if [ -e /usr/lib/systemd/system/cups.socket ] && ! systemctl is-enabled cups.socket >/dev/null 2>&1; then
        sudo systemctl enable --now cups.socket >/dev/null 2>&1 && echo "Printing: CUPS on (Settings > Printers)."
    fi
    # The firewall on by default (Vini), once: turned off later in Settings, a new install leaves it
    # off. Nothing comes in unasked; from the local network (and Tailscale) only Screen Sharing (VNC,
    # its own password, off until turned on) and printer discovery (mDNS).
    FW_DONE="${XDG_CONFIG_HOME:-$HOME/.config}/sonata2/.firewall-default"
    if [ "$FIREWALL" != 0 ] && command -v ufw >/dev/null 2>&1 && [ ! -e "$FW_DONE" ]; then
        if sudo sh -c 'ufw default deny incoming && ufw default allow outgoing &&
                for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10; do
                    ufw allow from "$net" to any port 5900 proto tcp comment "Sonata Screen Sharing" &&
                    ufw allow from "$net" to any port 5353 proto udp comment "printers (mDNS)" || exit 1
                done && systemctl enable --now ufw && ufw --force enable' >/dev/null; then
            echo "Firewall: on (Settings > Security & Privacy; --no-firewall skips this)."
        else
            echo "Firewall: couldn't be turned on -- Settings > Security & Privacy > Firewall."
        fi
        mkdir -p "$(dirname "$FW_DONE")" && touch "$FW_DONE"
    fi
    # USBGuard on its own blocks every USB device it has no rule for -- the keyboard and mouse too.
    # Sonata's set-up (backend/usbprotect.py) allows them all while unlocked and blocks only the ones
    # plugged in while the screen is locked.
    USB_RULE=/etc/polkit-1/rules.d/70-sonata2-usbguard.rules
    if command -v usbguard >/dev/null 2>&1 && [ ! -f "$USB_RULE" ] && [ "$USBGUARD" != 0 ]; then
        echo "USB protection: careful -- USBGuard on its own blocks every USB device it has no rule for,"
        echo "  keyboard and mouse included. Sonata sets it up to allow every device while unlocked and"
        echo "  block only new ones plugged in while the screen is locked (--no-usbguard skips this)."
        setup="$("$BIN/sonata2-rs" settings-usb-setup 2>/dev/null || true)"
        if [ -n "$setup" ] && sudo sh -c "$setup" >/dev/null; then
            echo "USB protection: on (Settings > Security & Privacy > USB)."
        else
            echo "USB protection: not set up -- Settings > Security & Privacy > USB > Set Up."
        fi
    fi
fi

# -- Bazaar, the app store (Flatpak apps from Flathub) ----------------------------------------------
BAZAAR_ID=io.github.kolunmi.Bazaar
if [ "$BAZAAR" != 0 ] && command -v flatpak >/dev/null 2>&1; then
    if flatpak info "$BAZAAR_ID" >/dev/null 2>&1; then
        :
    elif sudo flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo && \
            sudo flatpak install -y --noninteractive flathub "$BAZAAR_ID"; then
        echo "Bazaar: installed (the app store; --no-bazaar skips it)."
    else
        echo "Bazaar: couldn't be installed now -- flatpak install flathub $BAZAAR_ID"
    fi
fi

# -- Sonata's login screen (greetd) -------------------------------------------------------------------
if [ -x /usr/local/bin/sonata-greeter ] && [ "$GREETER" != 0 ]; then
    SONATA2_BIN="$BIN/sonata2-rs" "$SRC/tools/greeter-setup.sh" install >/dev/null && echo "Login screen: updated to this version."
elif [ "$GREETER" = 1 ] || { [ "$GREETER" = ask ] && ask "Use Sonata's own login screen (replaces GDM/SDDM from the next boot; ./install.sh --gdm undoes it)?"; }; then
    say "Sonata's login screen (greetd)"
    SONATA2_BIN="$BIN/sonata2-rs" "$SRC/tools/greeter-setup.sh" install
else
    echo "Login screen: kept as it is (./install.sh --greeter to use Sonata's)."
fi

case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not in your PATH (the session adds it itself)." ;; esac
say "Checking this computer (sonata2 doctor):"
"$BIN/sonata2-rs" doctor || true
say "Done. Log out and pick \"Sonata\" on the login screen."
