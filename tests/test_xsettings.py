"""Vini: Electron apps' menu bars were light in Dark Mode. An app on X11
(XWayland) takes its theme, icons, cursor and fonts from the display's
XSETTINGS manager, and Sonata's session had none: Adwaita, light. Sonata's
manager (sonata2/xsettings.py) publishes its settings, live.
python3 -m unittest tests.test_xsettings (the GTK 3 one needs xvfb-run)"""
import os
import pathlib
import struct
import subprocess
import sys
import tempfile
import unittest

from sonata2 import xsettings as X

ROOT = pathlib.Path(__file__).resolve().parent.parent


def decode(data: bytes) -> dict:
    """The property back into {name: value} (the XSETTINGS spec)."""
    order, serial, n = struct.unpack_from("<B3xII", data, 0)
    off, out = 12, {}
    for _ in range(n):
        kind, ln = struct.unpack_from("<BxH", data, off)
        off += 4
        name = data[off:off + ln].decode()
        off += ln + X._pad(ln) + 4                     # (the setting's last-change serial)
        if kind == 0:
            out[name] = struct.unpack_from("<i", data, off)[0]
            off += 4
        else:
            (vl,) = struct.unpack_from("<I", data, off)
            off += 4
            out[name] = data[off:off + vl].decode()
            off += vl + X._pad(vl)
    return out


class EncodeTest(unittest.TestCase):
    def test_round_trip(self):
        s = {"Net/ThemeName": "Sonata-Dark", "Xft/DPI": 98304, "Gtk/CursorThemeSize": 24, "Net/X": "abc"}
        data = X.encode(s, 7)
        self.assertEqual(data[0], 0)                                    # little endian
        self.assertEqual(struct.unpack_from("<I", data, 4)[0], 7)
        self.assertEqual(decode(data), s)
        self.assertEqual(len(data) % 4, 0)


class ValuesTest(unittest.TestCase):
    def prefs(self, **kw):
        base = {"color-scheme": "prefer-dark", "gtk-theme": "Sonata-Dark", "icon-theme": "Sonata",
                "cursor-theme": "Sonata-Cursors", "cursor-size": "32", "font-name": "Inter 10",
                "text-scaling-factor": "1.25", "font-antialiasing": "rgba", "font-hinting": "full",
                "button-layout": "close,minimize,maximize:"}
        base.update(kw)
        return lambda schema, key, *a, **k: base.get(key, "")

    def test_from_sonatas_settings(self):
        v = X.values(self.prefs())
        self.assertEqual(v["Net/ThemeName"], "Sonata-Dark")
        self.assertEqual(v["Gtk/ApplicationPreferDarkTheme"], 1)
        self.assertEqual(v["Net/IconThemeName"], "Sonata")
        self.assertEqual(v["Gtk/CursorThemeSize"], 32)
        self.assertEqual(v["Xft/DPI"], 96 * 1024 * 1.25)
        self.assertEqual((v["Xft/RGBA"], v["Xft/HintStyle"]), ("rgb", "hintfull"))
        self.assertEqual(v["Gtk/DecorationLayout"], "close,minimize,maximize:")
        light = X.values(self.prefs(**{"color-scheme": "default", "gtk-theme": "Sonata-Light"}))
        self.assertEqual((light["Net/ThemeName"], light["Gtk/ApplicationPreferDarkTheme"]), ("Sonata-Light", 0))

    def test_started_with_xwayland(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "out.ini")
        subprocess.run(["bash", str(ROOT / "tools" / "wayfire-config.sh"), str(ROOT / "config" / "wayfire.ini"), out],
                       check=True, capture_output=True)
        text = open(out).read()
        core = text[text.index("[core]"):]
        core = core[:core.index("\n[", 1)]
        self.assertIn("xwayland_startup_script = env PYTHONPATH=", core)
        self.assertIn("python3 -m sonata2.xsettings", core)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs an X display (xvfb-run)")
class LiveTest(unittest.TestCase):
    def test_a_gtk3_app_follows_dark_mode(self):
        try:
            import gi
            gi.require_version("Gtk", "3.0")
        except (ImportError, ValueError):
            self.skipTest("no GTK 3")
        env = dict(os.environ, XDG_CONFIG_HOME=tempfile.mkdtemp(), PYTHONPATH=str(ROOT))
        py = sys.executable

        def set_dark(on):
            subprocess.run([py, "-c", "from sonata2 import prefs; prefs.set(prefs.I, 'color-scheme', %r); "
                            "prefs.set(prefs.I, 'gtk-theme', %r)" % ("prefer-dark" if on else "default",
                                                                    "Sonata-Dark" if on else "Sonata-Light")],
                           env=env, check=True)
        set_dark(True)
        mgr = subprocess.Popen([py, "-m", "sonata2.xsettings"], env=env)
        try:
            client = (
                "import gi, time, subprocess, sys\n"
                "gi.require_version('Gtk', '3.0')\n"
                "from gi.repository import Gtk, GLib\n"
                "s = Gtk.Settings.get_default()\n"
                "end = time.time() + 3\n"
                "while s.props.gtk_theme_name != 'Sonata-Dark' and time.time() < end:\n"
                "    GLib.MainContext.default().iteration(False); time.sleep(0.02)\n"
                "print(s.props.gtk_theme_name)\n"
                "sys.stdout.flush()\n"
                "sys.stdin.readline()\n"
                "end = time.time() + 3\n"
                "while s.props.gtk_theme_name != 'Sonata-Light' and time.time() < end:\n"
                "    GLib.MainContext.default().iteration(False); time.sleep(0.02)\n"
                "print(s.props.gtk_theme_name)\n")
            p = subprocess.Popen([py, "-c", client], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 text=True)
            self.assertEqual(p.stdout.readline().strip(), "Sonata-Dark")      # not GTK's light Adwaita
            set_dark(False)
            p.stdin.write("\n")
            p.stdin.flush()
            self.assertEqual(p.stdout.readline().strip(), "Sonata-Light")     # live
            p.wait(5)
        finally:
            mgr.terminate()
            mgr.wait(5)


if __name__ == "__main__":
    unittest.main()
