"""Software Update (Settings): what can be updated, and updating it in place.

Sources, each checked on its own (a slow AUR doesn't hold the others):
    system   the distro's packages: pacman (checkupdates, pacman-contrib),
             dnf, apt, zypper
    aur      AUR packages through yay or paru (Arch family)
    flatpak  Flatpak apps and runtimes

Updating runs in the app, one step per source, with the output streamed to
the details log and a progress fraction read from pacman's "(3/12)" lines.
Root steps go through pkexec (the session's polkit agent asks for the
password). Sources without an unattended mode (apt, dnf, zypper) update
in a terminal, as does anything the user wants to watch there.

    srcs = sources()                     # [Source] available here
    ups = check(src)                     # [Update] (blocking: run in a thread)
    Runner(steps, on_line, on_fraction, on_done).start()
"""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from gi.repository import Gio, GLib


@dataclass
class Update:
    name: str
    old: str = ""
    new: str = ""


@dataclass
class Source:
    id: str
    title: str
    list_cmd: List[str]
    install: Optional[List[str]]           # run in the app (None: terminal only)
    terminal: str                          # the same, for a terminal
    parse: Callable[[str], List[Update]] = field(repr=False, default=None)


# -- parsing --------------------------------------------------------------------------
_ARROW = re.compile(r"^(\S+)\s+(\S+)\s+->\s+(\S+)")


def parse_arrow(out: str) -> List[Update]:
    """pacman / checkupdates / yay -Qua / paru -Qua: "name old -> new"."""
    ups = []
    for ln in out.splitlines():
        m = _ARROW.match(ln.strip())
        if m and "[ignored]" not in ln:
            ups.append(Update(*m.groups()))
    return ups


def parse_apt(out: str) -> List[Update]:
    """apt list --upgradable: "name/suite new arch [upgradable from: old]"."""
    ups = []
    for ln in out.splitlines():
        if "/" not in ln or ln.startswith("Listing"):
            continue
        parts = ln.split()
        old = ln.rsplit("from:", 1)[1].strip(" ]") if "from:" in ln else ""
        ups.append(Update(parts[0].split("/")[0], old, parts[1] if len(parts) > 1 else ""))
    return ups


def parse_columns(out: str) -> List[Update]:
    """dnf check-update / zypper list-updates: the name, then the version."""
    ups = []
    for ln in out.splitlines():
        s = ln.strip()
        if not s or s.startswith(("Last metadata", "Loading", "S |", "--", "Obsoleting")):
            continue
        if "|" in s:                                          # zypper table
            cols = [c.strip() for c in s.split("|")]
            if len(cols) >= 6:
                ups.append(Update(cols[2], cols[3], cols[4]))
            continue
        parts = s.split()
        ups.append(Update(parts[0], "", parts[1] if len(parts) > 1 else ""))
    return ups


def parse_flatpak(out: str) -> List[Update]:
    """flatpak remote-ls --updates --columns=name,application,version (tab separated)."""
    ups = []
    for ln in out.splitlines():
        cols = ln.split("\t")
        if len(cols) >= 2 and cols[1].strip() and cols[1].strip() != "Application ID":
            ups.append(Update(cols[0].strip() or cols[1].strip(), "", cols[2].strip() if len(cols) > 2 else ""))
    return ups


# -- what is here --------------------------------------------------------------------
def _aur_helper() -> Optional[str]:
    return next((h for h in ("paru", "yay") if shutil.which(h)), None)


def sources() -> List[Source]:
    out = []
    if shutil.which("pacman"):
        lst = ["checkupdates", "--nocolor"] if shutil.which("checkupdates") else ["pacman", "-Qu"]
        out.append(Source("system", "System", lst, ["pkexec", "pacman", "-Syu", "--noconfirm"],
                          "sudo pacman -Syu", parse_arrow))
        helper = _aur_helper()
        if helper:
            flags = (["--skipreview"] if helper == "paru" else
                     ["--answerdiff", "None", "--answerclean", "None", "--answeredit", "None"])
            out.append(Source("aur", "AUR", [helper, "-Qua"],
                              [helper, "-Sua", "--noconfirm", "--sudo", "pkexec"] + flags,
                              f"{helper} -Sua", parse_arrow))
    elif shutil.which("dnf"):
        out.append(Source("system", "System", ["dnf", "-q", "check-update"], None,
                          "sudo dnf upgrade", parse_columns))
    elif shutil.which("apt"):
        out.append(Source("system", "System", ["apt", "list", "--upgradable"], None,
                          "sudo apt update && sudo apt upgrade", parse_apt))
    elif shutil.which("zypper"):
        out.append(Source("system", "System", ["zypper", "-q", "list-updates"], None,
                          "sudo zypper update", parse_columns))
    if shutil.which("flatpak"):
        out.append(Source("flatpak", "Flatpak", ["flatpak", "remote-ls", "--updates",
                                                 "--columns=name,application,version"],
                          ["flatpak", "update", "-y", "--noninteractive"], "flatpak update", parse_flatpak))
    return out


def check(src: Source) -> Optional[List[Update]]:
    """The updates of one source; None when the check itself failed
    (offline, AUR down...). checkupdates exits 2 for "nothing"."""
    try:
        p = subprocess.run(src.list_cmd, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode not in (0, 2, 100) and not p.stdout.strip():   # 100: dnf "updates available"
        return None
    return src.parse(p.stdout)


# -- updating ------------------------------------------------------------------------
_STEP = re.compile(r"\(\s*(\d+)/(\d+)\)")


class Runner:
    """Runs the steps ([argv]) one after the other, stopping at the first
    failure. on_line(text), on_fraction(0..1 or None: pulsing),
    on_done(ok: bool, failed_step_index)."""

    def __init__(self, steps, on_line, on_fraction, on_done):
        self.steps = list(steps)
        self.on_line, self.on_fraction, self.on_done = on_line, on_fraction, on_done
        self.index = -1
        self.proc = None
        self.cancel = Gio.Cancellable()

    def start(self) -> None:
        self._next()

    def stop(self) -> None:
        self.cancel.cancel()
        if self.proc:
            self.proc.force_exit()

    def _next(self) -> None:
        self.index += 1
        if self.index >= len(self.steps):
            self.on_done(True, -1)
            return
        argv = self.steps[self.index]
        self.on_fraction(None)
        self.on_line("$ " + " ".join(argv))
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE)
        launcher.setenv("LC_ALL", "C", True)
        try:
            self.proc = launcher.spawnv(argv)
        except GLib.Error as e:
            self.on_line(str(e.message))
            self.on_done(False, self.index)
            return
        stream = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self._read(stream)

    def _read(self, stream) -> None:
        def got(s, res):
            try:
                line, _n = s.read_line_finish_utf8(res)
            except GLib.Error:
                line = None
            if line is None:
                self.proc.wait_async(self.cancel, self._exited)
                return
            for part in line.split("\r"):              # progress bars redraw with \r
                if part.strip():
                    m = _STEP.search(part)
                    if m and int(m.group(2)):
                        self.on_fraction(int(m.group(1)) / int(m.group(2)))
                    self.on_line(part.rstrip())
            self._read(s)
        stream.read_line_async(GLib.PRIORITY_DEFAULT, self.cancel, got)

    def _exited(self, proc, res) -> None:
        try:
            proc.wait_finish(res)
        except GLib.Error:
            pass
        if proc.get_if_exited() and proc.get_exit_status() == 0:
            self._next()
        else:
            self.on_done(False, self.index)
