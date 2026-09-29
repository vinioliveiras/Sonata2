"""Graphic equalizer per audio output (Settings > Sound).

Ten bands (32 Hz .. 16 kHz, +/-12 dB). Each output -- a port of a card
("Speakers", "Headphones") or a device of its own (HDMI, Bluetooth) --
keeps its own curve in equalizer.json, keyed like system.AudioDevice.key
("sink-name|port-name"), and the curve follows the output in use.

How: a PipeWire filter-chain per sink, as WirePlumber "smart filters"
(filter.smart, WirePlumber >= 0.5): WirePlumber puts the filter in front
of its sink by itself, the sink stays the default output and its volume
stays the hardware one. The chains run in their own `pipewire -c` process
(started by the menu bar's services, only in the Sonata session); band
gains change live with `pw-cli set-param`, no restart, no audio gap.

    eq = Equalizer(); eq.start()      # service (topbar process)
    equalizer.set_curve(key, gains)   # Settings: saves; the service applies
"""
import json
import os
import re
import shutil
import subprocess
import sys

from gi.repository import GLib

from .. import config

BANDS = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
LABELS = ["32", "64", "125", "250", "500", "1K", "2K", "4K", "8K", "16K"]
MAX_DB = 12.0
FLAT = [0.0] * len(BANDS)
# macOS Music's presets (the ones people know), dB per band
PRESETS = {
    "Flat": FLAT,
    "Acoustic": [5, 5, 4, 1, 2, 2, 3.5, 4, 3.5, 2],
    "Bass Booster": [5.5, 4.5, 3.5, 2.5, 1.5, 0, 0, 0, 0, 0],
    "Bass Reducer": [-5.5, -4.5, -3.5, -2.5, -1.5, 0, 0, 0, 0, 0],
    "Classical": [5, 4, 3.5, 3, -1.5, -1.5, 0, 2, 3, 4],
    "Dance": [3.5, 6.5, 5, 0, 2, 3.5, 5, 4.5, 3.5, 0],
    "Electronic": [4.5, 4, 1.5, 0, -2, 2, 1, 1.5, 4, 5],
    "Hip-Hop": [5, 4.5, 1.5, 3, -1, -1, 1.5, -0.5, 2, 3],
    "Jazz": [4, 3, 1.5, 2, -1.5, -1.5, 0, 1.5, 3, 3.5],
    "Loudness": [6, 4, 0, 0, -2, 0, -1, -5, 5, 1],
    "Lounge": [-3, -1.5, -0.5, 1.5, 4, 2.5, 0, -1.5, 2, 1],
    "Piano": [3, 2, 0, 2.5, 3, 1.5, 3.5, 4.5, 3, 3.5],
    "Pop": [-1.5, -1, 0, 2, 4, 4, 2, 0, -1, -1.5],
    "R&B": [2.5, 7, 5.5, 1.5, -2.5, -1.5, 2.5, 3, 3, 4],
    "Rock": [5, 4, 3, 1.5, -0.5, -1, 0.5, 2.5, 3.5, 4.5],
    "Small Speakers": [5.5, 4, 3.5, 2.5, 1, 0, -1, -2, -3, -4],
    "Spoken Word": [-3.5, -0.5, 0, 0.5, 3.5, 4.5, 4.5, 4, 2, 0],
    "Treble Booster": [0, 0, 0, 0, 0, 1, 2.5, 3.5, 4.5, 5.5],
    "Treble Reducer": [0, 0, 0, 0, 0, -1, -2.5, -3.5, -4.5, -5.5],
    "Vocal Booster": [-1.5, -3, -3, 1.5, 3.5, 3.5, 3, 1.5, 0, -1.5],
}
DEFAULTS = {"outputs": {}}      # key -> {"on": bool, "preset": str, "gains": [10 floats]}
PREFIX = "sonata-eq"            # node names; system.py hides them from the output lists
RUN_CONF = os.path.join(GLib.get_user_runtime_dir() or "/tmp", "sonata2-equalizer.conf")
# the chain process's own output (errors loading the chain, WirePlumber links)
LOG = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "sonata2", "equalizer.log")


def _log(*a) -> None:
    print("equalizer:", *a, file=sys.stderr, flush=True)


