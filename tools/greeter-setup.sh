#!/usr/bin/env bash
# Sonata's login screen (sonata2/shell/greeter.py) under greetd, in place of
# GDM/SDDM/LightDM. Run by `install.sh --greeter`; `install.sh --gdm` (this
# script with "revert") puts the previous login manager back.
#
#   tools/greeter-setup.sh install   # needs sudo; takes effect on the next boot
#   tools/greeter-setup.sh revert
#
# What it sets up:
#   /usr/local/share/sonata2-greeter/   a copy of Sonata the greeter user can
#                                       read (your home folder is private)
#   /usr/local/bin/sonata-greeter       starts a small Wayfire running it
#   /etc/greetd/config.toml             greetd runs that as user "greeter"
#                                       (the old file is kept as .sonata-backup)
#   /var/lib/sonata-greeter/<you>/      your wallpaper, copied there by your
#                                       session so the login screen shows it
#   /var/cache/sonata-greeter/          last user and session (greeter's)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
ACTION="${1:-install}"
SHARE=/usr/local/share/sonata2-greeter
LAUNCHER=/usr/local/bin/sonata-greeter
GREETD=/etc/greetd/config.toml
MARK=/var/lib/sonata-greeter/previous-dm
DMS="gdm sddm lightdm lxdm ly"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
SUDO=sudo; [ "$(id -u)" = 0 ] && SUDO=""
ME="${SUDO_USER:-$(id -un)}"

current_dm() {
    for dm in $DMS; do
        systemctl is-enabled "$dm.service" >/dev/null 2>&1 && { echo "$dm"; return; }
    done
}

if [ "$ACTION" = revert ]; then
    prev="$($SUDO cat "$MARK" 2>/dev/null || echo gdm)"
    say "Login screen: back to $prev (on the next boot)"
    $SUDO systemctl disable greetd.service || true
    $SUDO systemctl enable "$prev.service"
    [ -f "$GREETD.sonata-backup" ] && $SUDO mv -f "$GREETD.sonata-backup" "$GREETD"
    $SUDO rm -f "$LAUNCHER"            # install.sh keeps it up to date only while it's in use
    # undo the rest of "install": the keyring lines added to greetd's PAM file, the quiet console
    if [ -f /etc/pam.d/greetd.sonata-bak ]; then
        $SUDO env PYTHONPATH="$SRC" python3 -m sonata2 keyring unpam /etc/pam.d/greetd || true
    fi
    "$SRC/tools/quiet-console.sh" revert || true
    say "Done. Restart the computer to see it."
    exit 0
fi

# -- greetd itself ---------------------------------------------------------------------------
if ! command -v greetd >/dev/null && [ ! -x /usr/bin/greetd ]; then
    . /etc/os-release 2>/dev/null || true
    case "${ID:-} ${ID_LIKE:-}" in
        *arch*) $SUDO pacman -S --needed --noconfirm greetd ;;
        *debian*|*ubuntu*) $SUDO apt install -y greetd ;;
        *fedora*|*rhel*) $SUDO dnf install -y greetd ;;
        *suse*) $SUDO zypper install -y greetd ;;
        *void*) $SUDO xbps-install -y greetd ;;
        *alpine*) $SUDO apk add greetd ;;
        *) echo "Install greetd with your package manager, then run this again."; exit 1 ;;
    esac
fi
getent passwd greeter >/dev/null || $SUDO useradd -r -M -G video -s /usr/bin/nologin greeter

# -- a copy of Sonata the greeter can read ---------------------------------------------------------
say "Copying Sonata for the login screen -> $SHARE"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/share"
tar -C "$SRC" --exclude='__pycache__' --exclude='*.pyc' -cf - sonata2 | tar -C "$tmp/share" -xf -
layout="$(localectl status 2>/dev/null | sed -n 's/.*X11 Layout: *//p')"
variant="$(localectl status 2>/dev/null | sed -n 's/.*X11 Variant: *//p')"
cat > "$tmp/share/wayfire.ini" <<EOF
# Wayfire for the login screen only (tools/greeter-setup.sh)
[core]
plugins = autostart
xwayland = false

[input]
xkb_layout = ${layout:-us}
xkb_variant = ${variant}
cursor_theme = Sonata-Cursors
cursor_size = 24

