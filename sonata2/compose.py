"""ç on U.S. International (Vini: it couldn't be typed). With us(intl),
' is a dead key, and Linux's compose tables make ' + c a "ć" (c acute),
where Windows and macOS give "ç". ~/.XCompose -- read by GTK, Qt and
Chromium / Electron apps -- gets the two lines that make it "ç" / "Ç",
after the locale's own table (include "%L"), so every other combination
stays. A file of the user's own keeps its lines: ours are added to it.

    compose.ensure()          # at login (autostart); apps read it as they start"""
import os

MARK = "# Sonata: ' + c types ç on U.S. International (as on Windows and macOS)"
RULES = ('<dead_acute> <c> : "ç" ccedilla', '<dead_acute> <C> : "Ç" Ccedilla')


def path() -> str:
    return os.environ.get("XCOMPOSEFILE") or os.path.expanduser("~/.XCompose")


def ensure(target: str = None) -> bool:
    """True when the file has the cedilla rules (written now or before)."""
    target = target or path()
    try:
        with open(target, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = None
    except OSError:
        return False
    if text is not None and all(r.split(" :")[0] in text for r in RULES):
        return True                                  # (ours, or the user's own lines for it)
    if text is None:
        out = MARK + "\ninclude \"%L\"\n" + "\n".join(RULES) + "\n"
    else:
        out = text + ("" if text.endswith("\n") or not text else "\n") + "\n" + MARK + "\n" + "\n".join(RULES) + "\n"
        if "include" not in text:                    # a file without the locale's table would lose the rest
            out = "include \"%L\"\n" + out
    try:
        tmp = target + ".sonata-tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(out)
        os.replace(tmp, target)
    except OSError:
        return False
    return True
