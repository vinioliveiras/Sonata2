#!/usr/bin/env bash
# Sonata 2 installer -- works on any distro (no distro-specific paths).
#
#   ./install.sh              install for this user (~/.local), ask for sudo
#                             only to add "Sonata" to the login screen
#   ./install.sh --system     install for all users (/usr/local, needs sudo)
#   (dependencies)            always checked and installed with the distro's
#                             package manager (pacman/apt/dnf/zypper/xbps/apk):
#                             required ones, then every optional one (Wi-Fi,
#                             screenshots, Night Shift...), pywayland from PyPI
#                             if needed; pixdecor built from source
#   ./install.sh --no-deps    only check them (print what's missing)
#   ./install.sh --yes        don't ask (login-screen entry included)
#   ./install.sh --uninstall  remove it again (your settings stay)
#   ./install.sh --dev        link to this clone instead of copying it: the
#                             session runs the code you are editing
#                             (`sonata2 restart` reloads the shell)
#   (login screen)            Sonata's own login screen (greetd in place of
#                             GDM/SDDM, from the next boot) is set up too:
#                             asked once, then kept up to date
#   ./install.sh --no-greeter keep the current login screen
#   ./install.sh --gdm        back to the previous login screen (only that)
#   ./install.sh --mount-without-password
#                             let administrators mount system disks and unlock
#                             system LUKS without a password (a polkit rule;
#                             otherwise asked, default No; --yes never adds it)
#   ./install.sh --no-mount-without-password  remove that rule again
#
# Installs: the sonata2 package + themes/icons -> <prefix>/share/sonata2,
# launchers `sonata2` and `sonata-session` -> <prefix>/bin, the session
# entry -> /usr/share/wayland-sessions/sonata.desktop, the session's Wayfire
# config -> ~/.config/sonata2/wayfire.ini (an edited copy is kept), portal
# preferences for the "Sonata" desktop.
set -euo pipefail
# a step that fails stops the install: say which (a friend's install ended without a word)
trap 's=$?; echo "install.sh stopped at line $LINENO (exit $s): $BASH_COMMAND" >&2; echo "Please send this line to the Sonata developers." >&2' ERR
SRC="$(cd "$(dirname "$0")" && pwd)"
MODE=user DEPS=1 YES=0 UNINSTALL=0 DEV=0 GREETER=ask MOUNTRULE=ask
for a in "$@"; do
    case "$a" in
        --system) MODE=system ;; --deps) DEPS=1 ;; --no-deps) DEPS=0 ;; --yes|-y) YES=1 ;; --uninstall) UNINSTALL=1 ;; --dev) DEV=1 ;;
        --greeter) GREETER=1 ;; --no-greeter) GREETER=0 ;;
        --mount-without-password) MOUNTRULE=1 ;; --no-mount-without-password) MOUNTRULE=0 ;; --gdm) exec "$(dirname "$0")/tools/greeter-setup.sh" revert ;;
        -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d'; exit 0 ;;
        *) echo "unknown option: $a (see --help)"; exit 2 ;;
    esac
done

if [ "$MODE" = system ]; then PREFIX=/usr/local; SUDO=sudo; else PREFIX="$HOME/.local"; SUDO=""; fi
[ "$(id -u)" = 0 ] && SUDO=""
SHARE="$PREFIX/share/sonata2"
BIN="$PREFIX/bin"
SESSION_FILE=/usr/share/wayland-sessions/sonata.desktop
CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
if [ "$MODE" = system ]; then PORTAL_DIR=/usr/share/xdg-desktop-portal; else PORTAL_DIR="$CFG/xdg-desktop-portal"; fi

say() { printf '\033[1m%s\033[0m\n' "$*"; }
# Your data (notes, calendars, TextEdit's open tabs) used to
# live inside the install folder ($SHARE), which an update replaces (and a dev
# install links to the git clone): carry it to ~/.local/share/sonata2-data
# (sonata2/userdata.py) before $SHARE goes.
carry_data() {
    local data="${XDG_DATA_HOME:-$HOME/.local/share}" src app
    [ "$SHARE" = "$HOME/.local/share/sonata2" ] || [ "$SHARE" = "$data/sonata2" ] || return 0
    src="$(readlink -f "$SHARE" 2>/dev/null || true)"
    [ -n "$src" ] && [ -d "$src" ] || return 0
    for app in notes calendar textedit; do
        if [ -d "$src/$app" ] && [ ! -e "$data/sonata2-data/$app" ]; then
            mkdir -p "$data/sonata2-data" && mv "$src/$app" "$data/sonata2-data/$app" &&
                echo "  your $app data is in $data/sonata2-data/$app"
        fi
    done
}
ask() { [ "$YES" = 1 ] && return 0; read -r -p "$1 [Y/n] " r; [ -z "$r" ] || [[ "$r" =~ ^[YySs] ]]; }
# default No (security-relevant choices): --yes answers No, only an explicit flag says yes
ask_no() { [ "$YES" = 1 ] && return 1; read -r -p "$1 [y/N] " r; [[ "$r" =~ ^[YySs] ]]; }