# -- settings (Settings app) ----------------------------------------------------------
def curve(key: str) -> dict:
    out = config.load("equalizer", DEFAULTS)["outputs"].get(key) or {}
    gains = [float(g) for g in (out.get("gains") or FLAT)][:len(BANDS)]
    gains += [0.0] * (len(BANDS) - len(gains))
    return {"on": bool(out.get("on", False)), "preset": out.get("preset", "Flat"), "gains": gains}


def set_curve(key: str, on: bool = None, preset: str = None, gains=None) -> None:
    cfg = config.load("equalizer", DEFAULTS)
    cur = curve(key)
    if on is not None:
        cur["on"] = bool(on)
    if preset is not None:
        cur["preset"] = preset
        if preset in PRESETS:
            cur["gains"] = [float(g) for g in PRESETS[preset]]
    if gains is not None:
        cur["gains"] = [max(-MAX_DB, min(MAX_DB, round(float(g), 1))) for g in gains]
        match = next((n for n, p in PRESETS.items() if [float(x) for x in p] == cur["gains"]), None)
        cur["preset"] = match or "Custom"
    cfg["outputs"][key] = cur
    config.save("equalizer", cfg)


def available() -> bool:
    return bool(shutil.which("pipewire") and shutil.which("pw-cli") and shutil.which("pactl"))


