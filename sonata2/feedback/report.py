"""Feedbacker's reports: one .zip in ~/Sonata Reports with what a
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


CRASH = "last-crash"                 # written by tools/sonata-session: "<epoch> <exit code>"


def crash() -> dict:
    """The last session's crash ({"time": epoch, "code": exit code}), or {}."""
    try:
        with open(os.path.join(log_dir(), CRASH), encoding="utf-8") as f:
            when, code = f.read().split()[:2]
        return {"time": int(when), "code": int(code)}
    except (OSError, ValueError):
        return {}


def clear_crash() -> None:
    """Reported: the next login doesn't open Feedbacker again."""
    try:
        os.remove(os.path.join(log_dir(), CRASH))
    except OSError:
        pass


def crash_text(c: dict) -> str:
    """"Sonata quit unexpectedly at 04:16 (exit code 134, SIGABRT)."""
    import signal
    code = c.get("code", 0)
    why = ""
    if code > 128:
        try:
            why = f", {signal.Signals(code - 128).name}"
        except ValueError:
            pass
    when = time.strftime("%H:%M on %b %d", time.localtime(c.get("time", 0)))
    return f"Sonata quit unexpectedly at {when} (exit code {code}{why})."


# -- what took the session down (from the logs it left) ----------------------------------------
HISTORY = "crashes.log"               # one line per crash: "<epoch> <kind> <exit code>" (in the reports too)
# kind -> what Feedbacker says; the first that matches wins (most specific first)
KINDS = (
    ("nvidia-memory", "The NVIDIA card ran out of memory (an app filled it).",
     ("NV_ERR_NO_MEMORY", "Failed to allocate NVKMS memory")),
    ("amd-reset", "The AMD graphics card stopped and was reset (a driver bug).",
     ("amdgpu", "ring gfx", "timeout")),
    ("gpu-reset", "The graphics card was reset.", ("GPU reset",)),
    ("nvidia-pcie", "The NVIDIA card's connection had errors (PCIe).", ("BadTLP", "Xid")),
    ("gpu-buffer", "The graphics card refused a buffer.", ("gbm_bo_create failed", "Failed to allocate auxilliary")),
    ("wayfire-abort", "Wayfire stopped on an internal error.", ("Fatal error(SIGABRT)", "dassert")),
    ("wayfire-segfault", "Wayfire crashed (segmentation fault).", ("Fatal error(SIGSEGV)", "wayfire[")),
)


