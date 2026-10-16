"""Vini: sizes look right at 1080p; 4K was tiny, small screens too big.
A display nobody picked a scale for gets one for its resolution.
xvfb-run -a python3 -m unittest tests.test_display_scale"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402,F401

from sonata2.shell import monitors  # noqa: E402


class AutoScaleTest(unittest.TestCase):
    def test_by_resolution(self):
        for (w, h), want in {(1920, 1080): 1.0, (3840, 2160): 2.0, (2560, 1440): 1.25, (1366, 768): 0.75,
                             (1600, 900): 0.75, (1920, 1200): 1.0, (2560, 1600): 1.5, (3440, 1440): 1.25,
                             (1080, 1920): 1.0, (5120, 2880): 2.75, (7680, 4320): 3.0, (800, 600): 0.75}.items():
            self.assertEqual(monitors.auto_scale(w, h), want, (w, h))

    def test_settings_offers_every_auto_scale(self):
        import re
        from sonata2.settings import app
        with open(app.__file__) as f:
            src = f.read()
        offered = {float(x) for x in re.findall(r"\((\d\.\d+), \"\d+ %\"\)", src)}
        steps = {monitors.auto_scale(h * 16 // 9, h) for h in range(600, 4400, 10)}
        self.assertLessEqual(steps, offered)

    def test_written_once_only_when_unset(self):
        saved = {"output:HDMI-A-1": {"mode": "highrr"}, "output:DP-1": {"mode": "highrr", "scale": "1.5"}}

        class Mon:
            def __init__(self, c, w, h):
                self.c, self.w, self.h = c, w, h

            def get_connector(self):
                return self.c

            def get_geometry(self):
                r = Gdk.Rectangle()
                r.width, r.height = self.w, self.h
                return r

            def get_scale(self):
                return 1.0
        sets = []
        from sonata2.backend import system
        with mock.patch.object(monitors, "_list", return_value=[Mon("HDMI-A-1", 3840, 2160), Mon("DP-1", 1920, 1080)]), \
                mock.patch.object(system, "wayfire_get", lambda sec, key, d="": saved.get(sec, {}).get(key, d)), \
                mock.patch.object(system, "wayfire_set", lambda sec, key, v: sets.append((sec, key, v))):
            monitors.ensure_refresh()
        self.assertEqual(sets, [("output:HDMI-A-1", "scale", 2.0)])      # DP-1: the user's 150 % stays


if __name__ == "__main__":
    unittest.main()
