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
#                             if needed, pixdecor from the AUR on Arch
#   ./install.sh --no-deps    only check them (print what's missing)
#   ./install.sh --yes        don't ask (login-screen entry included)
#   ./install.sh --uninstall  remove it again (your settings stay)
#   ./install.sh --dev        link to this clone instead of copying it: the
#                             session runs the code you are editing
#                             (`sonata2 restart` reloads the shell)
#   ./install.sh --greeter    also use Sonata's own login screen (greetd in
#                             place of GDM/SDDM; from the next boot)
#   ./install.sh --gdm        back to the previous login screen (only that)
#
# Installs: the sonata2 package + themes/icons -> <prefix>/share/sonata2,
# launchers `sonata2` and `sonata-session` -> <prefix>/bin, the session
# entry -> /usr/share/wayland-sessions/sonata.desktop, the session's Wayfire
# config -> ~/.config/sonata2/wayfire.ini (an edited copy is kept), portal
# preferences for the "Sonata" desktop.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
MODE=user DEPS=1 YES=0 UNINSTALL=0 DEV=0 GREETER=0
for a in "$@"; do
    case "$a" in
        --system) MODE=system ;; --deps) DEPS=1 ;; --no-deps) DEPS=0 ;; --yes|-y) YES=1 ;; --uninstall) UNINSTALL=1 ;; --dev) DEV=1 ;;
        --greeter) GREETER=1 ;; --gdm) exec "$(dirname "$0")/tools/greeter-setup.sh" revert ;;
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
ask() { [ "$YES" = 1 ] && return 0; read -r -p "$1 [Y/n] " r; [ -z "$r" ] || [[ "$r" =~ ^[YySs] ]]; }

# -- uninstall ---------------------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
    say "Removing Sonata 2 from $PREFIX"
    $SUDO rm -rf "$SHARE"
    $SUDO rm -f "$BIN/sonata2" "$BIN/sonata-session" "$PORTAL_DIR/sonata-portals.conf"
    if [ -f "$SESSION_FILE" ] && ask "Remove \"Sonata\" from the login screen (sudo)?"; then
        sudo rm -f "$SESSION_FILE" /usr/local/bin/sonata-login
    fi
    rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services/org.freedesktop.FileManager1.service" \
          "$BIN/sonata-filemanager1"
    rm -f "$HOME/.local/share/applications/sonata2-launchpad.desktop" \
          "$HOME/.local/share/applications/sonata2-settings.desktop"
    say "Done. Your settings are still in $CFG/sonata2 (delete that folder to reset them)."
    exit 0
fi

# -- dependencies ------------------------------------------------------------------------------
missing_py="$(python3 - <<'PY' 2>/dev/null || echo "python3"
import gi
missing = []
for ns, v in (("Gtk", "4.0"), ("Adw", "1"), ("Gtk4LayerShell", "1.0")):
    try:
        gi.require_version(ns, v)
        __import__("gi.repository." + ns)
    except (ValueError, ImportError):
        missing.append(f"{ns}-{v}")
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
)"
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
              OPT="networkmanager wireplumber brightnessctl bluez-utils wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr polkit-gnome gnome-keyring libpulse xorg-xwayland grim slurp wl-clipboard ffmpegthumbnailer wf-recorder wlsunset wtype swayidle openssl meson ninja" ;;
    *debian*|*ubuntu*) PM="sudo apt install"; NI="-y"
              PKGS="wayfire gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk4layershell-1.0 python3-gi python3-gi-cairo python3-pywayland"
              OPT="network-manager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr policykit-1-gnome gnome-keyring pulseaudio-utils xwayland grim slurp wl-clipboard ffmpegthumbnailer wf-recorder wlsunset wtype swayidle openssl" ;;
    *fedora*|*rhel*) PM="sudo dnf install"; NI="-y"
              PKGS="wayfire gtk4 libadwaita gtk4-layer-shell python3-gobject python3-cairo python3-pywayland"
              OPT="NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr polkit-gnome gnome-keyring pulseaudio-utils xorg-x11-server-Xwayland grim slurp wl-clipboard ffmpegthumbnailer wf-recorder wlsunset wtype swayidle openssl" ;;
    *suse*)   PM="sudo zypper install"; NI="-y"
              PKGS="wayfire gtk4 libadwaita-1-0 typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 gtk4-layer-shell python3-gobject python3-gobject-cairo python3-pywayland"
              OPT="NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr grim slurp wl-clipboard ffmpegthumbnailer wf-recorder wlsunset wtype swayidle openssl" ;;
    *void*)   PM="sudo xbps-install"; NI="-y"
              PKGS="wayfire gtk4 libadwaita gtk4-layer-shell python3-gobject python3-cairo python3-pywayland"
              OPT="NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr grim slurp wl-clipboard ffmpegthumbnailer wf-recorder wlsunset wtype swayidle openssl" ;;
    *alpine*) PM="sudo apk add"; NI=""
              PKGS="wayfire gtk4.0 libadwaita gtk4-layer-shell py3-gobject3 py3-cairo py3-pywayland"
              OPT="networkmanager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr grim slurp wl-clipboard ffmpegthumbnailer wf-recorder wlsunset wtype swayidle openssl" ;;
    *)        PM=""; PKGS=""; OPT="" ;;
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
    # pywayland isn't packaged everywhere: pip, for this user
    if ! python3 -c "import pywayland" 2>/dev/null; then
        say "pywayland from PyPI (this user only)"
        python3 -m pip install --user pywayland 2>/dev/null || \
            python3 -m pip install --user --break-system-packages pywayland || true
    fi
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
            for p in $OPT; do $PM $NI "$p" >/dev/null 2>&1 || echo "  (not available here: $p)"; done
        fi
        # services those features talk to
        for svc in NetworkManager bluetooth power-profiles-daemon; do
            if systemctl list-unit-files "$svc.service" >/dev/null 2>&1 && ! systemctl is-active -q "$svc"; then
                sudo systemctl enable --now "$svc" >/dev/null 2>&1 && echo "  started $svc"
            fi
        done
    else
        echo "  $PM $OPT"
    fi
