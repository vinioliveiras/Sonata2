#!/usr/bin/env python3
"""Generate Sonata's menu-bar icons (original drawings in the SF Symbols
spirit: filled speaker, fan-shaped Wi-Fi, proportional battery) into
sonata2/data/icons/Sonata/status/symbolic. Symbolic SVGs: black is
recoloured to the label colour; class="success"/"error" to green/red.
Run after editing:  python3 tools/gen-status-icons.py"""
import math
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "sonata2", "data", "icons", "Sonata", "status", "symbolic")
HEAD = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
FADED = 'opacity="0.25"'


def write(name, body):
    with open(os.path.join(OUT, name + ".svg"), "w") as f:
        f.write(HEAD + body + "</svg>\n")


def arc(cx, cy, r, deg, width, extra=""):
    a = math.radians(deg)
    x0, y0 = cx - r * math.sin(a), cy - r * math.cos(a)
    x1, y1 = cx + r * math.sin(a), cy - r * math.cos(a)
    return (f'<path d="M{x0:.2f} {y0:.2f}A{r} {r} 0 0 1 {x1:.2f} {y1:.2f}" fill="none" stroke="#000" '
            f'stroke-width="{width}" stroke-linecap="round" {extra}/>')


def side_arc(cx, cy, r, deg, width):
    a = math.radians(deg)
    x0, y0 = cx + r * math.cos(a), cy - r * math.sin(a)
    x1, y1 = cx + r * math.cos(a), cy + r * math.sin(a)
    return (f'<path d="M{x0:.2f} {y0:.2f}A{r} {r} 0 0 1 {x1:.2f} {y1:.2f}" fill="none" stroke="#000" '
            f'stroke-width="{width}" stroke-linecap="round"/>')


def volume():
    speaker = ('<path d="M1.6 5.6h2.1l3.1-2.7c.4-.35 1-.07 1 .46v9.28c0 .53-.6.81-1 .46L3.7 10.4H1.6'
               'A.9.9 0 0 1 .7 9.5v-3a.9.9 0 0 1 .9-.9z" fill="#000"/>')
    for level in range(4):
        waves = "".join(side_arc(7.2, 8, r, 42, 1.35) for r in (2.9, 5.2, 7.5)[:level])
        write(f"sonata-volume-{level}-symbolic", speaker + waves)
    write("sonata-volume-muted-symbolic", speaker +
          '<path d="M10.2 6.2l3.6 3.6M13.8 6.2l-3.6 3.6" stroke="#000" stroke-width="1.35" stroke-linecap="round"/>')


def wifi():
    cx, cy = 8, 13.4
    parts = [lambda x="": f'<circle cx="{cx}" cy="{cy - 0.2}" r="1.5" fill="#000" {x}/>',
             lambda x="": arc(cx, cy, 5.0, 44, 1.9, x),
             lambda x="": arc(cx, cy, 9.0, 44, 1.9, x)]
    for level in range(4):
        write(f"sonata-wifi-{level}-symbolic", "".join(p("" if i < level else FADED) for i, p in enumerate(parts)))
    write("sonata-wifi-off-symbolic", "".join(p(FADED) for p in parts) +
          '<path d="M2.5 2.2l11 11" stroke="#000" stroke-width="1.4" stroke-linecap="round"/>')


def battery():
    """24x24 canvas (the bar shows it at 24 px): a macOS-proportioned battery."""
    head = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
    shell = ('<rect x="1.5" y="7" width="19" height="10" rx="3" fill="none" stroke="#000" '
             'stroke-width="1.1" opacity="0.45"/>'
             '<path d="M21.7 10.2c.9.2 1.4.9 1.4 1.8s-.5 1.6-1.4 1.8z" fill="#000" opacity="0.45"/>')
    for tenth in range(11):
        w = 15.4 * tenth / 10
        for charging in (False, True):
            cls = ' class="success"' if charging else (' class="error"' if tenth <= 1 else "")
            fill = (f'<rect x="3.3" y="8.8" width="{max(w, 1.6):.2f}" height="6.4" rx="1.6" fill="#000"{cls}/>'
                    if tenth or charging else "")
            bolt = ('<path d="M12.6 5.2L8 12.6h3.4l-1 6.2 5-8h-3.5z" fill="#000"/>' if charging else "")
            name = f"sonata-battery-{tenth * 10}{'-charging' if charging else ''}-symbolic"
            with open(os.path.join(OUT, name + ".svg"), "w") as f:
                f.write(head + shell + fill + bolt + "</svg>\n")


def logo():
    with open(os.path.join(OUT, "..", "..", "actions", "symbolic", "sonata-logo-symbolic.svg"), "w") as f:
        f.write(HEAD + '<circle cx="8" cy="8" r="6.2" fill="#000"/></svg>\n')


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    volume()
    wifi()
    battery()
    logo()
    print("written to", os.path.normpath(OUT))
