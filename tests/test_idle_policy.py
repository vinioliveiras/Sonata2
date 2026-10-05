"""Vini: Chrome kept the session awake for hours -- the screen never went
off and it never locked. Now only media playing keeps it awake: Sonata
watches input idle itself (ignoring apps' inhibitors) and turns the
displays and the keyboard's light off, then locks."""
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from gi.repository import GLib

from sonata2.shell import idlepolicy as P


class FakeWatch:
    def __init__(self):
        self.watches = {}

    def watch(self, s, on_idle, on_back):
        self.watches[1] = (s, on_idle, on_back)
        return 1

    def unwatch(self, oid):
        self.watches.pop(oid, None)


class PolicyTest(unittest.TestCase):
    def make(self, playing=False):
        self.events, self.timers = [], []
        self.w = FakeWatch()
        self.state = {"playing": playing}
        pol = P.IdlePolicy(self.w, lambda on: self.events.append(("dark", on)), lambda: self.events.append("lock"),
                           playing=lambda: self.state["playing"],
                           later=lambda s, f: (self.timers.append((s, f)), len(self.timers))[1],
                           cancel=lambda t: None)
        return pol

    def test_dark_then_lock_whatever_apps_ask(self):
        pol = self.make()
        pol.apply(120, 0)
        s, idle, back = self.w.watches[1]
        self.assertEqual(s, 120)
        idle()
        self.assertEqual(self.events, [("dark", True), "lock"])
        back()
        self.assertEqual(self.events[-1], ("dark", False))

    def test_lock_some_seconds_later(self):
        pol = self.make()
        pol.apply(120, 60)
        self.w.watches[1][1]()
        self.assertEqual(self.events, [("dark", True)])
        self.assertEqual(self.timers[0][0], 60)
        self.timers[0][1]()
        self.assertEqual(self.events[-1], "lock")

    def test_media_playing_keeps_it_awake(self):
        pol = self.make(playing=True)
        pol.apply(120, 0)
        self.w.watches[1][1]()
        self.assertEqual(self.events, [])                       # playing: nothing
        self.assertEqual(self.timers[0][0], P.RECHECK_S)
        self.state["playing"] = False                           # it stopped, still nobody there
        self.timers[0][1]()
        self.assertEqual(self.events, [("dark", True), "lock"])

    def test_never(self):
        pol = self.make()
        pol.apply(0, -1)
        self.assertEqual(self.w.watches, {})


class SoundTest(unittest.TestCase):
    def run_pactl(self, streams):
        res = subprocess.CompletedProcess([], 0, stdout=json.dumps(streams))
        with mock.patch.object(P.shutil, "which", return_value="/usr/bin/pactl"), \
                mock.patch.object(P.subprocess, "run", return_value=res):
            return P.sound_playing()

    def test_streams(self):
        self.assertTrue(self.run_pactl([{"corked": False, "mute": False,
                                         "properties": {"application.name": "Google Chrome"}}]))
        self.assertFalse(self.run_pactl([{"corked": True, "mute": False, "properties": {}}]))
        self.assertFalse(self.run_pactl([{"corked": False, "mute": False,
                                          "properties": {"application.name": "sonata2-topbar"}}]))
        self.assertFalse(self.run_pactl([]))


@unittest.skipUnless(shutil.which("sway"), "needs sway (headless) to test the Wayland client")
class IdleWatchTest(unittest.TestCase):
    def test_against_a_compositor(self):
        from sonata2.wl.idlewatch import IdleWatch
        run = tempfile.mkdtemp()
        os.chmod(run, 0o700)
        cfg = os.path.join(run, "cfg")
        with open(cfg, "w") as f:
            f.write("output HEADLESS-1 resolution 640x480\n")
        env = dict(os.environ, XDG_RUNTIME_DIR=run, WLR_BACKENDS="headless", WLR_LIBINPUT_NO_DEVICES="1",
                   WLR_RENDERER="pixman", WAYLAND_DISPLAY="")
        sway = subprocess.Popen(["sway", "-c", cfg], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(sway.kill)
        end = time.time() + 5
        while time.time() < end and not any(n.startswith("wayland-") and not n.endswith(".lock")
                                            for n in os.listdir(run)):
            time.sleep(0.1)
        name = next(n for n in os.listdir(run) if n.startswith("wayland-") and not n.endswith(".lock"))
        w = IdleWatch(os.path.join(run, name))
        self.assertTrue(w.ok)
        self.assertTrue(w.can_power())
        ev = []
        w.watch(0.5, lambda: ev.append("idle"), lambda: ev.append("back"))
        self.assertTrue(w.displays(False))
        end = time.time() + 4
        while time.time() < end and not ev:
            GLib.MainContext.default().iteration(False)
            time.sleep(0.02)
        self.assertEqual(ev, ["idle"])
        w.close()


if __name__ == "__main__":
    unittest.main()
