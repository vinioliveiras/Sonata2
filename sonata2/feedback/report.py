"""Feedback Assistant's reports: one .zip in ~/Sonata Reports with what a
bug report needs -- your description, Sonata's logs (~/.cache/sonata2),
`sonata2 doctor` and the system's versions. Nothing leaves the computer:
"Report on GitHub" opens a pre-filled issue in the browser and you attach
the .zip yourself.

Monitoring is Sonata's detailed logging (logs.py): with it off, the logs
only hold errors, usually not enough to trace a bug."""
import os
import platform
import shutil
import subprocess
import time
import urllib.parse
import zipfile

from .. import __version__, logs

FOLDER = "Sonata Reports"
ISSUES = "https://github.com/vinioliveiras/sonata2/issues/new"
URL_MAX = 6000                       # browsers and GitHub cut longer links
LOG_EXT = (".log", ".txt")
PACKAGES = ("mesa", "wayfire", "linux-cachyos", "linux", "gtk4", "libadwaita", "nvidia-utils", "nvidia-open-dkms")


def folder() -> str:
    """~/Sonata Reports (created)."""
    path = os.path.join(os.path.expanduser("~"), FOLDER)
    os.makedirs(path, exist_ok=True)
    return path


def log_dir() -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2")


def monitoring() -> bool:
    return logs.verbose()


def set_monitoring(on: bool) -> None:
    logs.set_verbose(on)


def _run(cmd, timeout=5) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _distro() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def system_info() -> dict:
    """Short facts for the issue and system.txt (no names, no paths)."""
    pkgs = _run(["pacman", "-Q"] + list(PACKAGES)) if shutil.which("pacman") else ""
    gpus = [ln.split(": ", 1)[-1] for ln in _run(["lspci"]).splitlines()
            if "VGA" in ln or "3D controller" in ln or "Display controller" in ln]
    return {"Sonata": __version__, "System": _distro(), "Kernel": platform.release(),
            "Desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
            "GPU": "; ".join(gpus), "Monitoring": "on" if monitoring() else "off",
            "Packages": ", ".join(" ".join(ln.split()) for ln in pkgs.splitlines())}


def _log_files() -> list:
    d = log_dir()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    return [os.path.join(d, n) for n in names
            if n.endswith(LOG_EXT) and n != "doctor.txt" and os.path.isfile(os.path.join(d, n))]


def create(title: str, description: str, doctor_text: str = None, now: float = None) -> str:
    """Write ~/Sonata Reports/Sonata Report <date>.zip; returns its path."""
    stamp = time.strftime("%Y-%m-%d at %H.%M.%S", time.localtime(now or time.time()))
    path = os.path.join(folder(), f"Sonata Report {stamp}.zip")
    info = system_info()
    if doctor_text is None:
        try:
            from .. import doctor
            doctor_text = doctor.report_text()
        except Exception as e:          # noqa: BLE001 -- the report still goes out
            doctor_text = f"doctor failed: {e}\n"
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("description.txt", f"{title.strip()}\n\n{description.strip()}\n")
        z.writestr("system.txt", "".join(f"{k}: {v}\n" for k, v in info.items()))
        z.writestr("doctor.txt", doctor_text)
        for p in _log_files():
            try:
                z.write(p, "logs/" + os.path.basename(p))
            except OSError:
                pass
    os.replace(tmp, path)
    return path


def issue_url(title: str, description: str, report_name: str = "") -> str:
    """A GitHub "new issue" link with the title, description and versions."""
    info = system_info()
    facts = "\n".join(f"- **{k}**: {v}" for k, v in info.items() if v)
    attach = (f"\n\n**Report:** please drag `{report_name}` (in ~/{FOLDER}) here." if report_name else "")
    body = f"### What happened\n\n{description.strip() or '(describe the problem)'}\n\n### System\n\n{facts}{attach}\n"

    def url(b):
        return ISSUES + "?" + urllib.parse.urlencode({"title": title.strip() or "Bug report", "body": b})
    while len(url(body)) > URL_MAX and len(description) > 200:
        description = description[: len(description) * 3 // 4]
        body = (f"### What happened\n\n{description.strip()}…\n\n(full text in the report)\n\n"
                f"### System\n\n{facts}{attach}\n")
    return url(body)