# -- uninstall ---------------------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
    say "Removing Sonata 2 from $PREFIX"
    carry_data                           # (your notes and calendars stay)
    $SUDO rm -rf "$SHARE"
    $SUDO rm -f "$BIN/sonata2" "$BIN/sonata-session" "$PORTAL_DIR/sonata-portals.conf"
    if [ -f "$SESSION_FILE" ] && ask "Remove \"Sonata\" from the login screen (sudo)?"; then
        sudo rm -f "$SESSION_FILE" /usr/local/bin/sonata-login
    fi
    rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services/org.freedesktop.impl.portal.desktop.sonata.service"
    sudo rm -f /usr/share/xdg-desktop-portal/portals/sonata.portal 2>/dev/null || true
    rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services/org.freedesktop.FileManager1.service" \
          "$BIN/sonata-filemanager1"
    grep -qs "Sonata's Terminal" "$BIN/xdg-terminal-exec" && $SUDO rm -f "$BIN/xdg-terminal-exec"
    rm -f "$HOME/.local/share/applications/sonata2-launchpad.desktop" \
          "$HOME/.local/share/applications/sonata2-settings.desktop" \
          "$HOME/.local/share/applications/sonata2-screenshot.desktop"
    "$(dirname "$0")/tools/quiet-console.sh" revert || true
    sudo rm -f /etc/polkit-1/rules.d/50-sonata2-mount.rules 2>/dev/null || true
    rm -rf "$HOME/.local/opt/sonata-wayfire"
    say "Done. Your settings are still in $CFG/sonata2 (delete that folder to reset them)."
    exit 0
fi

# -- dependencies ------------------------------------------------------------------------------
# what Sonata needs from Python (also asked again once the packages are in)
missing_python() {
    python3 - <<'PY' 2>/dev/null || echo "python3"
import gi, sys
missing = []
for ns, v in (("Gtk", "4.0"), ("Adw", "1"), ("Gtk4LayerShell", "1.0")):
    try:
        gi.require_version(ns, v)
        mod = __import__("gi.repository." + ns, fromlist=[ns])
        if ns == "Gtk4LayerShell":
            mod.get_major_version()   # the library itself (Ubuntu: its gir package came without it)
    except Exception as e:
        missing.append(f"{ns}-{v}")
        print(f"  {ns} {v}: {e}", file=sys.stderr)
for mod in ("pywayland", "cairo"):
    try:
        __import__(mod)
    except ImportError:
        missing.append(mod)
try:
    gi.require_foreign("cairo")
except ImportError:
    missing.append("gi-cairo")
print(" ".join(missing))
PY
}
missing_py="$(missing_python)"
missing="$missing_py"
command -v wayfire >/dev/null || missing="$missing wayfire"
missing="$(echo "$missing" | xargs)"

