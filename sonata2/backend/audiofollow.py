"""New headphones and headsets are used right away (Vini, like macOS): a
USB or Bluetooth device that appears becomes the default output -- and its
microphone the default input (a Bluetooth headset's too: WirePlumber keeps
the good-quality profile until something actually records, then switches
to the headset profile by itself). When it goes, the device used before
comes back.

Watched with `pactl subscribe` (PipeWire's pulse layer) in the menu bar's
process; on/off: sounds.json "follow_new_devices" (Settings > Sound).

    AudioFollow(run=..., defaults=...).event("Event 'new' on sink #57")
"""
import json
import re
import subprocess
import threading

EVENT = re.compile(r"Event '(new|remove|change)' on (sink|source) #(\d+)")
EXTERNAL_BUSES = ("bluetooth", "usb")


def _pactl(*args) -> str:
    try:
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def node_info(kind: str, index: int):
    """(name, properties) of sink/source #index, else None."""
    try:
        nodes = json.loads(_pactl("-f", "json", "list", kind + "s") or "[]")
    except ValueError:
        return None
    for n in nodes if isinstance(nodes, list) else []:
        if n.get("index") == index:
            return n.get("name", ""), n.get("properties") or {}
    return None


def external(name: str, props: dict) -> bool:
    """A device you plug in or pair (USB, Bluetooth) -- not the laptop's own
    card, a monitor of an output, or Sonata's equalizer."""
    if not name or name.endswith(".monitor") or name.startswith("sonata-eq"):
        return False
    bus = (props.get("device.bus") or "").lower()
    api = (props.get("device.api") or "").lower()
    return bus in EXTERNAL_BUSES or api == "bluez5" or name.startswith("bluez_")


class AudioFollow:
    def __init__(self, info=node_info, get_default=None, set_default=None, enabled=None, restore=None,
                 keep=None):
        self.info = info
        self.restore = restore or _restore_volume
        self.keep = keep or _keep_volume
        self.get_default = get_default or (lambda kind: _pactl("get-default-" + kind).strip())
        self.set_default = set_default or (lambda kind, name: _pactl("set-default-" + kind, name))
        self.enabled = enabled or _enabled
        self.ours = {}           # (kind, index) -> (name, the default before it)

    def event(self, line: str) -> None:
        m = EVENT.search(line or "")
        if not m:
            return
        what, kind, index = m.group(1), m.group(2), int(m.group(3))
        if what == "change":
            self.keep(kind, index)                    # an app turned it down? (devicevolume.py)
            return
        if what == "new":
            self.added(kind, index)
            found = self.info(kind, index)
            if found:
                self.restore(kind, found[0])          # its own volume again (devicevolume.py)
        else:
            self.removed(kind, index)

    def added(self, kind: str, index: int) -> None:
        if not self.enabled():
            return
        found = self.info(kind, index)
        if not found:
            return
        name, props = found
        if not external(name, props):
            return
        before = self.get_default(kind)
        if before == name:
            return
        self.ours[(kind, index)] = (name, before)
        self.set_default(kind, name)

    def removed(self, kind: str, index: int) -> None:
        entry = self.ours.pop((kind, index), None)
        if entry is None:
            return
        name, before = entry
        # one of ours used this one as its "before": it goes back further
        for key, (n, prev) in list(self.ours.items()):
            if key[0] == kind and prev == name:
                self.ours[key] = (n, before)
        # only if it was in use (you may have chosen another meanwhile)
        if before and self.enabled() and self.get_default(kind) in (name, ""):
            self.set_default(kind, before)        # (gone too: WirePlumber picks one itself)


def _restore_volume(kind: str, name: str) -> None:
    """In its own thread: it waits for WirePlumber to restore its own first."""
    from . import devicevolume
    if devicevolume.wanted(kind, name) is not None:
        threading.Thread(target=devicevolume.restore, args=(kind, name), daemon=True).start()


def _keep_volume(kind: str, index: int) -> None:
    from . import devicevolume
    devicevolume.keep(kind, index)


def _enabled() -> bool:
    from .. import config
    from ..sounds import DEFAULTS
    return bool(config.load("sounds", DEFAULTS).get("follow_new_devices", True))


def start():
    """Events from the shared `pactl subscribe` reader (pactl_watch: no pactl
    of its own any more, and it is restarted if pactl dies); None without
    pactl."""
    from . import pactl_watch, system
    follow = AudioFollow()
    lock = threading.Lock()                               # events one at a time, in order

    def handle(line):
        with lock:
            follow.event(line)

    def line(text):
        if EVENT.search(text):
            system.run_async(handle, None, text)          # pactl calls off the main loop
    follow.proc = pactl_watch.watch(line)
    return follow if follow.proc is not None else None
