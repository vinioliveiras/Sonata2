"""The screenshot shortcuts in Mission Control (Vini: Super+Shift+3/4/5 and
Print Screen did nothing while Super+Tab's overview was open). Wayfire's
command plugin refuses its binding_ keys while a plugin holding the input
(scale) is open; always_binding_ ones run regardless. A config kept from
before has the old names: renamed at login, the user's keys kept, never
both names (Wayfire would bind the key twice).

    python3 -m unittest tests.test_screenshot_keys
"""
import configparser
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from sonata2 import shortcuts, wfconfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMES = ("screenshot", "screenshot_area", "capture", "capture_print")

KEPT = """[command]
binding_launchpad = <super> | KEY_F4
command_launchpad = sonata2 launchpad
# binding_screenshot = <super> KEY_X
binding_screenshot = <super> <shift> KEY_9
command_screenshot = sonata2 screenshot
binding_screenshot_area = <super> <shift> KEY_4
command_screenshot_area = sonata2 screenshot area
  binding_capture   =   <super> <shift> KEY_5
command_capture = sonata2 screenshot toolbar
binding_capture_print = KEY_PRINT
always_binding_capture_print = KEY_SYSRQ
command_capture_print = sonata2 screenshot edit
binding_draw = <super> <shift> KEY_D

[vswitch]
binding_screenshot = KEY_LEFT
"""


def _default_ini():
    with open(os.path.join(ROOT, "config", "wayfire.ini"), encoding="utf-8") as f:
        return f.read()


def _command(text):
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    cfg.read_string(text)
    return cfg


def _keys(text, section="command"):
    """Every key of [section] in order (repeats too, unlike configparser)."""
    out, cur = [], None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
        elif cur == section and "=" in s and not s.startswith("#"):
            out.append(s.split("=", 1)[0].strip())
    return out


def _heredocs():
    """tools/wayfire-config.sh's Python steps, as the login runs them."""
    with open(os.path.join(ROOT, "tools", "wayfire-config.sh"), encoding="utf-8") as f:
        script = f.read().splitlines()
    docs, cur = [], None
    for line in script:
        if cur is not None and line == "PY":
            docs.append("\n".join(cur) + "\n")
            cur = None
        elif cur is not None:
            cur.append(line)
        elif "<<'PY'" in line:
            cur = []
    return docs