. /etc/os-release 2>/dev/null || true
family="${ID:-} ${ID_LIKE:-}"
# Package names per family (checked against the distros' archives, 2026-09).
# Needs: Wayfire >= 0.9, GTK >= 4.12, libadwaita >= 1.4, gtk4-layer-shell >= 1.0,
# Python >= 3.10 with PyGObject, pycairo and pywayland. Known good: Arch and its
# derivatives (CachyOS, EndeavourOS, Manjaro), Fedora 41+, Debian 13+,
# Ubuntu 25.04+ / derivatives, openSUSE Tumbleweed.
NI=""      # the package manager's "don't ask" flag, for optional packages one by one
case "$family" in
    *arch*)   PM="sudo pacman -S --needed"; NI="--noconfirm"
              PKGS="wayfire gtk4 libadwaita gtk4-layer-shell python-gobject python-cairo python-pywayland"
              OPT="vte4 networkmanager wireplumber brightnessctl bluez-utils wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk gnome-keyring libsecret keepassxc libpulse xorg-xwayland grim slurp wl-clipboard ffmpegthumbnailer ffmpeg webp-pixbuf-loader gamemode gtksourceview5 gst-plugins-good gst-plugins-bad gst-libav python-mutagen udisks2 wf-recorder wlsunset wtype swayidle openssl meson ninja ddcutil openrgb webkitgtk-6.0 wayvnc" ;;
    *debian*|*ubuntu*) PM="sudo apt install"; NI="-y"
              PKGS="wayfire gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk4layershell-1.0 libgtk4-layer-shell0 python3-gi python3-gi-cairo python3-pywayland python3-cffi-backend"
              OPT="gir1.2-vte-3.91 network-manager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk gir1.2-polkit-1.0 gnome-keyring keepassxc pulseaudio-utils xwayland grim slurp wl-clipboard ffmpegthumbnailer ffmpeg webp-pixbuf-loader gamemode gir1.2-gtksource-5 gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-libav python3-mutagen udisks2 wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb gir1.2-webkit-6.0 wayvnc" ;;
    *fedora*|*rhel*) PM="sudo dnf install"; NI="-y"
              PKGS="wayfire gtk4 libadwaita gtk4-layer-shell gobject-introspection python3-gobject python3-cairo python3-pywayland"
              OPT="vte291-gtk4 NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk gnome-keyring keepassxc pulseaudio-utils xorg-x11-server-Xwayland grim slurp wl-clipboard ffmpegthumbnailer ffmpeg-free webp-pixbuf-loader gamemode gtksourceview5 gstreamer1-plugins-good gstreamer1-plugins-bad-free python3-mutagen udisks2 wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb webkitgtk6.0 wayvnc" ;;
    *suse*)   PM="sudo zypper install"; NI="-y"
              PKGS="wayfire gtk4 libadwaita-1-0 typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 gtk4-layer-shell python3-gobject python3-gobject-cairo python3-pywayland"
              OPT="typelib-1_0-Vte-3_91 NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk grim slurp wl-clipboard ffmpegthumbnailer ffmpeg wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb typelib-1_0-WebKit-6_0" ;;
    *void*)   PM="sudo xbps-install"; NI="-y"
              PKGS="wayfire gtk4 libadwaita gtk4-layer-shell python3-gobject python3-cairo python3-pywayland"
              OPT="vte3-gtk4 NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk grim slurp wl-clipboard ffmpegthumbnailer ffmpeg wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb" ;;
    *alpine*) PM="sudo apk add"; NI=""
              PKGS="wayfire gtk4.0 libadwaita gtk4-layer-shell py3-gobject3 py3-cairo py3-pywayland"
              OPT="vte3-gtk4 networkmanager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-wlr xdg-desktop-portal-gtk grim slurp wl-clipboard ffmpegthumbnailer ffmpeg wf-recorder wlsunset wtype swayidle openssl ddcutil openrgb" ;;
    *)        PM=""; PKGS=""; OPT="" ;;
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
        echo "Install Wayfire, GTK 4, libadwaita, gtk4-layer-shell (+ GObject introspection),"
        echo "PyGObject with cairo support and pywayland with your package manager."
    fi
    # pywayland isn't packaged everywhere: pip, for this user -- it builds a
    # part of it, so pip and the build tools first. (Ubuntu 25.04's package
    # lacks its python3-cffi-backend dependency: "No module named
    # pywayland._ffi" -- it's in PKGS above.)
    if ! python3 -c "import pywayland" 2>/dev/null; then
        say "pywayland from PyPI (this user only)"
        if [ "$DEPS" = 1 ]; then
            case "$family" in
                *debian*|*ubuntu*) $PM $NI python3-pip python3-dev python3-cffi libwayland-dev gcc pkg-config || true ;;
                *fedora*|*rhel*) $PM $NI python3-pip python3-devel python3-cffi wayland-devel gcc pkgconf || true ;;
                *arch*) $PM $NI python-pip python-cffi wayland gcc pkgconf || true ;;
            esac
        fi
        python3 -m pip install --user pywayland 2>/dev/null || \
            python3 -m pip install --user --break-system-packages pywayland || true
    fi
fi
# still missing once the packages are in: Sonata can't start -- say so and stop
# (Fedora: GTK needs the cairo typelib from gobject-introspection; the install
#  said it was done and Sonata wouldn't open)
if [ -n "$missing" ] && [ "$DEPS" = 1 ]; then
    still="$(missing_python 2>/tmp/sonata-missing.$$ | xargs)"
    if [ -n "$still" ]; then
        say "Sonata can't start without: $still"
        cat /tmp/sonata-missing.$$ >&2 2>/dev/null; rm -f /tmp/sonata-missing.$$
        echo "Install them with your package manager and run ./install.sh again."
        exit 1
    fi
    rm -f /tmp/sonata-missing.$$
