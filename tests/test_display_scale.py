"""Vini: sizes look right on a 1080p monitor; 4K was tiny. A display
nobody picked a scale for gets one from its DPI (or its resolution), kept
up to date until one is picked. xvfb-run -a python3 -m unittest tests.test_display_scale"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402,F401

from sonata2 import config  # noqa: E402
from sonata2.shell import monitors  # noqa: E402

A = monitors.auto_scale


def settle(ms=500):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class AutoScaleTest(unittest.TestCase):
    def test_by_dpi(self):
        self.assertEqual(A(1920, 1080, 531, 299), 1.0)          # 24" 1080p: the reference
        self.assertEqual(A(3840, 2160, 597, 336), 1.75)         # 27" 4K
        self.assertEqual(A(3840, 2160, 708, 398), 1.5)          # 32" 4K
        self.assertEqual(A(3840, 2160, 527, 296), 2.0)          # 24" 4K
        self.assertEqual(A(2560, 1440, 597, 336), 1.25)         # 27" 1440p
        self.assertEqual(A(1920, 1080, 598, 336), 1.0)          # 27" 1080p: never under 1
        self.assertEqual(A(1920, 1080, 309, 174, builtin=True), 1.25)    # 14" laptop, seen closer
        self.assertEqual(A(2560, 1600, 286, 179, builtin=True), 1.75)    # 13" 2560x1600 laptop (227 DPI)
        self.assertEqual(A(1366, 768, 344, 194, builtin=True), 1.0)

    def test_made_up_sizes_use_the_resolution(self):
        for mm in ((0, 0), (160, 90), (1600, 900), (500, 500)):       # none, cm as mm, a TV, wrong shape
            self.assertIsNone(monitors.physical_dpi(3840, 2160, *mm), mm)
            self.assertEqual(A(3840, 2160, *mm), 2.0, mm)
        self.assertEqual(A(1366, 768), 1.0)                     # small: never under 1
        self.assertEqual(A(1080, 1920), 1.0)                    # portrait 1080p
        self.assertEqual(A(7680, 4320), 3.0)

    def test_settings_offers_every_auto_scale(self):
        import re
        from sonata2.settings import app
        with open(app.__file__) as f:
            src = f.read()
        offered = {float(x) for x in re.findall(r"\((\d\.\d+), \"\d+ %\"\)", src)}
        steps = {A(h * 16 // 9, h, mm * 16 // 9, mm) for h in range(600, 4400, 40) for mm in range(150, 800, 25)}
        self.assertLessEqual(steps, offered)




class FollowTest(unittest.TestCase):
    def _mons(self, *specs):
        out = []
        for c, w, h, wmm, hmm in specs:
            m = mock.Mock()
            m.get_connector.return_value = c
            r = Gdk.Rectangle()
            r.width, r.height = w, h
            m.get_geometry.return_value = r
            m.get_scale.return_value = 1.0
            m.get_width_mm.return_value, m.get_height_mm.return_value = wmm, hmm
            out.append(m)
        return out

    def _run(self, saved, mons):
        from sonata2.backend import system
        sets = []

        def wset(sec, key, v):
            sets.append((sec, key, v))
            saved.setdefault(sec, {})[key] = str(v)
        with mock.patch.object(monitors, "_list", return_value=mons), \
                mock.patch.object(system, "wayfire_get", lambda sec, key, d="": saved.get(sec, {}).get(key, d)), \
                mock.patch.object(system, "wayfire_set", wset), \
                mock.patch.object(Gdk.Display.get_default().get_monitors(), "connect"):
            monitors.ensure_refresh()
            settle(600)
        return [x for x in sets if x[1] == "scale"]

    def test_set_and_followed_until_picked(self):
        config.save("display-scale", {"auto": {}})
        saved = {"output:HDMI-A-1": {"mode": "highrr"}, "output:DP-1": {"mode": "highrr", "scale": "1.5"}}
        sets = self._run(saved, self._mons(("HDMI-A-1", 3840, 2160, 597, 336), ("DP-1", 1920, 1080, 531, 299)))
        self.assertEqual(sets, [("output:HDMI-A-1", "scale", 1.75)])     # DP-1: the user's 150 % stays
        # the resolution goes down to 1080p on the same monitor: the automatic scale follows
        sets = self._run(saved, self._mons(("HDMI-A-1", 1920, 1080, 597, 336)))
        self.assertEqual(sets, [("output:HDMI-A-1", "scale", 1.0)])
        # picked in Settings: never changed again
        saved["output:HDMI-A-1"]["scale"] = "1.25"
        self.assertEqual(self._run(saved, self._mons(("HDMI-A-1", 3840, 2160, 597, 336))), [])


if __name__ == "__main__":
    unittest.main()