fi
# pixdecor: macOS title bars for terminals / X11 apps (a Wayfire plugin built from source)
pixdecor=""
for d in $(pkg-config --variable=plugindir wayfire 2>/dev/null) /usr/lib/wayfire /usr/lib64/wayfire /usr/local/lib/wayfire; do
    [ -f "$d/libpixdecor.so" ] && pixdecor=1
done
if [ -z "$pixdecor" ]; then
    aur="$(command -v paru || command -v yay || true)"
    if [ "$DEPS" = 1 ] && [ -n "$aur" ] && [[ "$family" == *arch* ]]; then
        say "pixdecor (macOS title bars for terminals and X11 apps) from the AUR -- builds for a few minutes"
        "$aur" -S --needed --noconfirm wayfire-plugin-pixdecor-git || echo "  (pixdecor didn't build; title bars stay Wayfire's own)"
    else
        echo "Optional, macOS title bars for terminals/X11 apps: the pixdecor Wayfire plugin (Arch AUR: wayfire-plugin-pixdecor-git; elsewhere build from github.com/soreau/pixdecor)"
    fi
fi

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
$SUDO rm -rf "$SHARE"
$SUDO mkdir -p "$(dirname "$SHARE")"
if [ "$DEV" = 1 ]; then
    $SUDO ln -s "$SRC" "$SHARE"          # the session runs this clone's code
    echo "Dev install: $SHARE -> $SRC"
else
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
if [ "$DEPS" = 1 ] && [[ "$family" == *arch* ]] && ! command -v meson >/dev/null; then
    $PM $NI meson ninja >/dev/null 2>&1 || true
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
printf '[preferred]\ndefault=gtk\norg.freedesktop.impl.portal.Screenshot=wlr\norg.freedesktop.impl.portal.ScreenCast=wlr\n' \
    > "$tmp/sonata-portals.conf"
$SUDO install -m 644 "$tmp/sonata-portals.conf" "$PORTAL_DIR/sonata-portals.conf"

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
    if [ -n "$SUDO" ] || [ "$(id -u)" = 0 ] || ask "Add \"Sonata\" to the login screen (needs sudo)?"; then
        sudo install -D -m 755 "$tmp/sonata-login" "$LOGIN_BIN" &&
        sudo install -D -m 644 "$tmp/sonata.desktop" "$SESSION_FILE" && echo "Login screen: \"Sonata\" added."
    else
        echo "Skipped: the login screen won't list Sonata (run ./install.sh again to add it)."
    fi
fi
# GDM lists Wayland sessions only when it runs on Wayland itself.
if grep -Eqs '^[[:space:]]*WaylandEnable[[:space:]]*=[[:space:]]*false' /etc/gdm/custom.conf /etc/gdm3/custom.conf /etc/gdm3/daemon.conf; then
    say "GDM has Wayland turned off (WaylandEnable=false in its custom.conf): Sonata won't be listed."
    echo "  Remove that line (or set it to true) and restart the computer."
fi

case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not in your PATH (the session adds it itself)." ;; esac
say "Checking this computer (sonata2 doctor):"
PYTHONPATH="$SHARE" python3 -m sonata2 doctor || true
say "Done. Log out and pick \"Sonata\" on the login screen, or try it inside your desktop with:"
echo "  $SRC/tools/dev-session.sh"
if [ "$GREETER" = 1 ]; then
    say "Sonata's login screen (greetd)"
    "$SRC/tools/greeter-setup.sh" install
elif [ -x /usr/local/bin/sonata-greeter ]; then
    "$SRC/tools/greeter-setup.sh" install >/dev/null && echo "Login screen: updated to this version."
fi
