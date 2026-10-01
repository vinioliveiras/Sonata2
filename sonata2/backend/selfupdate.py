"""Sonata's own updates, from its GitHub releases.

    rel = latest_release()             # {"tag", "name", "body", "url", "tarball"} or None (offline...)
    newer(rel["tag"], __version__)     # True: an update
    sonata2 self-update --check        # "Sonata 0.1.0-alpha -> 0.2.0" (Software Update's list)
    sonata2 self-update [TAG]          # download that release, run its install.sh, restart Sonata

Settings > About > Software Update lists it with the system's updates;
the menu bar (UpdateNotifier) checks now and then and posts a
notification once per new release. A clone installed with --dev updates
with `git pull` instead (the code runs from it)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

from .. import __version__, config

REPO = "vinioliveiras/sonata2"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
NAME = "selfupdate"
DEFAULTS = {"notified": "", "last_check": 0}
PRE = {"dev": 0, "alpha": 1, "a": 1, "beta": 2, "b": 2, "rc": 3}        # a release without a tag: 4


def parse_version(tag: str) -> tuple:
    """'v0.2.0-beta.1' -> (0, 2, 0, 2, 1): numbers, then the pre-release kind
    and its number (a final release sorts after its pre-releases)."""
    t = (tag or "").strip().lstrip("vV")
    main, _, pre = t.partition("-")
    nums = [int(n) for n in re.findall(r"\d+", main)[:3]]
    nums += [0] * (3 - len(nums))
    if not pre:
        return (*nums, 4, 0)
    m = re.match(r"([a-zA-Z]+)\.?(\d*)", pre)
    kind = PRE.get(m.group(1).lower(), 0) if m else 0
    return (*nums, kind, int(m.group(2)) if m and m.group(2) else 0)


def newer(tag: str, current: str = __version__) -> bool:
    return parse_version(tag) > parse_version(current)


def pick(releases) -> dict:
    """The newest published release (drafts left out; pre-releases count:
    Sonata is in alpha)."""
    best = None
    for r in releases if isinstance(releases, list) else []:
        if not isinstance(r, dict) or r.get("draft") or not r.get("tag_name"):
            continue
        if best is None or parse_version(r["tag_name"]) > parse_version(best["tag_name"]):
            best = r
    if best is None:
        return None
    return {"tag": best["tag_name"], "name": best.get("name") or best["tag_name"], "body": best.get("body") or "",
            "url": best.get("html_url") or f"https://github.com/{REPO}/releases",
            "tarball": best.get("tarball_url") or f"https://api.github.com/repos/{REPO}/tarball/{best['tag_name']}"}


def latest_release(timeout: float = 10) -> dict:
    """Ask GitHub (blocking: run it in a thread). None when it can't."""
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": f"sonata2/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return pick(json.loads(r.read().decode("utf-8")))
    except (OSError, ValueError):
        return None


def available(timeout: float = 10):
    """The newer release, None when up to date or unknown."""
    rel = latest_release(timeout)
    return rel if rel and newer(rel["tag"]) else None


# -- where Sonata runs from ---------------------------------------------------------------
def install_root() -> str:
    """The folder holding the sonata2 package (a clone, or <prefix>/share/sonata2)."""
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def is_clone(root: str = None) -> bool:
    return os.path.isdir(os.path.join(root or install_root(), ".git"))


def install_args(root: str = None) -> list:
    """install.sh's options for an update: the same kind of install, no
    questions, the login screen as it is (kept up to date when it's Sonata's)."""
    root = root or install_root()
    args = ["--yes"]
    if root.startswith("/usr/"):
        args.append("--system")
    if not os.path.exists("/usr/local/bin/sonata-greeter"):
        args.append("--no-greeter")          # never set up one the user didn't choose
    return args


def terminal_command(tag: str = "") -> str:
    """What Software Update runs in a terminal (asks for sudo there)."""
    from ..__main__ import self_command
    root = install_root()
    if is_clone(root):
        return f"git -C '{root}' pull --ff-only && {self_command()} restart"
    return f"{self_command()} self-update {tag}".strip()


# -- updating ------------------------------------------------------------------------------
def _cache() -> str:
    d = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2", "updates")
    os.makedirs(d, exist_ok=True)
    return d


def safe_members(tar: tarfile.TarFile, dest: str):
    """Only plain files and folders that stay inside `dest` (no links, no
    "../"): for Pythons without tarfile's "data" filter."""
    base = os.path.realpath(dest)
    for m in tar.getmembers():
        path = os.path.realpath(os.path.join(dest, m.name))
        if (m.isfile() or m.isdir()) and (path == base or path.startswith(base + os.sep)):
            yield m


def extract(tar: tarfile.TarFile, dest: str) -> None:
    if hasattr(tarfile, "data_filter"):
        tar.extractall(dest, filter="data")
    else:
        tar.extractall(dest, members=list(safe_members(tar, dest)))


def download(rel: dict, say=print) -> str:
    """The release's source in the cache; returns the folder with install.sh."""
    dest = os.path.join(_cache(), rel["tag"])
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest)
    archive = dest + ".tar.gz"
    say(f"Downloading Sonata {rel['tag']}…")
    req = urllib.request.Request(rel["tarball"], headers={"User-Agent": f"sonata2/{__version__}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(archive, "wb") as f:
        shutil.copyfileobj(r, f)
    with tarfile.open(archive) as t:
        extract(t, dest)
    os.remove(archive)
    top = [d for d in os.listdir(dest) if os.path.isdir(os.path.join(dest, d))]
    folder = os.path.join(dest, top[0]) if len(top) == 1 else dest
    if not os.path.exists(os.path.join(folder, "install.sh")):
        raise OSError("the release has no install.sh")
    return folder


def main(args: list) -> int:
    """`sonata2 self-update [--check] [TAG]`."""
    if args[:1] == ["--check"]:
        rel = available()
        if rel:
            print(f"Sonata {__version__} -> {rel['tag'].lstrip('vV')}")
        return 0
    rel = latest_release()
    if rel is None:
        print("Sonata: couldn't reach GitHub (offline?)")
        return 1
    tag = args[0] if args else rel["tag"]
    if tag != rel["tag"]:
        rel = dict(rel, tag=tag, tarball=f"https://api.github.com/repos/{REPO}/tarball/{tag}")
    if not args and not newer(tag):
        print(f"Sonata {__version__} is up to date.")
        return 0
    if is_clone():
        print("This Sonata runs from a git clone: update it with git pull.")
        return 2
    folder = download(rel)
    print(f"Installing Sonata {tag}…")
    code = subprocess.call(["bash", os.path.join(folder, "install.sh")] + install_args(), cwd=folder)
    if code != 0:
        print(f"The installer stopped (exit {code}). Sonata wasn't changed.")
        return code
    config.update(NAME, notified=tag)
    from ..__main__ import self_command
    subprocess.call(self_command().split() + ["restart"])
    print(f"Sonata {tag} is installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
