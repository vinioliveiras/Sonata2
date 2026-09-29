#!/usr/bin/env bash
# wayfire-config.sh <in.ini> <out.ini>: the session's Wayfire config with
# @SONATA@ replaced by this Sonata folder, the pixdecor plugin swapped in for
# "decoration" when it is installed (else Wayfire's own title bars), and the
# options changed in Sonata's Settings (~/.config/sonata2/wayfire-overrides.ini)
# applied on top.
set -euo pipefail
in="$1" out="$2"
here="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
pix=""
for d in $(pkg-config --variable=plugindir wayfire 2>/dev/null) /usr/lib/wayfire /usr/lib64/wayfire \
         /usr/local/lib/wayfire /usr/lib/x86_64-linux-gnu/wayfire; do
    [ -f "$d/libpixdecor.so" ] && { pix=1; break; }
done
sed -e "s|@SONATA@|$here|g" "$in" > "$out"
if [ -n "$pix" ]; then
    sed -i -E '/^plugins *=/ s/(^| )decoration( |$)/\1pixdecor\2/' "$out"
fi
over="${XDG_CONFIG_HOME:-$HOME/.config}/sonata2/wayfire-overrides.ini"
if [ -f "$over" ]; then
    PYTHONPATH="$here" python3 - "$out" "$over" <<'PY'
import sys
from sonata2 import wfconfig as system
out, over = sys.argv[1], sys.argv[2]
system._wayfire_files = lambda: [out]          # write only the resolved copy
sec = None
for line in open(over, encoding="utf-8"):
    s = line.strip()
    if s.startswith("[") and s.endswith("]"):
        sec = s[1:-1]
    elif sec and "=" in s and not s.startswith("#"):
        k, v = s.split("=", 1)
        system.wayfire_set(sec, k.strip(), v.strip())
PY
fi
