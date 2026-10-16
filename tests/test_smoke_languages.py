"""Sonata's apps in every language they speak (Vini: "check for more errors
of that kind -- my guess is it came with the translation"). Each language
runs in its own processes (the language is read once per process):

- every Sonata app opens and runs a few seconds without a Python error, in
  English, Portuguese (Brazil) and Portuguese (Portugal) -- and Portuguese
  never fails where English doesn't;
- Settings: every section opens, is rebuilt in place (what adding a user
  does) and shows its own content, its title in the language, nothing left
  on top of it.

    dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_smoke_languages
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGS = ("en_US.UTF-8", "pt_BR.UTF-8", "pt_PT.UTF-8")
# Sonata's apps (`sonata2 <name>`); the shell's parts need a Wayland session
APPS = ("settings", "files", "calculator", "notes", "calendar", "clock", "textedit", "preview", "activity",
        "diskutil", "feedback", "assistant")
RUN_S = 6


def env_for(lang: str) -> dict:
    env = dict(os.environ, LANG=lang, LC_ALL="", LANGUAGE="", LC_MESSAGES="", GDK_BACKEND="x11", PYTHONPATH=ROOT)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        env[var] = tempfile.mkdtemp()
    return env


def tracebacks(text: str) -> list:
    """The last line (the error) of each Python traceback in `text`."""
    out, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("Traceback (most recent call last)"):
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or not lines[j].strip()):
                j += 1
            out.append(lines[j].strip() if j < len(lines) else "?")
    return out


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class AppsStartTest(unittest.TestCase):
    def run_all(self, lang: str) -> dict:
        """{app: [errors]} -- every app at once, each in its own process."""
        procs = {}
        for app in APPS:
            procs[app] = subprocess.Popen([sys.executable, "-m", "sonata2", app], cwd=ROOT, env=env_for(lang),
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        time.sleep(RUN_S)
        out = {}
        for app, p in procs.items():
            p.terminate()
            try:
                text = p.communicate(timeout=10)[0]
            except subprocess.TimeoutExpired:
                p.kill()
                text = p.communicate()[0]
            out[app] = tracebacks(text)
        return out

    def test_every_app_in_every_language(self):
        results = {lang: self.run_all(lang) for lang in LANGS}
        for lang, per_app in results.items():
            for app, errors in per_app.items():
                self.assertEqual(errors, [], f"{app} in {lang}")
        for lang in LANGS[1:]:                                         # Portuguese: never worse than English
            for app in APPS:
                extra = [e for e in results[lang][app] if e not in results[LANGS[0]][app]]
                self.assertEqual(extra, [], f"{app}: only in {lang}")


SETTINGS_WALK = r"""
import json, sys, traceback
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw, GLib
errors = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception_only(t, v)).strip())
from sonata2 import i18n, ui
from sonata2.settings import app as S
Adw.init(); ui.setup()

def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)

win = S.Settings(None)
win.set_default_size(980, 680)
win.present()
settle(300)
problems = []
for sid, title, *_ in S.SECTIONS:
    try:
        win.select(sid)
        settle(120)
        win._reload_page(sid)                  # rebuilt in place (adding a user, a printer...)
        settle(450)
    except Exception:
        errors.append(sid + ": " + traceback.format_exc().strip().splitlines()[-1])
        continue
    if win.current != sid:
        problems.append(f"{sid}: shows {win.current}")
    elif win.content.get_visible_child() is not win.pages[sid]:
        problems.append(f"{sid}: another page in front")
    elif win.pages[sid].title.get_label() != title:
        problems.append(f"{sid}: title {win.pages[sid].title.get_label()!r}, not {title!r}")
    if win.fade._pic is not None:
        problems.append(f"{sid}: a picture left on top")
win.destroy()
print(json.dumps({"lang": i18n.language(), "errors": errors, "problems": problems}))
"""


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class SettingsWalkTest(unittest.TestCase):
    def walk(self, lang: str) -> dict:
        p = subprocess.run([sys.executable, "-c", SETTINGS_WALK], cwd=ROOT, env=env_for(lang),
                           capture_output=True, text=True, timeout=300)
        last = [ln for ln in p.stdout.splitlines() if ln.startswith("{")]
        self.assertTrue(last, f"{lang}: no result\n{p.stderr[-2000:]}")
        return json.loads(last[-1])

    def test_every_section_in_every_language(self):
        for lang, code in zip(LANGS, ("en", "pt_BR", "pt_PT")):
            r = self.walk(lang)
            self.assertEqual(r["lang"], code)
            self.assertEqual(r["errors"], [], lang)
            self.assertEqual(r["problems"], [], lang)


@unittest.skipUnless(shutil.which("xvfb-run") or os.environ.get("DISPLAY"), "needs a display")
class TracebackParseTest(unittest.TestCase):
    def test_parse(self):
        text = "x\nTraceback (most recent call last):\n  File \"a.py\", line 1\n    boom()\nKeyError: 'n'\ny"
        self.assertEqual(tracebacks(text), ["KeyError: 'n'"])


if __name__ == "__main__":
    unittest.main()