# -- the filter chains ---------------------------------------------------------------------
def _slug(sink: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", sink)


def _graph(gains) -> str:
    nodes, links = [], []
    for i, f in enumerate(BANDS):
        label = "bq_lowshelf" if i == 0 else "bq_highshelf" if i == len(BANDS) - 1 else "bq_peaking"
        q = 0.7 if label != "bq_peaking" else 1.1
        nodes.append(f'{{ type = builtin name = b{i} label = {label} '
                     f'control = {{ "Freq" = {float(f)} "Q" = {q} "Gain" = {float(gains[i]):.1f} }} }}')
        if i:
            links.append(f'{{ output = "b{i - 1}:Out" input = "b{i}:In" }}')
    return ("{ nodes = [ " + " ".join(nodes) + " ] links = [ " + " ".join(links) + " ] }")


def conf_text(sinks: dict) -> str:
    """pipewire -c file: one smart filter chain per sink name (-> its gains)."""
    mods = []
    for sink, gains in sinks.items():
        s = _slug(sink)
        mods.append(f"""  {{ name = libpipewire-module-filter-chain
    args = {{
      node.description = "Equalizer"
      media.name = "Equalizer"
      filter.graph = {_graph(gains)}
      audio.channels = 2
      audio.position = [ FL FR ]
      capture.props = {{
        node.name = "{PREFIX}.{s}"
        media.class = Audio/Sink
        filter.smart = true
        filter.smart.name = "{PREFIX}.{s}"
        filter.smart.target = {{ node.name = "{sink}" }}
      }}
      playback.props = {{
        node.name = "{PREFIX}-out.{s}"
        node.passive = true
      }}
    }}
  }}""")
    return ("context.properties = { log.level = 2 }\n"
            "context.spa-libs = { audio.convert.* = audioconvert/libspa-audioconvert "
            "support.* = support/libspa-support }\n"
            "context.modules = [\n"
            "  { name = libpipewire-module-rt flags = [ ifexists nofail ] }\n"
            "  { name = libpipewire-module-protocol-native }\n"
            "  { name = libpipewire-module-client-node }\n"
            "  { name = libpipewire-module-adapter }\n"
            + "\n".join(mods) + "\n]\n")


def _run(cmd, timeout=4):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if p.returncode:
            _log(" ".join(cmd[:3]), "->", p.returncode, (p.stderr or "").strip()[:300])
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _sinks():
    """[(sink name, active port name)] of the real outputs."""
    rc, out = _run(["pactl", "-f", "json", "list", "sinks"])
    try:
        nodes = json.loads(out) if rc == 0 else []
    except ValueError:
        nodes = []
    return [(n.get("name", ""), n.get("active_port") or "") for n in nodes
            if n.get("name") and not n["name"].startswith(PREFIX)]


class Equalizer:
    """The service: keeps a chain in front of every sink with an output
    whose equalizer is on, and each chain on the active port's curve."""

    def __init__(self):
        self.proc = None
        self.chains = ()              # sink names the running process has
        self._events = None
        self._src = 0
        self._cfg_mon = None

    def start(self) -> None:
        if not available():
            return
        self._cfg_mon = config.watch("equalizer", self.sync_soon)
        self._subscribe()
        self.sync()

    def _subscribe(self) -> None:
        """pactl subscribe: a sink appeared/changed (port, plugged headphones)."""
        try:
            self._events = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, text=True,
                                            stderr=subprocess.DEVNULL)
        except OSError:
            return
        ch = GLib.IOChannel.unix_new(self._events.stdout.fileno())

        def line(_ch, cond):
            if cond & (GLib.IO_HUP | GLib.IO_ERR):
                return False
            text = self._events.stdout.readline()
            if " sink " in text or "'server'" in text or " card " in text:
                self.sync_soon()
            elif "'new' on sink-input" in text and self.chains:
                self._links_soon()           # an app started playing: does it go through us?
            return True
        GLib.io_add_watch(ch, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, line)

    def _links_soon(self) -> None:
        if not getattr(self, "_links_src", 0):
            def run():
                self._links_src = 0
                _log_links()
                return False
            self._links_src = GLib.timeout_add(1500, run)

    def sync_soon(self, *_a) -> None:
        if self._src:
            GLib.source_remove(self._src)
        self._src = GLib.timeout_add(250, self._sync_once)

    def _sync_once(self) -> bool:
        self._src = 0
        self.sync()
        return False

    def sync(self) -> None:
        sinks = _sinks()
        want = tuple(sorted(s for s, port in sinks if curve(f"{s}|{port}")["on"]))
        _log("outputs", [f"{s}|{p}" for s, p in sinks], "on:", list(want))
        if want != self.chains or (want and (self.proc is None or self.proc.poll() is not None)):
            self._restart({s: curve(f"{s}|{port}")["gains"] for s, port in sinks if s in want})
            GLib.timeout_add(700, lambda: (self._apply(sinks), False)[1])   # nodes need a moment
            GLib.timeout_add(3000, lambda: (_log_links(), False)[1])
            return
        self._apply(sinks)

    def _restart(self, sinks) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc, self.chains = None, tuple(sorted(sinks))
        if not sinks:
            return
        with open(RUN_CONF, "w", encoding="utf-8") as f:
            f.write(conf_text(sinks))
        try:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "w", encoding="utf-8") as log:
                self.proc = subprocess.Popen(["pipewire", "-c", RUN_CONF], stdout=log, stderr=log,
                                             start_new_session=True)
            _log("chain process", self.proc.pid, "for", list(sinks))
        except OSError as e:
            _log("can't start pipewire:", e)
            self.proc = None

    def _apply(self, sinks) -> None:
        ids = _node_ids()
        for sink, port in sinks:
            nid = ids.get(f"{PREFIX}.{_slug(sink)}")
            if nid is None:
                _log("no filter node for", sink, "(chain process",
                     "exited)" if self.proc is None or self.proc.poll() is not None else "running)")
                continue
            gains = curve(f"{sink}|{port}")["gains"]
            params = " ".join(f'"b{i}:Gain" {g:.1f}' for i, g in enumerate(gains))
            _run(["pw-cli", "set-param", str(nid), "Props", "{ params = [ " + params + " ] }"])

    def stop(self) -> None:
        self._restart({})
        if self._events and self._events.poll() is None:
            self._events.terminate()


def _node_ids() -> dict:
    """node.name -> id of our filter nodes (pw-dump)."""
    rc, out = _run(["pw-dump", "-N"], timeout=5)
    try:
        objs = json.loads(out) if rc == 0 else []
    except ValueError:
        return {}
    ids = {}
    for o in objs:
        props = ((o.get("info") or {}).get("props") or {})
        name = props.get("node.name", "")
        if o.get("type", "").endswith("Node") and name.startswith(PREFIX + "."):
            ids[name] = o["id"]
    return ids


def _log_links() -> None:
    """What WirePlumber linked to the chains (the log shows whether the
    sound really goes through the equalizer)."""
    _rc, ver = _run(["wireplumber", "--version"])
    _log("wireplumber", " ".join(ver.split())[:80])
    _rc, out = _run(["pw-link", "-l"])
    lines, keep = [], 0
    for ln in out.splitlines():
        if PREFIX in ln:
            keep = 3
        if keep:
            lines.append(ln.strip())
            keep -= 1
    _log("links:", " / ".join(lines) or "none")
