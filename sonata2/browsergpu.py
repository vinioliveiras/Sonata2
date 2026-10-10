"""Vulkan and WebGPU, per app (Vini: a toggle for each app; the one switch
for every browser is gone). Right-click an app in the Dock or Launchpad >
Options, or Settings > Apps > the app > Graphics.

What the toggles do depends on the app (kind(info)):

  chromium  Chrome, Chromium, Brave, Edge, Vivaldi, Thorium. Their Arch
            launcher reads a flag file (titlebars.CHROMIUM_FLAGS): Sonata's
            block there holds --ozone-platform=x11 (Chrome's Vulkan draws
            nothing in a Wayland window) and --enable-unsafe-webgpu, and
            Vulkan joins --enable-features. The file is the state: it works
            however the browser is opened. Flatpak browsers: the same flags
            at launch.
  webapp    Sonata's web apps: the same flags in webapps.chromium_command,
            and the browser's own program past its launcher, so the
            browser's flags never reach them (Vini: WhatsApp showed
            "unsupported command-line flag: --enable-unsafe-webgpu").
  electron  Discord, VS Code...: the same flags at launch.
  mozilla   Firefox, LibreWolf, Floorp: WebGPU only (dom.webgpu.enabled in
            its profiles' user.js; they draw it on Wayland themselves).
  wine      Steam, Lutris, Heroic, Bottles, Wine: Vulkan (DXVK) on by
            default; off, Windows games draw with WineD3D (OpenGL).
  other     Vulkan only: GTK 4 and Qt 6 apps draw with it (GSK_RENDERER,
            QSG_RHI_BACKEND); others ignore it.

WebGPU on Linux Chromium needs Vulkan: WebGPU on turns Vulkan on with it.

    supports(info) -> (vulkan, webgpu)
    vulkan(info) / webgpu(info) / set_vulkan(info, on) / set_webgpu(info, on)
    launch_env(info) / launch_args(info)      # apps.py, through gpu.extra_*
    menu_items(info, Item)                    # Dock and Launchpad
"""
import glob
import os
import re
import shlex

from gi.repository import GLib

from . import config

NAME = "browsergpu"
# vulkan / webgpu: app key -> the user's choice (apps without a file of their
# own); appended: flag file -> Vulkan joined the user's --enable-features line
DEFAULTS = {"vulkan": {}, "webgpu": {}, "appended": {}}
MARK = "# Sonata: Vulkan / WebGPU (the app's right-click menu > Options)"
MARKS = (MARK, "# Sonata: WebGPU for browser games (Settings > Displays > Games)")   # and before
X11 = "--ozone-platform=x11"
WEBGPU = "--enable-unsafe-webgpu"
FLAGS = (X11, WEBGPU)
FEATURE = "Vulkan"
OWN = "--enable-features=" + FEATURE
NOTE = "Takes effect the next time it opens."

# the program -> its launcher's flag file
CHROMIUM_BINS = {"google-chrome": "chrome-flags.conf", "google-chrome-stable": "chrome-flags.conf",
                 "google-chrome-beta": "chrome-beta-flags.conf", "google-chrome-unstable": "chrome-dev-flags.conf",
                 "chromium": "chromium-flags.conf", "brave": "brave-flags.conf",
                 "brave-browser": "brave-flags.conf", "vivaldi": "vivaldi-stable.conf",
                 "vivaldi-stable": "vivaldi-stable.conf", "thorium-browser": "thorium-flags.conf",
                 "microsoft-edge-stable": "microsoft-edge-stable-flags.conf"}
CHROMIUM_FLATPAKS = {"com.google.Chrome", "com.google.ChromeDev", "org.chromium.Chromium", "com.brave.Browser",
                     "com.microsoft.Edge", "com.vivaldi.Vivaldi",
                     "io.github.ungoogled_software.ungoogled_chromium"}
MOZILLA = {"firefox": ("~/.mozilla/firefox", "~/.config/mozilla/firefox"), "firefox-esr": ("~/.mozilla/firefox",),
           "librewolf": ("~/.librewolf",), "floorp": ("~/.floorp",),
           "org.mozilla.firefox": ("~/.var/app/org.mozilla.firefox/.mozilla/firefox",),
           "io.gitlab.librewolf-community": ("~/.var/app/io.gitlab.librewolf-community/.librewolf",),
           "one.ablaze.floorp": ("~/.var/app/one.ablaze.floorp/.floorp",)}
WINE = {"steam", "steam-native", "steam-runtime", "com.valvesoftware.Steam", "lutris", "net.lutris.Lutris",
        "heroic", "com.heroicgameslauncher.hgl", "bottles", "com.usebottles.bottles", "faugus-launcher",
        "io.github.Faugus.faugus-launcher", "wine", "wine64", "proton", "umu-run"}
