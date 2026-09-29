#!/usr/bin/env bash
# wayfire-config.sh <in.ini> <out.ini>: the session's Wayfire config with
# @SONATA@ replaced by this Sonata folder, and the pixdecor plugin swapped in
# for "decoration" when it is installed (else Wayfire's own title bars).
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