def _read_log(name: str, limit: int = 4 << 20) -> str:
    try:
        with open(os.path.join(log_dir(), name), "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def classify(kernel: str, session: str) -> str:
    """The crash's kind from the kernel's lines and Wayfire's log."""
    text = kernel + "\n" + session
    for kind, _say, marks in KINDS:
        if kind == "amd-reset":
            if "amdgpu" in kernel and ("ring gfx" in kernel or "page fault" in kernel) and \
                    ("timeout" in kernel or "GPU reset" in session):
                return kind
            continue
        if kind == "wayfire-segfault":
            if "Fatal error(SIGSEGV)" in session or ("segfault at" in kernel and "wayfire[" in kernel):
                return kind
            continue
        if any(m in text for m in marks):
            return kind
    return "unknown"


def describe(kind: str) -> str:
    return next((say for k, say, _m in KINDS if k == kind), "The cause isn't in the logs.")


def note_crash(c: dict) -> str:
    """This crash's kind, written once into the history (Feedbacker)."""
    if not c:
        return ""
    for when, kind, _code in history():
        if when == c["time"]:
            return kind
    kind = classify(_read_log("kernel-at-crash.log"), _read_log("session.old.log") or _read_log("session.log"))
    try:
        with open(os.path.join(log_dir(), HISTORY), "a", encoding="utf-8") as f:
            f.write(f"{c['time']} {kind} {c.get('code', 0)}\n")
        logs.trim(os.path.join(log_dir(), HISTORY))
    except OSError:
        pass
    return kind


def history(days: float = None, now: float = None) -> list:
    """[(epoch, kind, code)], oldest first; only the last `days` when given."""
    out = []
    try:
        with open(os.path.join(log_dir(), HISTORY), encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[0].isdigit():
                    out.append((int(parts[0]), parts[1], int(parts[2]) if parts[2].lstrip("-").isdigit() else 0))
    except OSError:
        pass
    if days is not None:
        limit = (now or time.time()) - days * 86400
        out = [h for h in out if h[0] >= limit]
    return out


def history_text(days: float = 7, now: float = None) -> str:
    """"3 crashes in the last 7 days: 2 AMD resets, 1 NVIDIA memory"."""
    h = history(days, now)
    if not h:
        return ""
    short = {"nvidia-memory": "NVIDIA memory", "amd-reset": "AMD reset", "gpu-reset": "GPU reset",
             "nvidia-pcie": "NVIDIA PCIe", "gpu-buffer": "refused buffer", "wayfire-abort": "Wayfire error",
             "wayfire-segfault": "Wayfire crash", "unknown": "unknown"}
    counts = {}
    for _t, kind, _c in h:
        counts[kind] = counts.get(kind, 0) + 1
    parts = ", ".join(f"{n} {short.get(k, k)}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    return f"{len(h)} crash{'es' if len(h) != 1 else ''} in the last {int(days)} days: {parts}"


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


def create(title: str, description: str, doctor_text: str = None, now: float = None, info: dict = None) -> str:
    """Write ~/Sonata Reports/Sonata Report <date>.zip; returns its path.
    info: system_info() already gathered (it runs pacman and lspci)."""
    stamp = time.strftime("%Y-%m-%d at %H.%M.%S", time.localtime(now or time.time()))
    path = os.path.join(folder(), f"Sonata Report {stamp}.zip")
    info = info if info is not None else system_info()
    if doctor_text is None:
        try:
            from .. import doctor
            doctor_text = doctor.report_text()
        except Exception as e:          # noqa: BLE001 -- the report still goes out
            doctor_text = f"doctor failed: {e}\n"
    tmp = path + ".part"
    try:
        # strict_timestamps=False: a log dated before 1980 (clock reset) is stored as 1980, not refused
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
            z.writestr("description.txt", f"{title.strip()}\n\n{description.strip()}\n")
            c = crash()
            z.writestr("system.txt", "".join(f"{k}: {v}\n" for k, v in info.items())
                       + (f"Crash: {crash_text(c)}\n" if c else ""))
            z.writestr("doctor.txt", doctor_text)
            for p in _log_files():
                try:
                    z.write(p, "logs/" + os.path.basename(p))
                except (OSError, ValueError):
                    pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)                   # never a .part left behind
        except OSError:
            pass
        raise
    return path


TITLE_MAX = 200                      # characters of the title kept in the link


def issue_url(title: str, description: str, report_name: str = "", info: dict = None) -> str:
    """A GitHub "new issue" link with the title, description and versions."""
    info = info if info is not None else system_info()
    title = title.strip()
    if len(title) > TITLE_MAX:                       # a pasted log as the title: the link stays short
        title = title[:TITLE_MAX].rstrip() + "…"
    facts = "\n".join(f"- **{k}**: {v}" for k, v in info.items() if v)
    attach = (f"\n\n**Report:** please drag `{report_name}` (in ~/{FOLDER}) here." if report_name else "")
    body = f"### What happened\n\n{description.strip() or '(describe the problem)'}\n\n### System\n\n{facts}{attach}\n"

    def url(b):
        return ISSUES + "?" + urllib.parse.urlencode({"title": title or "Bug report", "body": b})
    while len(url(body)) > URL_MAX and len(description) > 200:
        description = description[: len(description) * 3 // 4]
        body = (f"### What happened\n\n{description.strip()}…\n\n(full text in the report)\n\n"
                f"### System\n\n{facts}{attach}\n")
    while len(url(body)) > URL_MAX and body:         # still too long (many packages, odd characters)
        body = body[: len(body) * 3 // 4]
    return url(body)