FLATPAK_DIRS = ("/var/lib/flatpak/app", "~/.local/share/flatpak/app")
WINED3D_ENV = {"PROTON_USE_WINED3D": "1", "WINEDLLOVERRIDES": "d3d9,d3d10core,d3d11,dxgi=b"}
VULKAN_ENV = {"GSK_RENDERER": "vulkan", "QSG_RHI_BACKEND": "vulkan"}


# -- the flag files ------------------------------------------------------------------------------
def _flags(vulkan: bool, webgpu: bool) -> list:
    """Sonata's own lines for these choices (Vulkan goes in --enable-features)."""
    return ([X11] if vulkan or webgpu else []) + ([WEBGPU] if webgpu else [])


def edit(lines: list, flags, appended: bool = False):
    """(lines, appended): the flag file with Sonata's block set to `flags`
    ([] = taken out; Vulkan joins --enable-features with any). appended:
    Vulkan was added to the user's own --enable-features line (no comment
    can mark it: the launchers pass a line's every word to the browser)."""
    flags = list(flags or ())
    out, i = [], 0
    while i < len(lines):                                  # our block out (added again below)
        if lines[i].strip() in MARKS:
            i += 1
            while i < len(lines) and (lines[i].strip() in FLAGS or lines[i].strip() == OWN):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    user = next((j for j, ln in enumerate(out) if ln.strip().startswith("--enable-features=")), None)
    if appended and user is not None and not flags:        # Vulkan out of the user's line again
        names = [n for n in out[user].strip().split("=", 1)[1].split(",") if n and n != FEATURE]
        out[user] = "--enable-features=" + ",".join(names)
        appended = False
    if flags:
        if user is None:
            out += [MARK, OWN, *flags]
        else:
            names = [n for n in out[user].strip().split("=", 1)[1].split(",") if n]
            if FEATURE not in names:
                out[user] = "--enable-features=" + ",".join(names + [FEATURE])
                appended = True
            out += [MARK, *flags]
    return out, appended


def block(lines: list) -> list:
    """The flags in Sonata's block of a flag file ([] = none)."""
    out, inside = [], False
    for ln in lines:
        s = ln.strip()
        if s in MARKS:
            inside = True
        elif inside and s in FLAGS:
            out.append(s)
        elif inside and s != OWN:
            inside = False
    return out


def _cfg(cfg_dir=None) -> str:
    return cfg_dir or GLib.get_user_config_dir()


def _read(path: str) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except OSError:
        return []


def write_flag_file(name: str, flags, cfg_dir=None) -> None:
    from . import titlebars
    path = os.path.join(_cfg(cfg_dir), name)
    lines = _read(path)
    done = dict(config.load(NAME, DEFAULTS).get("appended") or {})
    new, appended = edit(lines, flags, bool(done.get(name)))
    if new != lines and (os.path.exists(path) or flags):
        titlebars._write(path, "\n".join(new) + "\n")
    if appended != bool(done.get(name)):
        done[name] = appended
        config.update(NAME, appended=done)


# -- Firefox ------------------------------------------------------------------------------------
MOZ_KEY = "dom.webgpu.enabled"
MOZ_LINE = 'user_pref("dom.webgpu.enabled", true);  // Sonata WebGPU (Allow WebGPU)'


def mozilla_profiles(bases) -> list:
    out = []
    for base in bases:
        base = os.path.expanduser(base)
        try:
            names = os.listdir(base)
        except OSError:
            continue
        out += [os.path.join(base, n) for n in names if os.path.isfile(os.path.join(base, n, "prefs.js"))]
    return out


def mozilla_on(profiles) -> bool:
    return any("Sonata WebGPU" in ln for p in profiles for ln in _read(os.path.join(p, "user.js")))


def apply_mozilla(on: bool, profiles) -> None:
    from . import titlebars
    for prof in profiles:
        user = os.path.join(prof, "user.js")
        lines = _read(user)
        new = [ln for ln in lines if "Sonata WebGPU" not in ln] + ([MOZ_LINE] if on else [])
        if new != lines:
            titlebars._write(user, "\n".join(new) + ("\n" if new else ""))
        if on or os.path.lexists(os.path.join(prof, "lock")):
            continue                                      # running: it writes prefs.js back on quit
        prefs = os.path.join(prof, "prefs.js")
        plines = _read(prefs)
        kept = [ln for ln in plines if f'"{MOZ_KEY}"' not in ln]
        if kept != plines:
            titlebars._write(prefs, "\n".join(kept) + "\n")


# -- which app is which --------------------------------------------------------------------------
_kinds = {}


def _key(info) -> str:
    did = info.get_id() or ""
    return did[:-8] if did.endswith(".desktop") else did


