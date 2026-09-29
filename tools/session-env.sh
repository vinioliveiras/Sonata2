#!/usr/bin/env bash
# Environment of a Sonata session (sourced by tools/sonata-session and
# tools/dev-session.sh). Only processes started inside the session see it,
# so other desktops keep their own look.
#   - bundled icon, cursor and GTK themes found without installing anything
#   - GTK3/GTK4/libadwaita apps use the Big Sur window theme (Sonata-Light/Dark)
#     with macOS button layout (close, minimize, zoom on the left)
#   - Sonata-only dconf layer, so these choices don't leak into GNOME/KDE
# Not inherited from a shell process of another session (e.g. a terminal
# opened from the Dock): the flag would disable layer-shell here.
unset SONATA2_PRELOADED SONATA2_ORIG_LD_PRELOAD
case "${LD_PRELOAD:-}" in *gtk4-layer-shell*) unset LD_PRELOAD ;; esac
SONATA_REPO="${SONATA_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export SONATA_REPO
export XDG_DATA_DIRS="$SONATA_REPO/sonata2/data:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
# Flatpak apps: a login screen session doesn't source /etc/profile.d, where
# flatpak adds its export folders -- without them its apps are invisible.
for d in "${XDG_DATA_HOME:-$HOME/.local/share}/flatpak/exports/share" /var/lib/flatpak/exports/share; do
    case ":$XDG_DATA_DIRS:" in *":$d:"*|*":$d/:"*) ;; *) [ -d "$d" ] && XDG_DATA_DIRS="$XDG_DATA_DIRS:$d" ;; esac
done
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
    touch "$mark"
fi
# UI font, every login: SF Pro when the user installed it (Apple's licence
# forbids shipping it), else the bundled Inter. Only replaces our own
# defaults, never a font the user picked. macOS text size: 13 px (10 pt).
if command -v gsettings >/dev/null && command -v fc-list >/dev/null; then
    I=org.gnome.desktop.interface
    fams="$(fc-list : family 2>/dev/null)"
    ui=""
    for f in "SF Pro Text" "SF Pro" "Inter Variable" "Inter"; do
        printf '%s\n' "$fams" | tr ',' '\n' | grep -qx "$f" && { ui="$f"; break; }
    done
    cur="$(gsettings get $I font-name 2>/dev/null | tr -d "'")"
    case "$cur" in
        "SF Pro Text 10"|"SF Pro 10"|"Inter Variable 10"|"Inter 10"|"Cantarell 11"|"Adwaita Sans 11"|"")
            if [ -n "$ui" ] && [ "$cur" != "$ui 10" ]; then
                _gs $I font-name "$ui 10"; _gs $I document-font-name "$ui 10"
                _gs org.gnome.desktop.wm.preferences titlebar-font "$ui Bold 10"
            fi ;;
    esac
    cur="$(gsettings get $I monospace-font-name 2>/dev/null | tr -d "'")"
    case "$cur" in
        "Monospace 10"|"Source Code Pro 10"|"Adwaita Mono 11"|"SF Mono 10"|"")
            if printf '%s\n' "$fams" | tr ',' '\n' | grep -qx "SF Mono"; then m="SF Mono 10"; else m="Monospace 10"; fi
            [ "$cur" != "$m" ] && _gs $I monospace-font-name "$m" ;;
    esac
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
# Open/Save dialogs through the file chooser portal -> Sonata's panels
# (sonata2/portal.py): GTK 3 apps (GTK_USE_PORTAL), GTK 4 apps (GDK_DEBUG
# portals); Chromium/Electron use the portal on their own, Firefox through
# its pref (sonata2/titlebars.py)
export GTK_USE_PORTAL=1
case ",${GDK_DEBUG:-}," in *,portals,*) ;; *) export GDK_DEBUG="${GDK_DEBUG:+$GDK_DEBUG,}portals" ;; esac
# Sonata title bars for all apps (Settings > Appearance, on unless turned
# off): Claude Desktop's Linux build (claude-desktop-bin) draws its own
# frame unless told to use the system's (sonata2/titlebars.py does the rest)
if ! grep -qs '"system_titlebars": *false' "$HOME/.config/sonata2/appearance.json"; then
    export CLAUDE_NATIVE_TITLEBAR="${CLAUDE_NATIVE_TITLEBAR:-1}"
fi
# Folders open in Sonata's Files -- only in this session: the
# desktop-specific list (XDG_CURRENT_DESKTOP=Sonata) other desktops ignore.
# Every folder-like type goes to Files (never Nautilus); keys the user
# already set keep their value.
ml="$HOME/.config/sonata-mimeapps.list"
grep -q '^\[Default Applications\]' "$ml" 2>/dev/null || printf '[Default Applications]\n' >> "$ml"
for t in inode/directory inode/mount-point x-directory/normal application/x-directory \
         x-scheme-handler/trash x-scheme-handler/recent x-scheme-handler/computer \
         x-scheme-handler/network x-scheme-handler/smb x-scheme-handler/sftp x-scheme-handler/afp; do
    grep -q "^$t=" "$ml" || \
        sed -i "/^\[Default Applications\]/a $t=io.github.vinioliveiras.sonata2.files.desktop" "$ml"
done
# Wayfire blurs Sonata windows here: sidebars use the glass material.
export SONATA_GLASS=1
export PYTHONPATH="$SONATA_REPO${PYTHONPATH:+:$PYTHONPATH}"
