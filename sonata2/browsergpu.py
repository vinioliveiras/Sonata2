"""WebGPU in Chromium browsers, for 3D browser games (Settings > Displays >
Games, off by default; Vini: "Unable to find a GPU" in a WebGPU game).

On Linux, Chrome's WebGPU needs Vulkan, and Chrome's Vulkan draws nothing
in a Wayland window (the window comes out transparent). So, turned on, the
browsers' flag files (titlebars.CHROMIUM_FLAGS, read by the Arch launchers)
get, in a block of Sonata's own:

    --ozone-platform=x11          # through XWayland, where Vulkan draws
    --enable-unsafe-webgpu
    --enable-features=Vulkan      # or Vulkan added to the file's own line
                                  # (Chrome keeps only the last one given)

Turned off, all of it goes again; the user's own lines stay. Sonata's web
apps (webapps.chromium_command) stay on Wayland without Vulkan either way.

Firefox (and LibreWolf, Floorp) draw WebGPU on Wayland themselves: their
profiles' user.js get dom.webgpu.enabled; turned off, that line goes and
prefs.js forgets the value Firefox copied there (once it is closed).

    enabled() / set_enabled(on)
    apply(on, cfg_dir=None)       # the flag files of the installed browsers
"""
import os

from gi.repository import GLib

from . import config

NAME = "browsergpu"
DEFAULTS = {"enabled": False, "appended": {}}   # appended: flag file -> Vulkan joined the user's line
MARK = "# Sonata: WebGPU for browser games (Settings > Displays > Games)"
FLAGS = ("--ozone-platform=x11", "--enable-unsafe-webgpu")
FEATURE = "Vulkan"
OWN = "--enable-features=" + FEATURE


def enabled() -> bool:
    return bool(config.load(NAME, DEFAULTS).get("enabled"))


def set_enabled(on: bool) -> None:
    config.update(NAME, enabled=bool(on))
    apply(bool(on))


def edit(lines: list, on: bool, appended: bool = False):
    """(lines, appended): the flag file with Sonata's WebGPU flags added or
    taken out. appended: Vulkan was added to the user's own
    --enable-features line (no comment can mark it: the launchers pass a
    line's every word to the browser)."""
    out, i = [], 0
    while i < len(lines):                                  # our block out (added again below if on)
        if lines[i].strip() == MARK:
            i += 1
            while i < len(lines) and (lines[i].strip() in FLAGS or lines[i].strip() == OWN):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    user = next((j for j, ln in enumerate(out) if ln.strip().startswith("--enable-features=")), None)
    if appended and user is not None and not on:           # Vulkan out of the user's line again
        names = [n for n in out[user].strip().split("=", 1)[1].split(",") if n and n != FEATURE]
        out[user] = "--enable-features=" + ",".join(names)
        appended = False
    if on:
        if user is None:
            out += [MARK, OWN, *FLAGS]
        else:
            names = [n for n in out[user].strip().split("=", 1)[1].split(",") if n]
            if FEATURE not in names:
                out[user] = "--enable-features=" + ",".join(names + [FEATURE])
                appended = True
            out += [MARK, *FLAGS]
    return out, appended


MOZ_KEY = "dom.webgpu.enabled"
MOZ_LINE = 'user_pref("dom.webgpu.enabled", true);  // Sonata WebGPU (Settings > Displays > Games)'
MOZILLA = ("~/.mozilla/firefox", "~/.config/mozilla/firefox", "~/.librewolf", "~/.floorp",
           "~/.var/app/org.mozilla.firefox/.mozilla/firefox")


def mozilla_profiles(bases=MOZILLA) -> list:
    out = []
    for base in bases:
        base = os.path.expanduser(base)
        try:
            names = os.listdir(base)
        except OSError:
            continue
        out += [os.path.join(base, n) for n in names if os.path.isfile(os.path.join(base, n, "prefs.js"))]
    return out


def apply_mozilla(on: bool, profiles=None) -> None:
    from . import titlebars
    for prof in mozilla_profiles() if profiles is None else profiles:
        user = os.path.join(prof, "user.js")
        try:
            with open(user, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            lines = []
        new = [ln for ln in lines if "Sonata WebGPU" not in ln] + ([MOZ_LINE] if on else [])
        if new != lines:
            titlebars._write(user, "\n".join(new) + ("\n" if new else ""))
        if on or os.path.lexists(os.path.join(prof, "lock")):
            continue                                      # running: it writes prefs.js back on quit
        prefs = os.path.join(prof, "prefs.js")
        try:
            with open(prefs, encoding="utf-8") as f:
                plines = f.read().splitlines()
        except OSError:
            continue
        kept = [ln for ln in plines if f'"{MOZ_KEY}"' not in ln]
        if kept != plines:
            titlebars._write(prefs, "\n".join(kept) + "\n")


def apply(on: bool, cfg_dir=None) -> None:
    from . import titlebars
    cfg = cfg_dir or GLib.get_user_config_dir()
    for name in titlebars.CHROMIUM_FLAGS:
        path = os.path.join(cfg, name)
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            if not on or not titlebars._installed(name):
                continue
            lines = []
        except OSError:
            continue
        state = config.load(NAME, DEFAULTS)
        done = dict(state.get("appended") or {})
        new, appended = edit(lines, on, bool(done.get(name)))
        if new != lines:
            titlebars._write(path, "\n".join(new) + "\n")
        if appended != bool(done.get(name)):
            done[name] = appended
            config.update(NAME, appended=done)
    if cfg_dir is None:
        apply_mozilla(on)
