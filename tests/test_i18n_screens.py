"""Nothing in English on screen when Sonata speaks Portuguese (Vini: "tests
that check every item has a translation"). tests.test_i18n checks the
strings marked for translation; this opens every Sonata app -- and every
Settings section -- in Portuguese (Brazil) and Portuguese (Portugal), walks
every widget and fails on a text still in English: an English string the
catalogs translate shown as it is (marked with N_() and never put through
_(), translated before the language was known, or not marked at all).

Left out: GTK's own words inside its drop-down lists (their search field:
GTK translates those itself), and file and folder names.

    dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_i18n_screens
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGS = ("pt_BR.UTF-8", "pt_PT.UTF-8")
APPS = ("settings", "files", "calculator", "notes", "calendar", "clock", "textedit", "preview", "activity",
        "diskutil", "feedback", "assistant")

# what a widget shows, by the getters that hold text
WALK = r'''
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
from sonata2 import i18n

GETTERS = ("get_label", "get_title", "get_subtitle", "get_tooltip_text", "get_placeholder_text",
           "get_description", "get_heading", "get_body")


def gtk_own(w):
    """Inside a GTK / libadwaita drop-down (its list's search field:
    GTK's own words, translated by GTK)."""
    while w is not None:
        if isinstance(w, (Gtk.DropDown, Adw.ComboRow)):
            return True
        w = w.get_parent()
    return False


def english(roots):
    cat = i18n._load(i18n.language())
    seen, bad = 0, set()

    def walk(w, path):
        nonlocal seen
        here = f"{path} > {type(w).__name__}"
        for g in GETTERS:
            try:
                v = getattr(w, g)() if hasattr(w, g) else None
            except Exception:
                v = None
            if isinstance(v, str) and v.strip():
                seen += 1
                t = cat.get(v)
                if isinstance(t, str) and t != v and not gtk_own(w):
                    bad.add(f"{v!r} ({here.split(' > ', 2)[-1][-90:]}.{g})")
        c = w.get_first_child()
        while c is not None:
            walk(c, here)
            c = c.get_next_sibling()
    for r in roots:
        t = r.get_title() if hasattr(r, "get_title") else None
        if isinstance(t, str) and isinstance(cat.get(t), str) and cat[t] != t:
            bad.add(f"{t!r} (window title)")
        walk(r, type(r).__name__)
    return seen, sorted(bad)
'''

APP_PROBE = WALK + r'''
import json, os, sys
from gi.repository import GLib


def report():
    seen, bad = english(Gtk.Window.list_toplevels())
    print("RESULT " + json.dumps({"lang": i18n.language(), "seen": seen, "english": bad}), flush=True)
    os._exit(0)


GLib.timeout_add(4000, lambda: (report(), False)[1])
sys.argv = ["sonata2", sys.argv[1]]
from sonata2.__main__ import main
main()
'''

SETTINGS_PROBE = WALK + r'''
import json
from gi.repository import GLib
from sonata2.settings import app as S


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


win = S.Settings(None)
win.set_default_size(980, 680)
win.present()
settle(300)
seen_all, bad_all = 0, []
for sid, *_ in S.SECTIONS:
    try:
        win.select(sid)
        settle(450)
    except Exception as e:
        bad_all.append(f"{sid}: {type(e).__name__}: {e}")
        continue
    seen, bad = english([win])
    seen_all += seen
    bad_all += [f"{sid}: {b}" for b in bad]
print("RESULT " + json.dumps({"lang": i18n.language(), "seen": seen_all, "english": sorted(set(bad_all))}),
      flush=True)
'''


def env_for(lang: str) -> dict:
    env = dict(os.environ, LANG=lang, LC_ALL="", LANGUAGE="", LC_MESSAGES="", GDK_BACKEND="x11", PYTHONPATH=ROOT)
    for var in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        env[var] = tempfile.mkdtemp()          # (an empty home: no folders named in English on disk)
    return env


def run(script: str, lang: str, *args) -> dict:
    p = subprocess.run([sys.executable, "-c", script] + list(args), cwd=ROOT, env=env_for(lang),
                       capture_output=True, text=True, timeout=180)
    lines = [ln[7:] for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    if not lines:
        raise AssertionError(f"{lang} {args}: no result\n{p.stderr[-2000:]}")
    return json.loads(lines[-1])


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class NoEnglishOnScreenTest(unittest.TestCase):
    def test_every_app(self):
        for lang in LANGS:
            for app in APPS:
                with self.subTest(lang=lang, app=app):
                    r = run(APP_PROBE, lang, app)
                    self.assertEqual(r["lang"], lang.split(".")[0])
                    self.assertGreater(r["seen"], 0)                       # (it did look)
                    self.assertEqual(r["english"], [])

    def test_every_settings_section(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                r = run(SETTINGS_PROBE, lang)
                self.assertGreater(r["seen"], 100)
                self.assertEqual(r["english"], [])

    def test_it_sees_english(self):
        """The probe itself: Calculator in English is its title in English."""
        r = run(APP_PROBE.replace("i18n._load(i18n.language())", 'i18n._load("pt_BR")'), "en_US.UTF-8",
                "calculator")
        self.assertIn("'Calculator' (window title)", r["english"])


if __name__ == "__main__":
    unittest.main()
