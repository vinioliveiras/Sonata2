#!/usr/bin/env bash
# Environment of a Sonata session (sourced by tools/sonata-session and
# tools/dev-session.sh). Only processes started inside the session see it,
# so other desktops keep their own look.
#   - bundled icon, cursor and GTK themes found without installing anything
#   - GTK3/GTK4/libadwaita apps use the Big Sur window theme (Sonata-Light/Dark)
#     with macOS button layout (close, minimize, zoom on the left)
#   - Sonata-only dconf layer, so these choices don't leak into GNOME/KDE
SONATA_REPO="${SONATA_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export SONATA_REPO
export XDG_DATA_DIRS="$SONATA_REPO/sonata2/data:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
export XCURSOR_PATH="$SONATA_REPO/sonata2/data/icons:$HOME/.local/share/icons:$HOME/.icons:/usr/share/icons:/usr/share/pixmaps"
export XCURSOR_THEME=Sonata-Cursors XCURSOR_SIZE=24
mkdir -p "$HOME/.config/sonata2"
# Bundled fonts (Inter, the free stand-in for SF Pro), without installing
# them system-wide: a fontconfig file that adds our folder to the system's.
cat > "$HOME/.config/sonata2/fonts.conf" 2>/dev/null <<FC || true
<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>$SONATA_REPO/sonata2/data/fonts</dir></fontconfig>
FC
export FONTCONFIG_FILE="$HOME/.config/sonata2/fonts.conf"
# Sonata's own settings layer (dconf): values set in the Sonata session go to
# ~/.config/dconf/sonata and are read first; everything else still comes
# from the normal user database, which other desktops keep using unchanged.
printf 'user-db:sonata\nuser-db:user\n' > "$HOME/.config/sonata2/dconf-profile"
export DCONF_PROFILE="$HOME/.config/sonata2/dconf-profile"
_gs() { gsettings set "$@" 2>/dev/null || true; }
# First run (per defaults version): write Sonata's whole look into its own
# layer, so nothing falls through from GNOME/KDE (fonts, scaling, dark mode,
# accent...). Later changes made in Sonata are kept.
SONATA_DEFAULTS=1
mark="$HOME/.config/sonata2/.defaults-v$SONATA_DEFAULTS"
if [ ! -f "$mark" ] && command -v gsettings >/dev/null; then
    I=org.gnome.desktop.interface
    _gs org.gnome.desktop.wm.preferences button-layout 'close,minimize,maximize:'
    _gs org.gnome.desktop.wm.preferences action-double-click-titlebar 'toggle-maximize'
    _gs $I icon-theme 'Sonata'
    _gs $I cursor-theme 'Sonata-Cursors'
    _gs $I cursor-size 24
    _gs $I color-scheme 'default'            # macOS starts in Light
    _gs $I accent-color 'blue'
    _gs $I text-scaling-factor 1.0
    _gs $I enable-animations true
    _gs $I font-antialiasing 'grayscale'
    _gs $I font-hinting 'slight'
    _gs $I gtk-enable-primary-paste false
    _gs $I overlay-scrolling true
    # macOS text size (13 px); Inter stands in for SF Pro when it isn't installed.
    ui=""; mono="Monospace 10"
    if fc-list 2>/dev/null | grep -qi "SF Pro"; then ui="SF Pro Text"
    elif fc-list 2>/dev/null | grep -qi "Inter Variable"; then ui="Inter Variable"
    elif fc-list 2>/dev/null | grep -qi "Inter"; then ui="Inter"; fi
    fc-list 2>/dev/null | grep -qi "SF Mono" && mono="SF Mono 10"
    if [ -n "$ui" ]; then
        _gs $I font-name "$ui 10"; _gs $I document-font-name "$ui 10"
        _gs org.gnome.desktop.wm.preferences titlebar-font "$ui Bold 10"
    fi
    _gs $I monospace-font-name "$mono"
    touch "$mark"
fi
scheme="$(gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null)"
if [ "$scheme" = "'prefer-dark'" ]; then export GTK_THEME=Sonata-Dark; else export GTK_THEME=Sonata-Light; fi
_gs org.gnome.desktop.interface gtk-theme "$GTK_THEME"
# Qt apps: always the GTK look here, never a KDE/qt5ct theme set elsewhere.
export QT_QPA_PLATFORMTHEME=gtk3
unset QT_STYLE_OVERRIDE KDE_FULL_SESSION KDE_SESSION_VERSION
# Chromium/Electron (Chrome, Spotify, VS Code...): native Wayland with
# client-side title bars drawn from the GTK theme.
export ELECTRON_OZONE_PLATFORM_HINT="${ELECTRON_OZONE_PLATFORM_HINT:-auto}"
export PYTHONPATH="$SONATA_REPO${PYTHONPATH:+:$PYTHONPATH}"
