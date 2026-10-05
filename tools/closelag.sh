#!/bin/bash
# Lag when closing apps: for 60 s, every Sonata process logs what blocks its
# main loop (sonata2/stallwatch.py) and the busiest processes are sampled
# twice a second. Open and close a few apps meanwhile, then send the report.
out="${1:-$HOME/GitHub/.sonata-work/closelag.txt}"
cache="${XDG_CACHE_HOME:-$HOME/.cache}/sonata2"
mkdir -p "$cache"
: > "$cache/stalls.log"
touch "$cache/stallwatch"
trap 'rm -f "$cache/stallwatch"' EXIT
echo "Recording 60 s: open and close apps (Chrome, Files, Steam...) a few times now."
{
    echo "== CPU (top, every 0.5 s) =="
    top -b -d 0.5 -n 120 -w 160 | awk '/^top -/{print "--", $3} /^ *[0-9]+ /{if ($9+0 > 5) print}'
} > "$out.cpu" 2>&1
rm -f "$cache/stallwatch"
{
    echo "== main-loop stalls =="
    cat "$cache/stalls.log"
    echo
    cat "$out.cpu"
    echo; echo "== quit.log (tail) =="
    tail -20 "$cache/quit.log" 2>/dev/null
} > "$out"
rm -f "$out.cpu"
echo "Done: $out"
