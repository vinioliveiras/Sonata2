#!/usr/bin/env bash
# Sonata's login screen (`sonata2 greeter`) under greetd, in place of
# GDM/SDDM/LightDM. Run by `install.sh --greeter`; `install.sh --gdm` (this
# script with "revert") puts the previous login manager back.
#
#   tools/greeter-setup.sh install   # needs sudo; takes effect on the next boot
#   tools/greeter-setup.sh revert
#
# What it sets up:
#   /usr/local/share/sonata2-greeter/   a copy of Sonata the greeter user can
#                                       read (your home folder is private):
#                                       bin/sonata2-rs, share/sonata2-rs/{data,locale}
#   /usr/local/bin/sonata-greeter       starts a small Wayfire running it
#   /etc/greetd/config.toml             greetd runs that as user "greeter"
#                                       (the old file is kept as .sonata-backup)
#   /var/lib/sonata-greeter/<you>/      your wallpaper, copied there by your
#                                       session so the login screen shows it
#   /var/cache/sonata-greeter/          last user and session (greeter's)
#   Switch User (lock screen): a second greetd on
#   the next free VT, started on demand --
#     /etc/greetd/sonata-switch.toml
#     /etc/systemd/system/sonata-greeter-switch.service
#     /etc/polkit-1/rules.d/60-sonata2-switch-user.rules  (local users and
#                                       the greeter may start / stop it)
set -euo pipefail
trap 's=$?; echo "greeter-setup.sh stopped at line $LINENO (exit $s): $BASH_COMMAND" >&2' ERR
SRC="$(cd "$(dirname "$0")/.." && pwd)"
# The Sonata binary to copy: install.sh names the one it installed; else
# the installed one, else this clone's build.
RS="${SONATA2_BIN:-}"
for c in "$(command -v sonata2-rs 2>/dev/null || true)" "$HOME/.local/bin/sonata2-rs" "$SRC/rust/target/release/sonata2-rs"; do
    [ -n "$RS" ] && break
    [ -n "$c" ] && [ -x "$c" ] && RS="$c"
done
ACTION="${1:-install}"
SHARE=/usr/local/share/sonata2-greeter
LAUNCHER=/usr/local/bin/sonata-greeter
GREETD=/etc/greetd/config.toml
MARK=/var/lib/sonata-greeter/previous-dm
SWITCH_TOML=/etc/greetd/sonata-switch.toml
SWITCH_UNIT=/etc/systemd/system/sonata-greeter-switch.service
SWITCH_RULE=/etc/polkit-1/rules.d/60-sonata2-switch-user.rules
DMS="gdm sddm plasmalogin lightdm lxdm ly lemurs cosmic-greeter"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
SUDO=sudo; [ "$(id -u)" = 0 ] && SUDO=""
ME="${SUDO_USER:-$(id -un)}"

# whichever login manager the system starts: the one display-manager.service
# points to (any of them -- a friend's CachyOS had Plasma Login, plasmalogin,
# and enabling greetd failed: "display-manager.service already exists"),
# else the known ones by name
DM_LINK="${DM_LINK:-/etc/systemd/system/display-manager.service}"
current_dm() {
    local target
    target="$(readlink -f "$DM_LINK" 2>/dev/null || true)"
    if [ -n "$target" ] && [ -e "$target" ]; then
        target="$(basename "$target" .service)"
        [ -n "$target" ] && [ "$target" != display-manager ] && { echo "$target"; return; }
    fi
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
    $SUDO rm -f "$SWITCH_TOML" "$SWITCH_UNIT" "$SWITCH_RULE"
    $SUDO systemctl daemon-reload || true
    # undo the rest of "install": the keyring lines added to greetd's PAM file, the quiet console
    if [ -f /etc/pam.d/greetd.sonata-bak ]; then
        rs="$RS"; [ -x "$SHARE/bin/sonata2-rs" ] && rs="$SHARE/bin/sonata2-rs"
        if [ -n "$rs" ]; then $SUDO "$rs" keyring unpam /etc/pam.d/greetd || true; fi
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
if [ -z "$RS" ]; then
    echo "Sonata isn't built yet (sonata2-rs): run ./install.sh first." >&2
    exit 1
fi
say "Copying Sonata for the login screen -> $SHARE"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp:?}"' EXIT
# laid out as an install: the binary finds its data and translations at
# ../share/sonata2-rs next to itself
mkdir -p "$tmp/share/bin" "$tmp/share/share/sonata2-rs"
cp "$RS" "$tmp/share/bin/sonata2-rs"
cp -a "$SRC/data" "$SRC/locale" "$tmp/share/share/sonata2-rs/"
# (no systemd-localed to ask: "us" -- under pipefail a failing localectl
#  stopped the whole install without a word)
loc="$(localectl status 2>/dev/null || true)"
layout="$(printf '%s\n' "$loc" | sed -n 's/.*X11 Layout: *//p')"
variant="$(printf '%s\n' "$loc" | sed -n 's/.*X11 Variant: *//p')"
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
greeter = $SHARE/bin/sonata2-rs greeter
EOF
# Displays: the same mode/scale/position as your Sonata session (Settings >
# Displays writes them to wayfire-overrides.ini), else the highest refresh
# rate. The login screen's own resolution menu can still change them.
USER_HOME="$(getent passwd "$ME" | cut -d: -f6)"
over="$USER_HOME/.config/sonata2/wayfire-overrides.ini"
for card in /sys/class/drm/card*-*; do
    [ -e "$card" ] || continue
    n="${card##*/}"; n="${n#*-}"
    printf '%s\n' "$n"
