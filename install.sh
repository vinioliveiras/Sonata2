#!/usr/bin/env bash
# Sonata 2 installer -- works on any distro (no distro-specific paths).
#
#   ./install.sh              install for this user (~/.local), ask for sudo
#                             only to add "Sonata" to the login screen
#   ./install.sh --system     install for all users (/usr/local, needs sudo)
#   ./install.sh --deps       also install missing dependencies with the
#                             distro's package manager (pacman/apt/dnf/zypper)
#   ./install.sh --yes        don't ask (login-screen entry included)
#   ./install.sh --uninstall  remove it again (your settings stay)
#   ./install.sh --dev        link to this clone instead of copying it: the
#                             session runs the code you are editing
#                             (`sonata2 restart` reloads the shell)
#
# Installs: the sonata2 package + themes/icons -> <prefix>/share/sonata2,
# launchers `sonata2` and `sonata-session` -> <prefix>/bin, the session
# entry -> /usr/share/wayland-sessions/sonata.desktop, the session's Wayfire
# config -> ~/.config/sonata2/wayfire.ini (an edited copy is kept), portal
# preferences for the "Sonata" desktop.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
MODE=user DEPS=0 YES=0 UNINSTALL=0 DEV=0
for a in "$@"; do
    case "$a" in
        --system) MODE=system ;; --deps) DEPS=1 ;; --yes|-y) YES=1 ;; --uninstall) UNINSTALL=1 ;; --dev) DEV=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
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
        sudo rm -f "$SESSION_FILE"
    fi
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
case "$family" in
    *arch*)   PM="sudo pacman -S --needed"
              PKGS="wayfire gtk4 libadwaita gtk4-layer-shell python-gobject python-cairo python-pywayland"
              OPT="networkmanager wireplumber brightnessctl bluez-utils wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr polkit-gnome" ;;
    *debian*|*ubuntu*) PM="sudo apt install"
              PKGS="wayfire gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk4layershell-1.0 python3-gi python3-gi-cairo python3-pywayland"
              OPT="network-manager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr policykit-1-gnome" ;;
    *fedora*|*rhel*) PM="sudo dnf install"
              PKGS="wayfire gtk4 libadwaita gtk4-layer-shell python3-gobject python3-cairo python3-pywayland"
              OPT="NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr polkit-gnome" ;;
    *suse*)   PM="sudo zypper install"
              PKGS="wayfire gtk4 libadwaita-1-0 typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 gtk4-layer-shell python3-gobject python3-gobject-cairo python3-pywayland"
              OPT="NetworkManager wireplumber brightnessctl bluez wlr-randr power-profiles-daemon xdg-desktop-portal-gtk xdg-desktop-portal-wlr" ;;
    *)        PM=""; PKGS=""; OPT="" ;;
esac

if [ -n "$missing" ]; then
    say "Missing: $missing"
    if [ -n "$PM" ]; then
        echo "Install with:  $PM $PKGS"
        if [ "$DEPS" = 1 ] || ask "Install them now?"; then $PM $PKGS; fi
    else
        echo "Install Wayfire, GTK 4, libadwaita, gtk4-layer-shell (+ GObject introspection),"
        echo "PyGObject with cairo support and pywayland with your package manager."
    fi
fi
[ -n "$OPT" ] && echo "Optional (Wi-Fi, sound, brightness, Bluetooth, displays, energy, portals): $PM $OPT"
echo "Optional, macOS title bars for terminals/X11 apps: the pixdecor Wayfire plugin (Arch AUR: wayfire-plugin-pixdecor-git)"

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
cat > "$tmp/sonata.desktop" <<EOF
[Desktop Entry]
Name=Sonata
Comment=Sonata 2 desktop (Wayfire + Sonata shell)
Exec=$BIN/sonata-session
TryExec=$BIN/sonata-session
Type=Application
DesktopNames=Sonata
EOF
if [ -d /usr/share/wayland-sessions ] || [ "$MODE" = system ]; then
    if [ -n "$SUDO" ] || [ "$(id -u)" = 0 ] || ask "Add \"Sonata\" to the login screen (needs sudo)?"; then
        sudo install -D -m 644 "$tmp/sonata.desktop" "$SESSION_FILE" && echo "Login screen: \"Sonata\" added."
    else
        echo "Skipped. Later: sudo install -D -m 644 <(cat <<'X'"; cat "$tmp/sonata.desktop"; echo "X"; echo ") $SESSION_FILE"
    fi
fi

case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not in your PATH (the session adds it itself)." ;; esac
say "Done. Log out and pick \"Sonata\" on the login screen, or try it inside your desktop with:"
echo "  $SRC/tools/dev-session.sh"
