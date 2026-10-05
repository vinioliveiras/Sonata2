"""Limit games' frame rate (Vini: Control Center > FPS Limit, Settings >
Displays; off by default) -- through frame-pacer (github.com/endjynn/frame-pacer,
MIT): a Vulkan layer for native Vulkan, DXVK and vkd3d-proton games that
reads ~/.config/frame-pacer/frame-pacer.conf about once a second, so a new
limit reaches a running game within a second.

    fpslimit.installed()              # frame-pacer's layer in ~/.local
    fpslimit.get()                    # "off", or a number of frames per second
    fpslimit.set("60")                # also "off", "max" (the display's refresh rate: refresh_hz)
    fpslimit.install(done)            # the latest release, into ~/.local (a thread; no sudo)
    fpslimit.env_for(info)            # ENABLE_FRAME_PACER=1 for game launchers (gpu.extra_env)

Only the top of frame-pacer's file is Sonata's (global_fps_limit, hud): a
game's own [section] written by hand stays. Its overlay stays hidden unless
Settings shows it. The layer only loads in what Sonata starts with
ENABLE_FRAME_PACER=1 -- game launchers (Steam, Faugus, Lutris, Heroic,
Bottles): browsers and other apps drawing with Vulkan are never limited.
Steam picks it up the next time it opens."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

from . import config

NAME = "fpslimit"
DEFAULTS = {"limit": "off", "hud": False, "refresh": 0}     # refresh: the display's Hz, for "max"
CHOICES = ("off", "30", "60", "90", "120", "max")
REPO = "endjynn/frame-pacer"
ASSET_RE = re.compile(r"^frame-pacer-.*-linux-x86_64-multilib\.tar\.xz$")
LAUNCHERS = {"steam", "steam-native", "steam-runtime", "com.valvesoftware.Steam", "faugus-launcher",
             "io.github.Faugus.faugus-launcher", "net.lutris.Lutris", "lutris", "heroic",
             "com.heroicgameslauncher.hgl", "com.usebottles.bottles", "bottles"}


def _home(*p) -> str:
    return os.path.join(os.path.expanduser("~"), *p)


def conf_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or _home(".config")
    return os.path.join(base, "frame-pacer", "frame-pacer.conf")


def installed() -> bool:
    d = _home(".local", "share", "vulkan", "implicit_layer.d")
    try:
        return any("frame_pacer" in n or "frame-pacer" in n for n in os.listdir(d)) and \
            os.path.isdir(_home(".local", "lib", "frame-pacer"))
    except OSError:
        return False


def settings() -> dict:
    return config.load(NAME, DEFAULTS)


def get() -> str:
    v = str(settings().get("limit", "off"))
    return v if v in CHOICES else "off"


def resolve(limit: str, refresh_hz: float = None) -> str:
    """What frame-pacer gets: "off" or 1..999 ("max": the display's rate, rounded)."""
    if limit == "max":
        return str(max(1, min(999, round(refresh_hz)))) if refresh_hz else "off"
    if limit.isdigit() and 1 <= int(limit) <= 999:
        return limit
    return "off"


def conf_text(old: str, limit: str, hud: bool) -> str:
    """frame-pacer.conf with Sonata's two keys at the top; the game sections
    (from the first "[" line) and any other top lines stay as they were."""
    lines = old.splitlines()
    first = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith("[")), len(lines))
    keep = [ln for ln in lines[:first] if ln.strip() and "Sonata" not in ln
            and not re.match(r"\s*(global_fps_limit|hud)\s*=", ln)]
    top = ["# Sonata: Control Center > FPS Limit (these two lines)",
           f"global_fps_limit = {limit}", f"hud = {'on' if hud else 'off'}"]
    sections = lines[first:]
    return "\n".join(top + keep + ([""] + sections if sections else [])) + "\n"


def write_conf(limit: str, hud: bool) -> None:
    path = conf_path()
    try:
        with open(path, encoding="utf-8") as f:
            old = f.read()
    except OSError:
        old = ""
    new = conf_text(old, limit, hud)
    if new != old:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        config.atomic_write(path, new.encode("utf-8"), fsync=False)


def set(limit: str, refresh_hz: float = None, hud: bool = None) -> None:   # noqa: A001 -- the module's verb
    cfg = settings()
    if limit not in CHOICES:
        limit = "off"
    hud = cfg.get("hud", False) if hud is None else bool(hud)
    refresh_hz = refresh_hz or cfg.get("refresh") or 0
    config.update(NAME, limit=limit, hud=hud, refresh=refresh_hz)
    write_conf(resolve(limit, refresh_hz), hud)


def env_for(info) -> dict:
    """ENABLE_FRAME_PACER=1 for game launchers once frame-pacer is here."""
    did = ((info.get_id() if info else "") or "").removesuffix(".desktop")
    return {"ENABLE_FRAME_PACER": "1"} if did in LAUNCHERS and installed() else {}


# -- install (a release: prebuilt, no compiler, no sudo) --------------------------------------
def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "sonata2", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_asset(releases: list):
    """(tarball url, sha256 url) of the newest release with a Linux x86_64 archive."""
    for rel in releases or []:
        assets = {a.get("name"): a.get("browser_download_url") for a in rel.get("assets", [])}
        for name, url in assets.items():
            if name and ASSET_RE.match(name) and assets.get(name + ".sha256"):
                return url, assets[name + ".sha256"]
    return None


def install_blocking() -> str:
    """Download, verify and install frame-pacer; returns "" or why it failed."""
    try:
        found = latest_asset(json.loads(_get(f"https://api.github.com/repos/{REPO}/releases")))
        if not found:
            return "no Linux release found"
        data = _get(found[0], 300)
        want = _get(found[1]).decode().split()[0].lower()
        if hashlib.sha256(data).hexdigest() != want:
            return "the download didn't match its checksum"
        tmp = tempfile.mkdtemp(prefix="sonata-frame-pacer-")
        try:
            path = os.path.join(tmp, "fp.tar.xz")
            with open(path, "wb") as f:
                f.write(data)
            with tarfile.open(path) as t:
                t.extractall(tmp, filter="data")
            dirs = [d for d in os.listdir(tmp) if os.path.isfile(os.path.join(tmp, d, "install.sh"))]
            if not dirs:
                return "the release has no installer"
            r = subprocess.run(["sh", "./install.sh"], cwd=os.path.join(tmp, dirs[0]), capture_output=True,
                               text=True, timeout=120, stdin=subprocess.DEVNULL)
            if r.returncode:
                return (r.stderr or r.stdout).strip()[-300:] or "the installer failed"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:                         # offline, GitHub down...
        return str(e)
    cfg = settings()
    write_conf(resolve(get(), cfg.get("refresh")), cfg.get("hud", False))
    return ""


def install(done=None) -> None:
    from .backend import system
    system.run_async(install_blocking, done)
