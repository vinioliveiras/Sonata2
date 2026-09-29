"""Interface sound effects (macOS "Play user interface sound effects"):
Move to Trash, Empty Trash, a copy/move finished, a screenshot, volume
feedback... Settings > Sound turns them off (sounds.json).

    sounds.play("trash")

The sounds are bundled (sonata2/data/sounds: freedesktop sound theme, see
CREDITS there) and played with the first player found: pw-play (PipeWire),
paplay (PulseAudio), canberra-gtk-play, ffplay. Non-blocking; silent when
nothing can play them."""
import os
import shutil
import subprocess
import time

from . import config

DIR = os.path.join(os.path.dirname(__file__), "data", "sounds")
DEFAULTS = {"effects": True, "volume_feedback": True}
EVENTS = {                           # event -> bundled file (without .oga)
    "trash": "trash-empty",          # Move to Trash
    "empty-trash": "trash-empty",
    "done": "complete",              # copy / move / duplicate finished
    "screenshot": "camera-shutter",
    "volume": "audio-volume-change",
    "error": "dialog-warning",
    "plug": "device-added",          # power adapter / device plugged in
    "unplug": "device-removed",
}
_last = {}


def enabled(event: str) -> bool:
    cfg = config.load("sounds", DEFAULTS)
    return bool(cfg["volume_feedback"] if event == "volume" else cfg["effects"])


def _player(path):
    for cmd in (["pw-play", path], ["paplay", path], ["canberra-gtk-play", "-f", path],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]):
        if shutil.which(cmd[0]):
            return cmd
    return None


def play(event: str) -> None:
    if not enabled(event):
        return
    now = time.monotonic()
    if now - _last.get(event, 0) < 0.12:          # a burst (20 files trashed) plays once
        return
    _last[event] = now
    path = os.path.join(DIR, EVENTS.get(event, event) + ".oga")
    cmd = _player(path) if os.path.exists(path) else None
    if cmd:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