[autostart]
autostart_wf_shell = false
greeter = env PYTHONPATH=$SHARE python3 -m sonata2 greeter
EOF
# Displays: the same mode/scale/position as your Sonata session (Settings >
# Displays writes them to wayfire-overrides.ini), else the highest refresh
# rate. The login screen's own resolution menu can still change them.
USER_HOME="$(getent passwd "$ME" | cut -d: -f6)"
python3 - "$USER_HOME/.config/sonata2/wayfire-overrides.ini" >> "$tmp/share/wayfire.ini" <<'PY'
import configparser, glob, os, sys
cp = configparser.ConfigParser(interpolation=None, strict=False)
try:
    cp.read(sys.argv[1])
except configparser.Error:
    pass
names = sorted({os.path.basename(p).split("-", 1)[1] for p in glob.glob("/sys/class/drm/card*-*")
                if "-" in os.path.basename(p)})
for name in names:
    sec = f"output:{name}"
    keys = {k: cp.get(sec, k) for k in ("mode", "scale", "position", "transform") if cp.has_option(sec, k)}
    keys.setdefault("mode", "highrr")
    print(f"\n[{sec}]")
    for k, v in keys.items():
        print(f"{k} = {v}")
PY
cat > "$tmp/sonata-greeter" <<EOF
#!/bin/sh
# greetd runs this (as user "greeter"): Sonata's login screen in a small
# Wayfire; the screen ends that Wayfire once you log in (greeter.py).
export SONATA_GREETER_WAYFIRE=1 XDG_CURRENT_DESKTOP=Sonata
export XCURSOR_PATH=$SHARE/sonata2/data/icons XCURSOR_THEME=Sonata-Cursors XCURSOR_SIZE=24
export XDG_CONFIG_HOME=/var/cache/sonata-greeter/config XDG_CACHE_HOME=/var/cache/sonata-greeter/cache
# a clean screen: no text on this console around the login (the VT shows
# whatever was printed on it when no compositor holds the display)
printf '\033c\033[?25l' 2>/dev/null
exec wayfire -c $SHARE/wayfire.ini > /var/cache/sonata-greeter/wayfire.log 2>&1
EOF
$SUDO rm -rf "$SHARE.new"
$SUDO mkdir -p "$SHARE.new"
$SUDO cp -a "$tmp/share/." "$SHARE.new/"
$SUDO rm -rf "$SHARE.old"
[ -d "$SHARE" ] && $SUDO mv "$SHARE" "$SHARE.old"
$SUDO mv "$SHARE.new" "$SHARE"
$SUDO rm -rf "$SHARE.old"
$SUDO chmod -R a+rX "$SHARE"
$SUDO install -m 755 "$tmp/sonata-greeter" "$LAUNCHER"

# -- folders ------------------------------------------------------------------------------------------
$SUDO install -d -m 755 /var/lib/sonata-greeter
$SUDO install -d -m 755 -o "$ME" "/var/lib/sonata-greeter/$ME"          # your session copies the wallpaper here
$SUDO install -d -m 755 -o greeter /var/cache/sonata-greeter
$SUDO install -d -m 700 -o greeter /var/cache/sonata-greeter/config /var/cache/sonata-greeter/cache

# -- a quiet console: no kernel or boot/shutdown status text between screens -----------------
"$SRC/tools/quiet-console.sh" install >/dev/null

# -- greetd config ---------------------------------------------------------------------------------
if [ -f "$GREETD" ] && ! grep -q sonata-greeter "$GREETD" && [ ! -f "$GREETD.sonata-backup" ]; then
    $SUDO cp -a "$GREETD" "$GREETD.sonata-backup"
fi
$SUDO install -d /etc/greetd
printf '# Sonata login screen (tools/greeter-setup.sh; "install.sh --gdm" undoes it)\n[terminal]\nvt = 1\n\n[default_session]\ncommand = "%s"\nuser = "greeter"\n' \
    "$LAUNCHER" | $SUDO tee "$GREETD" >/dev/null

# -- the login unlocks GNOME's keyring (saved passwords: no prompt after logging in) ----------
# pam_gnome_keyring in /etc/pam.d/greetd (a copy kept as .sonata-bak); sonata2/keyring.py
if command -v gnome-keyring-daemon >/dev/null && [ -f /etc/pam.d/greetd ]; then
    $SUDO env PYTHONPATH="$SRC" python3 -m sonata2 keyring pam /etc/pam.d/greetd || true
fi

# -- switch the login manager (next boot: switching now would end this session) ---------------
prev="$(current_dm || true)"
if [ -n "$prev" ] && [ "$prev" != greetd ]; then
    echo "$prev" | $SUDO tee "$MARK" >/dev/null
    $SUDO systemctl disable "$prev.service"
fi
$SUDO systemctl enable greetd.service
say "Sonata's login screen is set up. Restart the computer to see it."
say "To go back: ./install.sh --gdm"
