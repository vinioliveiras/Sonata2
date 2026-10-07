"""Uninstalling an app (dropped on the Trash, or Launchpad's "Move to Trash").

Who owns the app's desktop entry decides how:
    flatpak   exported by Flatpak           -> flatpak uninstall (user or system)
    package   owned by the distro's package -> pacman -Rns / apt remove / dnf remove / zypper remove
    shortcut  a file in ~/.local/share/applications that no package owns
              (AppImage launchers, web apps...) -> the file goes to the Trash
Sonata's own apps are never uninstalled. Before a package goes, what else
would leave with it is asked (pacman -Rns --print ...) and shown.

    o = owner(info)            # Owner or None
    o.also()                   # other packages removed with it (blocking)
    o.steps()                  # [argv] for updates.Runner (root via pkexec)
"""
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from gi.repository import GLib

from .. import apps


@dataclass
class Owner:
    kind: str                 # flatpak / package / shortcut
    name: str                 # flatpak app id, package name or file path
    manager: str = ""         # pacman / apt / dnf / zypper / flatpak-user / flatpak-system
    extra: List[str] = field(default_factory=list)

    def also(self) -> List[str]:
        """Packages removed along (no longer needed); [] when none or unknown."""
        if self.kind != "package":
            return []
        cmd = {"pacman": ["pacman", "-Rs", "--print", "--print-format", "%n", self.name],
               "apt": ["apt-get", "-s", "remove", "--autoremove", self.name]}.get(self.manager)
        if not cmd or not shutil.which(cmd[0]):
            return []
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                 env={**os.environ, "LC_ALL": "C"}).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        if self.manager == "pacman":
            names = [ln.strip() for ln in out.splitlines() if ln.strip() and " " not in ln.strip()]
        else:
            names = [ln.split()[1] for ln in out.splitlines() if ln.startswith("Remv ")]
        return [n for n in names if n != self.name]

    def steps(self) -> List[List[str]]:
        if self.kind == "flatpak":
            scope = "--user" if self.manager == "flatpak-user" else "--system"
            return [["flatpak", "uninstall", "-y", "--noninteractive", scope, self.name]]
        if self.kind == "package":
            return [{"pacman": ["pkexec", "pacman", "-Rns", "--noconfirm", self.name],
                     "apt": ["pkexec", "apt-get", "remove", "-y", "--autoremove", self.name],
                     "dnf": ["pkexec", "dnf", "remove", "-y", self.name],
                     "zypper": ["pkexec", "zypper", "--non-interactive", "remove", "--clean-deps",
                                self.name]}[self.manager]]
        return []


def _run(cmd) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def _flatpak_id(path: str) -> str:
    if "/flatpak/exports/" in path and path.endswith(".desktop"):
        return os.path.basename(path)[:-len(".desktop")]
    parts = path.split("/")
    for i, part in enumerate(parts[:-2]):
        if part == "flatpak" and parts[i + 1] == "app":
            return parts[i + 2]
    return ""


def owner(info) -> Optional[Owner]:
    did = info.get_id() or ""
    if did.startswith(apps.PROTECTED):
        return None                                   # Files, Settings, Launchpad...: part of Sonata
    listed = apps.app_filename(info)                  # (GioUnix binds get_filename unbound)
    if not listed:
        return None
    path = os.path.realpath(listed)
    # Flatpak: exports/share/applications/<id>.desktop is a link into
    # flatpak/app/<id>/current/active/export/... -- either spelling counts
    fp = _flatpak_id(listed) or _flatpak_id(path)
    if fp:
        user = os.path.realpath(listed).startswith(os.path.realpath(GLib.get_user_data_dir())) or \
            listed.startswith(GLib.get_user_data_dir())
        return Owner("flatpak", fp, "flatpak-user" if user else "flatpak-system")
    for manager, cmd in (("pacman", ["pacman", "-Qqo", path]), ("apt", ["dpkg-query", "-S", path]),
                         ("dnf", ["rpm", "-qf", "--qf", "%{NAME}", path]), ("zypper", ["rpm", "-qf", "--qf", "%{NAME}", path])):
        if manager == "zypper" and not shutil.which("zypper"):
            continue
        if manager == "dnf" and not shutil.which("dnf"):
            continue
        out = _run(cmd)
        if out:
            name = out.split(":")[0].strip() if manager == "apt" else out.splitlines()[0].strip()
            return Owner("package", name, manager)
    if os.path.dirname(path) == os.path.realpath(os.path.join(GLib.get_user_data_dir(), "applications")):
        return Owner("shortcut", path)
    return None
