#!/usr/bin/env bash
# A clean screen around boot, login and shutdown: no kernel messages and no
# "[ OK ]" status lines on the console (install.sh runs this; --uninstall
# and "install.sh --gdm" undo it).
#
#   tools/quiet-console.sh install | revert
set -euo pipefail
SUDO=sudo; [ "$(id -u)" = 0 ] && SUDO=""
SYSCTL=/etc/sysctl.d/20-sonata-quiet-console.conf
SYSTEMD=/etc/systemd/system.conf.d/20-sonata-quiet.conf
if [ "${1:-install}" = revert ]; then
    $SUDO rm -f "$SYSCTL" "$SYSTEMD"
    exit 0
fi
printf '# Sonata (tools/quiet-console.sh): kernel messages off the console\nkernel.printk = 3 3 3 3\n' |
    $SUDO tee "$SYSCTL" >/dev/null
$SUDO sysctl -q -p "$SYSCTL" 2>/dev/null || true
$SUDO install -d "$(dirname "$SYSTEMD")"
printf '# Sonata (tools/quiet-console.sh): no [ OK ] lines on screen at boot/shutdown\n[Manager]\nShowStatus=no\n' |
    $SUDO tee "$SYSTEMD" >/dev/null
echo "Console: quiet (no boot/shutdown text on screen)."