class ScreenshotKeysTest(unittest.TestCase):
    def test_screenshot_keys_dead_in_mission_control(self):
        """Vini: the screenshot keys did nothing while Mission Control was
        open -- they're always_binding_ keys now, with their commands."""
        cmd = _command(_default_ini())["command"]
        for name in NAMES:
            self.assertIn(f"always_binding_{name}", cmd)
            self.assertNotIn(f"binding_{name}", cmd)
            self.assertTrue(cmd[f"command_{name}"].startswith("sonata2 screenshot"))
        self.assertIn("binding_draw", cmd)                      # not a capture: stays as it was
        by_id = {s.id: s for s in shortcuts.SHORTCUTS}
        for sid, name in (("shot", "screenshot"), ("shot_area", "screenshot_area"), ("capture", "capture"),
                          ("capture_print", "capture_print")):
            self.assertEqual(by_id[sid].key, f"always_binding_{name}")
            self.assertTrue(shortcuts.default(by_id[sid]))      # Settings still finds Sonata's own keys
        self.assertEqual(wfconfig.migrate_always_bindings(_default_ini()), _default_ini())   # nothing to move

    def test_kept_config_screenshot_keys_dead_in_mission_control(self):
        """Vini: a wayfire.ini or overrides kept from before still had the
        old binding_ names (dead in Mission Control): renamed, the user's
        keys kept, never both names."""
        new = wfconfig.migrate_always_bindings(KEPT)
        cmd = _command(new)["command"]
        self.assertEqual(cmd["always_binding_screenshot"], "<super> <shift> KEY_9")       # the user's own
        self.assertEqual(cmd["always_binding_screenshot_area"], "<super> <shift> KEY_4")
        self.assertIn("  always_binding_capture   =   <super> <shift> KEY_5\n", new)       # the line as it was
        self.assertEqual(cmd["always_binding_capture_print"], "KEY_SYSRQ")                # both: the new one stays
        keys = _keys(new)
        for name in NAMES:
            self.assertNotIn(f"binding_{name}", keys)
            self.assertEqual(keys.count(f"always_binding_{name}"), 1)
        self.assertIn("# binding_screenshot = <super> KEY_X\n", new)                      # comments untouched
        self.assertEqual(cmd["binding_launchpad"], "<super> | KEY_F4")
        self.assertEqual(cmd["binding_draw"], "<super> <shift> KEY_D")
        self.assertEqual(cmd["command_screenshot"], "sonata2 screenshot")
        self.assertEqual(_keys(new, "vswitch"), ["binding_screenshot"])                   # only [command]
        self.assertEqual(wfconfig.migrate_always_bindings(new), new)                      # once
        twice = "[command]\nbinding_screenshot = KEY_A\nbinding_screenshot = KEY_B\n"
        self.assertEqual(_keys(wfconfig.migrate_always_bindings(twice)), ["always_binding_screenshot"])

    def test_settings_wrote_screenshot_key_twice(self):
        """A file still holding the old name: Settings' change replaces it,
        not a second binding for the same shortcut (Wayfire would run it
        twice)."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "w.ini")
            with open(path, "w", encoding="utf-8") as f:
                f.write("[command]\nbinding_screenshot = KEY_A\ncommand_screenshot = sonata2 screenshot\n")
            with mock.patch.object(wfconfig, "_wayfire_files", return_value=[path]):
                wfconfig.wayfire_set("command", "always_binding_screenshot", "KEY_B")
            with open(path, encoding="utf-8") as f:
                text = f.read()
        self.assertEqual(_keys(text), ["command_screenshot", "always_binding_screenshot"])
        self.assertIn("always_binding_screenshot = KEY_B\n", text)

    def test_kept_overrides_screenshot_key_lost_at_login(self):
        """The login's steps (tools/wayfire-config.sh) on a kept config and
        overrides with the old names: the user's key wins, under the new
        name only, and the overrides file is renamed too (Settings reads
        always_binding_)."""
        docs = _heredocs()
        self.assertEqual(len(docs), 2)
        with tempfile.TemporaryDirectory() as d:
            out, over = os.path.join(d, "out.ini"), os.path.join(d, "over.ini")
            base = _default_ini().replace("always_binding_", "binding_")       # a wayfire.ini kept from before
            with open(out, "w", encoding="utf-8") as f:
                f.write(base)
            with open(over, "w", encoding="utf-8") as f:
                f.write("[command]\nbinding_screenshot = <super> KEY_F12\n")
            env = dict(os.environ, PYTHONPATH=ROOT, XDG_CONFIG_HOME=os.path.join(d, "cfg"),
                       XDG_RUNTIME_DIR=d)
            for doc, args in ((docs[0], [out]), (docs[1], [out, over])):
                r = subprocess.run([sys.executable, "-", *args], input=doc, text=True, env=env,
                                   capture_output=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            with open(out, encoding="utf-8") as f:
                text = f.read()
            with open(over, encoding="utf-8") as f:
                overrides = f.read()
        keys = _keys(text)
        for name in NAMES:
            self.assertEqual(keys.count(f"always_binding_{name}"), 1, name)
            self.assertNotIn(f"binding_{name}", keys)
        self.assertEqual(_command(text)["command"]["always_binding_screenshot"], "<super> KEY_F12")
        self.assertEqual(overrides, "[command]\nalways_binding_screenshot = <super> KEY_F12\n")


class LockedTest(unittest.TestCase):
    def test_screenshot_over_the_lock_screen(self):
        """always_binding_ keys run over the lock screen too: `sonata2
        screenshot` does nothing there (no capture of it, no editor behind)."""
        from sonata2 import __main__ as main
        from sonata2.shell import idlelock
        with mock.patch.object(idlelock, "is_locked", return_value=True), \
                mock.patch.object(main, "_topbar_action") as topbar:
            self.assertEqual(main.screenshot("edit"), 0)
        topbar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