def program(info):
    """(program name, flatpak) from its Exec line: ("google-chrome-stable",
    False), ("com.discordapp.Discord", True)."""
    try:
        words = shlex.split(info.get_commandline() or "")
    except (ValueError, AttributeError):
        return "", False
    words = [w for w in words if not re.match(r"^\w+=", w)]
    if words and os.path.basename(words[0]) == "env":
        words = [w for w in words[1:] if not w.startswith("-")]
    if not words:
        return "", False
    if os.path.basename(words[0]) == "flatpak" and "run" in words:
        rest = [w for w in words[words.index("run") + 1:] if not w.startswith("-")]
        return (rest[0] if rest else ""), True
    return os.path.basename(words[0]), False


def _which(name: str) -> str:
    import shutil
    return os.path.realpath(shutil.which(name) or name)


def electron(name: str, flatpak: bool) -> bool:
    """An Electron app: resources/app.asar next to its program, or a launcher
    script that starts electron."""
    if flatpak:
        return any(glob.glob(os.path.join(os.path.expanduser(base), name, "current", "active", "files", *("*",) * n,
                                          "resources", "app.asar"))
                   for base in FLATPAK_DIRS for n in range(4))
    path = _which(name)
    if os.path.exists(os.path.join(os.path.dirname(path), "resources", "app.asar")):
        return True
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return False
    return head.startswith(b"#!") and b"electron" in head.lower()


def kind(info) -> str:
    key = _key(info)
    if key not in _kinds:
        from . import webapps
        name, flatpak = program(info)
        if webapps.is_webapp(key):
            k = "webapp"
        elif name in CHROMIUM_BINS or name in CHROMIUM_FLATPAKS:
            k = "chromium"
        elif name in MOZILLA:
            k = "mozilla"
        elif key in WINE or name in WINE:
            k = "wine"
        elif name and electron(name, flatpak):
            k = "electron"
        else:
            k = "other"
        _kinds[key] = k
    return _kinds[key]


def flag_file(info):
    """The launcher's flag file of a (non-Flatpak) Chromium browser, or None."""
    name, flatpak = program(info)
    return None if flatpak else CHROMIUM_BINS.get(name)


def supports(info) -> tuple:
    k = kind(info)
    return k != "mozilla", k in ("chromium", "webapp", "electron", "mozilla")


# -- the choices ---------------------------------------------------------------------------------
def _choice(what: str, info):
    return (config.load(NAME, DEFAULTS).get(what) or {}).get(_key(info))


def webgpu(info, cfg_dir=None) -> bool:
    k = kind(info)
    if k == "chromium" and flag_file(info):
        return WEBGPU in block(_read(os.path.join(_cfg(cfg_dir), flag_file(info))))
    if k == "mozilla":
        return mozilla_on(mozilla_profiles(MOZILLA[program(info)[0]]))
    return k in ("webapp", "electron", "chromium") and bool(_choice("webgpu", info))


def vulkan(info, cfg_dir=None) -> bool:
    k = kind(info)
    if k == "mozilla":
        return False
    if k == "chromium" and flag_file(info):
        return X11 in block(_read(os.path.join(_cfg(cfg_dir), flag_file(info))))
    c = _choice("vulkan", info)
    if c is None:
        c = k == "wine"                                     # DXVK: Proton's own default
    return bool(c) or webgpu(info, cfg_dir)


def vulkan_forced(info, cfg_dir=None) -> bool:
    """WebGPU on keeps Vulkan on (Chromium's WebGPU draws with it)."""
    return kind(info) in ("chromium", "webapp", "electron") and webgpu(info, cfg_dir)


def _set(what: str, info, on: bool, cfg_dir=None) -> None:
    k = kind(info)
    if k == "chromium" and flag_file(info):
        vk = on if what == "vulkan" else vulkan(info, cfg_dir)
        wg = on if what == "webgpu" else webgpu(info, cfg_dir)
        write_flag_file(flag_file(info), _flags(vk, wg), cfg_dir)
        return
    if k == "mozilla":
        if what == "webgpu":
            apply_mozilla(on, mozilla_profiles(MOZILLA[program(info)[0]]))
        return
    choices = dict(config.load(NAME, DEFAULTS).get(what) or {})
    choices[_key(info)] = bool(on)
    config.update(NAME, **{what: choices})


def set_vulkan(info, on: bool, cfg_dir=None) -> None:
    _set("vulkan", info, on, cfg_dir)


def set_webgpu(info, on: bool, cfg_dir=None) -> None:
    _set("webgpu", info, on, cfg_dir)