done | sort -u | while read -r name; do
    # that output's keys in the overrides ([output:<name>]: mode, scale, position, transform)
    keys="$(awk -v sec="[output:$name]" '
        /^[[:space:]]*\[/ { on = ($0 == sec) ; next }
        on && /^[[:space:]]*(mode|scale|position|transform)[[:space:]]*=/ {
            k = $0; sub(/[[:space:]]*=.*/, "", k); gsub(/^[[:space:]]+/, "", k)
            v = $0; sub(/^[^=]*=[[:space:]]*/, "", v); sub(/[[:space:]]+$/, "", v)
            print k " = " v
        }' "$over" 2>/dev/null || true)"
    printf '\n[output:%s]\n' "$name"
    printf '%s\n' "$keys" | grep -q '^mode = ' || printf 'mode = highrr\n'
    if [ -n "$keys" ]; then printf '%s\n' "$keys"; fi
done >> "$tmp/share/wayfire.ini"
cat > "$tmp/sonata-greeter" <<EOF
#!/bin/sh
# greetd runs this (as user "greeter"): Sonata's login screen in a small
# Wayfire; the screen ends that Wayfire once you log in.
export SONATA_GREETER_WAYFIRE=1 XDG_CURRENT_DESKTOP=Sonata
export XCURSOR_PATH=$SHARE/share/sonata2-rs/data/icons XCURSOR_THEME=Sonata-Cursors XCURSOR_SIZE=24
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

# -- Switch User: a second greetd on the next free VT, started from the lock screen ----------
# (greetd's own session stays on VT 1; this one's greeter goes away again
#  once you go back to a session that's already running)
printf '# Sonata: Switch User (tools/greeter-setup.sh)\n[terminal]\nvt = "next"\nswitch = true\n\n[default_session]\ncommand = "env SONATA_GREETER_SWITCH=1 %s"\nuser = "greeter"\n' \
    "$LAUNCHER" | $SUDO tee "$SWITCH_TOML" >/dev/null
GREETD_BIN="$(command -v greetd || echo /usr/bin/greetd)"
printf '[Unit]\nDescription=Sonata login screen for Switch User\nAfter=systemd-user-sessions.service\n\n[Service]\nExecStart=%s --config %s\nRestart=no\n' \
    "$GREETD_BIN" "$SWITCH_TOML" | $SUDO tee "$SWITCH_UNIT" >/dev/null
$SUDO systemctl daemon-reload || true
if [ -d /etc/polkit-1/rules.d ] || $SUDO mkdir -p /etc/polkit-1/rules.d 2>/dev/null; then
    $SUDO install -m 644 "$SRC/config/polkit/60-sonata2-switch-user.rules" "$SWITCH_RULE"
fi

# -- the login unlocks GNOME's keyring (saved passwords: no prompt after logging in) ----------
# pam_gnome_keyring in /etc/pam.d/greetd (a copy kept as .sonata-bak; `sonata2 keyring pam`)
if command -v gnome-keyring-daemon >/dev/null && [ -f /etc/pam.d/greetd ]; then
    $SUDO "$SHARE/bin/sonata2-rs" keyring pam /etc/pam.d/greetd || true
fi

# -- switch the login manager (next boot: switching now would end this session) ---------------
prev="$(current_dm || true)"
if [ -n "$prev" ] && [ "$prev" != greetd ]; then
    echo "$prev" | $SUDO tee "$MARK" >/dev/null
    $SUDO systemctl disable "$prev.service" || true
fi
# --force: display-manager.service is greetd's now, whatever still held it
$SUDO systemctl enable --force greetd.service
say "Sonata's login screen is set up. Restart the computer to see it."
say "To go back: ./install.sh --gdm"
