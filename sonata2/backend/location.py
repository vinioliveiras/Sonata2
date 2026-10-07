"""Location Services (Vini): one switch for every app, and a switch per app
installed from packages.

Apps get your location from GeoClue (the system's location service) --
directly, or through the portal for Flatpak apps. Sonata writes GeoClue's
own settings, /etc/geoclue/conf.d/90-sonata.conf (pkexec: the password
is asked):

  - Location Services off: every source of location off (Wi-Fi, mobile,
    GPS, IP address...) -- no app gets it, sandboxed or not.
  - an app turned off: its own section, allowed=false (GeoClue knows a
    packaged app by the name it gives itself, so a hostile app could lie).

Browsers (Chrome, and Firefox when told to) may find the location
themselves over the network; they ask per site.

    available()             # GeoClue installed
    enabled() / denied()    # what was chosen (Sonata's location.json)
    set_enabled(on) / set_app(app_id, on)    # blocking: pkexec
"""
import os
import subprocess

from .. import config

NAME = "location"
DEFAULTS = {"enabled": True, "denied": []}
CONF_DIR = "/etc/geoclue/conf.d"
CONF = os.path.join(CONF_DIR, "90-sonata.conf")
SOURCES = ("wifi", "3g", "cdma", "modem-gps", "network-nmea", "compass", "static-source")


def available() -> bool:
    return any(os.path.exists(p) for p in ("/etc/geoclue/geoclue.conf", "/usr/lib/geoclue-2.0/geoclue",
                                           "/usr/libexec/geoclue", "/usr/lib/geoclue"))


def _load() -> dict:
    return config.load(NAME, DEFAULTS)


def enabled() -> bool:
    return bool(_load().get("enabled", True))


def denied() -> list:
    got = _load().get("denied") or []
    return sorted({a for a in got if isinstance(a, str) and a})


def app_key(app_id: str) -> str:
    """GeoClue's name for an app: its desktop id without .desktop."""
    return (app_id or "").removesuffix(".desktop")


def allowed(app_id: str) -> bool:
    return app_key(app_id) not in denied()


def render(on: bool, deny) -> str:
    out = ["# Written by Sonata (Settings > Apps): Location Services and the apps",
           "# turned off there. Changed from Sonata; a hand edit is replaced.", ""]
    if not on:
        for src in SOURCES:
            out += [f"[{src}]", "enable=false", ""]
    for app in sorted(set(deny)):
        if app and "[" not in app and "]" not in app and "\n" not in app:
            out += [f"[{app}]", "allowed=false", "system=false", "users=", ""]
    return "\n".join(out)


def _write(text: str, run=None) -> bool:
    """The file, as root (pkexec), then GeoClue started again to read it."""
    script = (f"mkdir -p {CONF_DIR} && cat > {CONF}.new && mv -f {CONF}.new {CONF} && "
              "(systemctl try-restart geoclue.service 2>/dev/null || true)")
    run = run or (lambda cmd, data: subprocess.run(cmd, input=data, text=True, timeout=120).returncode == 0)
    try:
        return bool(run(["pkexec", "sh", "-c", script], text))
    except (OSError, subprocess.SubprocessError):
        return False


def set_enabled(on: bool, run=None) -> bool:
    if not _write(render(bool(on), denied()), run):
        return False                                    # (cancelled: nothing changes)
    config.update(NAME, enabled=bool(on))
    return True


def set_app(app_id: str, on: bool, run=None) -> bool:
    key = app_key(app_id)
    deny = [a for a in denied() if a != key] + ([] if on else [key])
    if not _write(render(enabled(), deny), run):
        return False
    config.update(NAME, denied=sorted(set(deny)))
    return True