fi
# Versions (older ones are the usual reason something doesn't show up)
python3 - <<'PY' || true
import sys
bad = []
if sys.version_info < (3, 10):
    bad.append(f"Python {sys.version.split()[0]} (needs 3.10+)")
try:
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 12):
        bad.append(f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()} (needs 4.12+)")
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 4):
        bad.append(f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()} (needs 1.4+)")
except Exception:
    pass
if bad:
    print("\033[1mToo old for Sonata:\033[0m " + ", ".join(bad) + " -- a newer distro release is needed.")
PY
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

# -- files ----------------------------------------------------------------------------------------
say "Installing Sonata 2 to $PREFIX"
$SUDO mkdir -p "$SHARE" "$BIN"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/sonata2/tools"
# Package (no caches), session tools, default config, licences.
items=(sonata2 config)
for f in "$SRC"/LICENSE* "$SRC/README.md"; do [ -e "$f" ] && items+=("$(basename "$f")"); done
( cd "$SRC" && tar --exclude=__pycache__ -cf - "${items[@]}" ) | tar -xf - -C "$tmp/sonata2"
cp "$SRC/tools/session-env.sh" "$SRC/tools/sonata-session" "$SRC/tools/wayfire-config.sh" "$tmp/sonata2/tools/"
carry_data
$SUDO mkdir -p "$(dirname "$SHARE")"
if [ "$DEV" = 1 ]; then
    $SUDO rm -rf "$SHARE"
    $SUDO ln -s "$SRC" "$SHARE"          # the session runs this clone's code
    echo "Dev install: $SHARE -> $SRC"
elif [ -d "$SHARE" ] && [ ! -L "$SHARE" ]; then
    # updated in place: unchanged files (the bundled font, icons, sounds) keep
    # their inode -- the running Dock lost its font when the copy was deleted
    # and every name label went blank (Vini)
    $SUDO python3 "$SRC/tools/sync-tree.py" "$tmp/sonata2" "$SHARE"
else
    $SUDO rm -rf "$SHARE"
    $SUDO cp -a "$tmp/sonata2" "$SHARE"
fi

cat > "$tmp/sonata2-launcher" <<EOF
#!/bin/sh
# Sonata 2 launcher (installed by install.sh): sonata2 <dock|topbar|launchpad|settings|...>
SONATA_HOME="$SHARE"
export PYTHONPATH="\$SONATA_HOME\${PYTHONPATH:+:\$PYTHONPATH}"
export SONATA2_LAUNCHER="$BIN/sonata2"
exec python3 -m sonata2 "\$@"
EOF
cat > "$tmp/sonata-session-launcher" <<EOF
#!/bin/sh
# Starts a Sonata session (the login screen's "Sonata" entry runs this).
# Everything up to Wayfire's start is traced to ~/.cache/sonata2/login.log
# (Wayfire and the shell then log to session.log next to it).
logdir="\${XDG_CACHE_HOME:-\$HOME/.cache}/sonata2"; mkdir -p "\$logdir"
exec > "\$logdir/login.log" 2>&1
date; echo "XDG_SESSION_TYPE=\$XDG_SESSION_TYPE WAYLAND_DISPLAY=\$WAYLAND_DISPLAY"
export PATH="$BIN:\$PATH" SONATA2_LAUNCHER="$BIN/sonata2"
exec bash -x "$SHARE/tools/sonata-session" "\$@"
EOF
$SUDO install -m 755 "$tmp/sonata2-launcher" "$BIN/sonata2"
$SUDO install -m 755 "$tmp/sonata-session-launcher" "$BIN/sonata-session"
$SUDO chmod 755 "$SHARE/tools/sonata-session" "$SHARE/tools/session-env.sh" "$SHARE/tools/wayfire-config.sh"

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
        [ -n "$BUILD_DEPS" ] && [ ! -s "$own_wf/standalone" ] && [ "$DEPS" = 1 ] &&
        ask "Your Wayfire is $(echo "$wf_ver" | tr ' ' .); build Wayfire 0.12 for Sonata's title bars, corners and window effects (10-20 minutes)?"; then
    say "Building Wayfire 0.12 (its own folder: $own_wf)"
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
    # pixdecor with long titles cut before the buttons (wayfire-plugin/pixdecor-title.patch):
    # built into the same user folder, which Wayfire searches before the system's
    say "Building pixdecor (title bars) with Sonata's title fix"
    bash "$SRC/tools/build-pixdecor.sh" || echo "  (the installed pixdecor stays; retry: tools/build-pixdecor.sh)"
    # Wayfire itself with Sonata's fix for buffers the GPU refuses (its own folder;
    # tools/sonata-session uses it while it matches the installed Wayfire)
    if [ ! -s "$own_wf/standalone" ] && ! wayfire --version 2>/dev/null | grep -qs -- "-$(head -n1 "$own_wf/sonata-commit" 2>/dev/null) " &&
            ask "Build Wayfire with Sonata's crash fix (a few minutes)?"; then
        say "Building Wayfire with Sonata's fix"
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
printf '[preferred]\ndefault=sonata;gtk\norg.freedesktop.impl.portal.FileChooser=sonata\norg.freedesktop.impl.portal.Settings=sonata\norg.freedesktop.impl.portal.Screenshot=wlr\norg.freedesktop.impl.portal.ScreenCast=wlr\n' \
    > "$tmp/sonata-portals.conf"