# -- at launch ----------------------------------------------------------------------------------
def launch_args(info) -> list:
    """Flags for Electron apps and Flatpak browsers (gpu.extra_args)."""
    k = kind(info)
    if k == "electron" or (k == "chromium" and not flag_file(info)):
        vk, wg = vulkan(info), webgpu(info)
        return _flags(vk, wg) + ([OWN] if vk or wg else [])
    return []


def launch_env(info) -> dict:
    k = kind(info)
    if k == "wine" and not vulkan(info):
        return dict(WINED3D_ENV)
    if k == "other" and vulkan(info):
        return dict(VULKAN_ENV)
    return {}


def _features(flags: list, extra: str) -> list:
    """Chrome keeps only the last --enable-features: `extra` joins theirs."""
    at = next((i for i, f in enumerate(flags) if f.startswith("--enable-features=")), None)
    if at is None:
        return flags + ["--enable-features=" + extra]
    names = [n for n in flags[at].split("=", 1)[1].split(",") if n]
    return flags[:at] + ["--enable-features=" + ",".join(names + ([extra] if extra not in names else []))] + \
        flags[at + 1:]


# launchers that are programs, not scripts: (their browser, their flag file)
KNOWN_LAUNCHERS = {"chromium": ("/usr/lib/chromium/chromium", "chromium-flags.conf")}


def web_app_browser(browser: str, cfg_dir=None) -> list:
    """The start of a web app's command (webapps.chromium_command): with
    Sonata's block in the browser's flag file, the browser's own program --
    past the launcher, which would add that block -- with the user's own
    flags from the file. Unknown launchers: as they are."""
    target, name = KNOWN_LAUNCHERS.get(os.path.basename(browser), (None, None))
    if target is None:
        try:
            with open(browser, "rb") as f:
                head = f.read(8192)
        except OSError:
            return [browser]
        if not head.startswith(b"#!"):
            return [browser]
        text = head.decode("utf-8", "replace")
        m = re.search(r"([\w.+-]+\.conf)\b", text)
        x = re.search(r"^\s*exec\s+[\"']?(/[^\s\"']+)", text, re.M)
        if not (m and x):
            return [browser]
        target, name = x.group(1), m.group(1)
    lines = _read(os.path.join(_cfg(cfg_dir), name))
    if not block(lines) or not (os.path.isfile(target) and os.access(target, os.X_OK)):
        return [browser]
    done = config.load(NAME, DEFAULTS).get("appended") or {}
    kept, _a = edit(lines, [], bool(done.get(name)))
    flags = []
    for ln in kept:
        if ln.strip() and not ln.lstrip().startswith("#"):
            try:
                flags += shlex.split(ln)
            except ValueError:
                flags += ln.split()
    return [target, *flags]


def web_app_command(app: str, browser: str, cfg_dir=None) -> list:
    """The browser and the web app's own Vulkan/WebGPU flags; without them
    a Wayland window without Vulkan even when the browser's file asks
    for them (an unknown launcher adds that file)."""
    from . import webapps
    info = _WebApp(webapps.desktop_id(app))
    argv = web_app_browser(browser, cfg_dir)
    vk, wg = vulkan(info), webgpu(info)
    if vk or wg:
        return _features(argv + _flags(vk, wg), FEATURE)
    if argv == [browser] and block(_read(os.path.join(_cfg(cfg_dir), _file_of(browser)))):
        return argv + ["--ozone-platform=wayland", "--disable-features=Vulkan"]
    return argv


def _file_of(browser: str) -> str:
    return CHROMIUM_BINS.get(os.path.basename(browser), "-")


class _WebApp:
    def __init__(self, did):
        self.did = did

    def get_id(self):
        return self.did + ".desktop"

    def get_commandline(self):
        return ""


# -- menus --------------------------------------------------------------------------------------
def menu_items(info, Item) -> list:
    if info is None:
        return []
    vk_ok, wg_ok = supports(info)
    items = []
    if vk_ok:
        items.append(Item("Use Vulkan", lambda on: set_vulkan(info, on), checked=vulkan(info),
                          enabled=not vulkan_forced(info)))
    if wg_ok:
        items.append(Item("Allow WebGPU", lambda on: set_webgpu(info, on), checked=webgpu(info)))
    return items


def vulkan_note(info) -> str:
    return {"chromium": "Draws through XWayland with Vulkan. Restart it after changing.",
            "webapp": "Draws through XWayland with Vulkan.",
            "electron": "Draws through XWayland with Vulkan.",
            "wine": "DXVK for Windows games. Off: WineD3D (OpenGL).",
            }.get(kind(info), "Apps made with GTK 4 or Qt 6 draw with Vulkan; others ignore it.")


def webgpu_note(info) -> str:
    return "For 3D web games." + (" Turns Vulkan on too." if kind(info) != "mozilla" else "") + \
        (" Restart it after changing." if kind(info) in ("chromium", "mozilla") else "")
