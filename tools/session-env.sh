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
export XCURSOR_THEME=Sonata-MacTahoe XCURSOR_SIZE=24
# Sonata's own settings layer (dconf): values set in the Sonata session go to
# ~/.config/dconf/sonata and are read first; everything else still comes
# from the normal user database, which other desktops keep using unchanged.
mkdir -p "$HOME/.config/sonata2"
printf 'user-db:sonata\nuser-db:user\n' > "$HOME/.config/sonata2/dconf-profile"
export DCONF_PROFILE="$HOME/.config/sonata2/dconf-profile"
_gs() { gsettings set "$@" 2>/dev/null || true; }
_gs org.gnome.desktop.wm.preferences button-layout 'close,minimize,maximize:'
_gs org.gnome.desktop.interface icon-theme 'Sonata'
_gs org.gnome.desktop.interface cursor-theme 'Sonata-MacTahoe'
_gs org.gnome.desktop.interface cursor-size 24
# macOS text size (13 px); Inter stands in for SF Pro when it isn't installed.
if fc-list 2>/dev/null | grep -qi "SF Pro"; then _gs org.gnome.desktop.interface font-name 'SF Pro Text 10'
elif fc-list 2>/dev/null | grep -qi "Inter"; then _gs org.gnome.desktop.interface font-name 'Inter 10'; fi
scheme="$(gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null)"
if [ "$scheme" = "'prefer-dark'" ]; then export GTK_THEME=Sonata-Dark; else export GTK_THEME=Sonata-Light; fi
_gs org.gnome.desktop.interface gtk-theme "$GTK_THEME"
# Qt apps: follow the GTK look where the platform theme allows it.
export QT_QPA_PLATFORMTHEME="${QT_QPA_PLATFORMTHEME:-gtk3}"
# Chromium/Electron (Chrome, Spotify, VS Code...): native Wayland with
# client-side title bars drawn from the GTK theme.
export ELECTRON_OZONE_PLATFORM_HINT="${ELECTRON_OZONE_PLATFORM_HINT:-auto}"
export PYTHONPATH="$SONATA_REPO${PYTHONPATH:+:$PYTHONPATH}"