$SUDO install -m 644 "$tmp/sonata-portals.conf" "$PORTAL_DIR/sonata-portals.conf"
# Sonata's Open/Save panels (sonata2/portal.py): a portal backend D-Bus starts
# on demand; xdg-desktop-portal lists backends only from its system folder
printf '[portal]\nDBusName=org.freedesktop.impl.portal.desktop.sonata\nInterfaces=org.freedesktop.impl.portal.FileChooser;org.freedesktop.impl.portal.Settings;\nUseIn=Sonata\n' \
    > "$tmp/sonata.portal"
sudo install -D -m 644 "$tmp/sonata.portal" /usr/share/xdg-desktop-portal/portals/sonata.portal || true
printf '[D-BUS Service]\nName=org.freedesktop.impl.portal.desktop.sonata\nExec=%s portal\n' "$BIN/sonata2" \
    > "$DBUS_DIR/org.freedesktop.impl.portal.desktop.sonata.service"

# Screen sharing (Chrome, Firefox, Discord, OBS...): xdg-desktop-portal-wlr
# captures, Sonata's picker (sonata2/shell/sharepicker.py) asks which
# screen. Its config for XDG_CURRENT_DESKTOP=Sonata only (other desktops keep theirs).
mkdir -p "$CFG/xdg-desktop-portal-wlr"
printf '[screencast]\nchooser_type=dmenu\nchooser_cmd=%s share-picker\nmax_fps=60\n' "$BIN/sonata2" \
    > "$CFG/xdg-desktop-portal-wlr/Sonata"
systemctl --user try-restart xdg-desktop-portal-wlr.service 2>/dev/null || true
# the Open/Save panels (Sonata's portal) run all session: the new code on next use
pkill -f -- "sonata2 portal( |$)" 2>/dev/null || true

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

# -- disks (opt-in): administrators mount system disks / unlock system LUKS without a password -------
# (udisks' "mount a system disk" action, for wheel/sudo members at a local, active session). It lowers
# security, so it is never added unasked: asked (default No), or --mount-without-password.
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
elif [ -f "$MOUNT_RULES" ] && [ "$MOUNTRULE" = ask ]; then
    echo "Disks: system disks mount without a password (--no-mount-without-password removes that)."
elif [ "$MOUNTRULE" = 1 ] || ask_no "Mount system disks and unlock encrypted system disks without asking for a password (administrators only; lowers security)?"; then
    if [ -d /etc/polkit-1/rules.d ] || sudo mkdir -p /etc/polkit-1/rules.d 2>/dev/null; then
        printf '%s\n' "$rule" | sudo tee "$MOUNT_RULES" >/dev/null && \
            echo "Disks: mounted at login without a password (polkit rule 50-sonata2-mount)."
    fi
else
    echo "Disks: system disks ask for the administrator password (--mount-without-password to change)."
fi

# -- Sonata's login screen (greetd) -------------------------------------------------------------------
if [ -x /usr/local/bin/sonata-greeter ] && [ "$GREETER" != 0 ]; then
    "$SRC/tools/greeter-setup.sh" install >/dev/null && echo "Login screen: updated to this version."
elif [ "$GREETER" = 1 ] || { [ "$GREETER" = ask ] && ask "Use Sonata's own login screen (replaces GDM/SDDM from the next boot; ./install.sh --gdm undoes it)?"; }; then
    say "Sonata's login screen (greetd)"
    "$SRC/tools/greeter-setup.sh" install
else
    echo "Login screen: kept as it is (./install.sh --greeter to use Sonata's)."
fi

case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not in your PATH (the session adds it itself)." ;; esac
say "Checking this computer (sonata2 doctor):"
PYTHONPATH="$SHARE" python3 -m sonata2 doctor || true
say "Done. Log out and pick \"Sonata\" on the login screen, or try it inside your desktop with:"
echo "  $SRC/tools/dev-session.sh"
