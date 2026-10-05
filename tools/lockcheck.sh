#!/bin/bash
# Why the session didn't lock on its own: the setting, swayidle, and what
# keeps the session awake (full-screen windows, apps holding it).
out="${1:-$HOME/GitHub/.sonata-work/lockcheck.txt}"
cd "$(dirname "$0")/.." || exit 1
{
echo "== security.json"; cat "${XDG_CONFIG_HOME:-$HOME/.config}/sonata2/security.json" 2>&1; echo
echo "== swayidle"; command -v swayidle; swayidle -v 2>&1 | head -1; pgrep -a swayidle || echo "(not running)"
echo "== brightnessctl / leds"; command -v brightnessctl; ls /sys/class/leds 2>&1 | grep -i kbd
echo "== wayfire [idle] (this session)"
sed -n '/^\[idle\]/,/^\[/p' "${XDG_RUNTIME_DIR:-/tmp}/sonata2-wayfire.ini" 2>&1 | head -8
echo "== windows (full screen?)"
python3 - <<'PY'
from sonata2.wl.wfipc import WayfireIPC
try:
    for v in WayfireIPC().call("window-rules/list-views") or []:
        if isinstance(v, dict) and v.get("type") == "toplevel":
            print(f"{v.get('app-id')!r:40} fullscreen={v.get('fullscreen')} minimized={v.get('minimized')} {v.get('title','')[:50]!r}")
except Exception as e:
    print("ipc:", e)
PY
echo "== Sonata's own idle watch (input idle, ignoring apps)"
python3 -c "
from sonata2.wl.idlewatch import IdleWatch
w = IdleWatch()
print('connected', w.ok, '| input idle (v2):', w.input_idle, '| display power:', w.can_power())
print('-> Sonata handles idle itself' if w.ok and w.input_idle else '-> falls back to swayidle (apps can keep it awake)')
"
echo "== logind inhibitors"; systemd-inhibit --list --no-pager 2>&1 | head -20
echo "== lock log"; journalctl --user --since "-12h" --no-pager 2>/dev/null | grep -i -E "swayidle|sonata2-lock|lock-wait" | tail -20
} > "$out" 2>&1
echo "Done: $out"
