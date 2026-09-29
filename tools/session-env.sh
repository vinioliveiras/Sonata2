#!/usr/bin/env bash
# Environment of a Sonata session (sourced by tools/sonata-session and
# tools/dev-session.sh). Only processes started inside the session see it,
# so other desktops keep their own look.
#   - bundled icon, cursor and GTK themes found without installing anything
#   - GTK3/GTK4/libadwaita apps use the Big Sur window theme (Sonata-Light/Dark)
#     with macOS button layout (close, minimize, zoom on the left)
#   - Sonata's own desktop settings (prefs.py), served to apps by its portal
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
# Sonata's own desktop settings (sonata2/prefs.py: ~/.config/sonata2/system.json;
# apps read them through Sonata's settings portal -- no GNOME/dconf needed).
# Every login: Sonata's defaults on the first run, the UI font, GTK_THEME.
# (copies kept for apps that read GSettings directly go to a Sonata-only
# dconf layer, so they never leak into another desktop's settings)
printf 'user-db:sonata\nuser-db:user\n' > "$HOME/.config/sonata2/dconf-profile"
export DCONF_PROFILE="$HOME/.config/sonata2/dconf-profile"
eval "$(PYTHONPATH="$SONATA_REPO${PYTHONPATH:+:$PYTHONPATH}" python3 -m sonata2.prefs session-env 2>/dev/null)"
[ -n "$GTK_THEME" ] || export GTK_THEME=Sonata-Light
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
# Plain text opens in Sonata's TextEdit (keys the user set keep theirs).
for t in text/plain text/markdown text/x-log; do
    grep -q "^$t=" "$ml" || \
        sed -i "/^\[Default Applications\]/a $t=io.github.vinioliveiras.sonata2.textedit.desktop" "$ml"
done
# Pictures open in Sonata's Preview.
for t in image/png image/jpeg image/gif image/webp image/bmp image/tiff image/svg+xml image/avif image/heic; do
    grep -q "^$t=" "$ml" || \
        sed -i "/^\[Default Applications\]/a $t=io.github.vinioliveiras.sonata2.preview.desktop" "$ml"
done
# Wayfire blurs Sonata windows here: sidebars use the glass material.
export SONATA_GLASS=1
export PYTHONPATH="$SONATA_REPO${PYTHONPATH:+:$PYTHONPATH}"
