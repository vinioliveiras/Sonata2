"""Settings smoke test (xvfb-run python3 -m unittest tests.test_settings): every
section builds; Sonata settings are written to ~/.config/sonata2."""
import json
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.settings import app as S  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class SettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def test_all_sections(self):
        win = S.Settings(None)
        win.present()
        for sid, *_ in S.SECTIONS:
            win.select(sid)
            settle(150)
            self.assertIn(sid, win.pages)
        win.destroy()

    def test_menu_bar_text_row(self):
        """Menu bar logo > Text: custom… shows the text field; saving writes menu_text."""
        win = S.Settings(None)
        win.present()
        win.select("menubar")                 # (it moved from General to Menu Bar)
        settle(150)
        found, stack = [], [win]
        while stack:
            w = stack.pop()
            if isinstance(w, Adw.EntryRow) and w.get_title() == "Menu bar text":
                found.append(w)
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.assertEqual(len(found), 1)
        row = found[0]
        self.assertTrue(row.get_visible())                         # never shown / hidden: the rows under it
        self.assertFalse(row.get_sensitive())                      # don't move (a click hit the next switch)
        stack, combo = [win], None
        while stack and combo is None:
            w = stack.pop()
            if isinstance(w, Adw.ComboRow) and "text:custom" in getattr(w, "values", []):
                combo = w
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        combo.set_selected(combo.values.index("text:custom"))
        self.assertTrue(row.get_sensitive())
        row.grab_focus()
        row.set_text("Vini 🎮")
        settle(100)
        stack = [row]                                              # its Apply button, clicked
        apply_btn = None
        while stack and apply_btn is None:
            w = stack.pop()
            if isinstance(w, Gtk.Button) and "suggested-action" in w.get_css_classes():
                apply_btn = w
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.assertIsNotNone(apply_btn)
        apply_btn.grab_focus()
        apply_btn.emit("clicked")
        settle(200)
        self.assertEqual(config.load("appearance", {"menu_text": ""})["menu_text"], "Vini 🎮")
        combo.set_selected(0)                                      # another logo: the field stays put
        self.assertTrue(row.get_visible())
        self.assertFalse(row.get_sensitive())
        self.assertTrue(config.load("appearance", {"system_titlebars": True})["system_titlebars"])
        win.destroy()

    def test_titlebars_on_for_new_installs(self):
        """No appearance.json yet (a new install): Sonata title bars for all apps is on."""
        from sonata2 import titlebars
        path = os.path.join(config.CONFIG_DIR, "appearance.json")
        if os.path.exists(path):
            os.remove(path)
        self.assertTrue(titlebars.enabled())
        config.save("appearance", {"theme": "mac"})              # older files without the key: on too
        self.assertTrue(titlebars.enabled())

    def test_keyring_switch_offered_for_keepassxc(self):
        """KeePassXC asked for its password at every login: Security & Privacy offers the move."""
        config.save("keyring", {"backend": "keepassxc"})
        win = S.Settings(None)
        win.present()
        win.select("privacy")
        settle(150)
        found, stack = [], [win]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Button) and w.get_label() == "Use Login Password…":
                found.append(w)
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.assertEqual(len(found), 1)
        win.destroy()
        config.save("keyring", {"backend": "gnome"})

    def test_restart_now_or_later(self):
        """Settings that need a restart ask: Later / Restart Sonata, Log Out, Restart."""
        from unittest import mock
        win = S.Settings(None)
        from sonata2.shell import quitapps
        for kind, owner, action in (("sonata", S.system, "restart_sonata"),
                                    ("session", quitapps, "end_session"),     # the apps quit first
                                    ("system", quitapps, "end_session")):
            with mock.patch("sonata2.ui.dialog.alert") as alert, \
                    mock.patch.object(owner, action) as act:
                win.ask_restart(kind, "It")
                heading, body, responses, answered = alert.call_args[0][:4]
                self.assertEqual([r[0] for r in responses], ["later", "now"])
                answered("later")
                act.assert_not_called()
                answered("now")
                act.assert_called_once()
                if action == "end_session":
                    self.assertEqual(act.call_args[0][0], "logout" if kind == "session" else "restart")
        src = open(S.__file__).read()
        for setting in ('"renderer", v)', '"icon_theme", v)', "set_compositor_on_display_gpu(on)",
                        "logs.set_verbose(on)"):
            i = src.index(setting)
            self.assertIn("ask_restart(", src[i:i + 300], setting)
        win.destroy()

    def test_reset_appearance(self):
        """Vini: one button puts Appearance back to the theme's defaults (asked
        first); other settings stay."""
        from unittest import mock
        from sonata2 import icons
        config.save("appearance", {"accent": "#ff00aa", "glass": {"dock": {"on": False}, "blur": 90},
                                   "radius": {"window": 3}, "glass_titlebars": True, "system_titlebars": False,
                                   "menu_text": "Vini", "screen_corners": False})
        win = S.Settings(None, "appearance")
        win.present()
        win.select("appearance", from_sidebar=True)
        settle(150)
        before = win.pages["appearance"]
        with mock.patch("sonata2.ui.dialog.alert") as alert, mock.patch.object(S.system, "run_async") as run:
            win.ask_reset_appearance()
            answered = alert.call_args[0][3]
            answered("cancel")
            self.assertEqual(config.load("appearance", icons.APPEARANCE_DEFAULTS)["accent"], "#ff00aa")      # nothing before yes
            answered("reset")
            run.assert_called()
        a = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        for k in S.Settings.APPEARANCE_RESET:
            self.assertEqual(a[k], icons.APPEARANCE_DEFAULTS[k], k)
        self.assertEqual((a["menu_text"], a["screen_corners"]), ("Vini", False))   # not on this page: kept
        settle(150)
        self.assertIsNot(win.pages["appearance"], before)                          # shown as it is now
        self.assertIs(win.content.get_visible_child(), win.pages["appearance"])
        win.destroy()

    def test_reset_dock(self):
        """Vini: Desktop & Dock back to the defaults; the Dock's apps and folders stay."""
        from unittest import mock
        from sonata2 import icons
        from sonata2.shell import dock as D, topbar as T
        pins = ["org.gnome.Nautilus.desktop", "folder:f1"]
        folders = {"f1": {"name": "Work", "apps": ["a.desktop", "b.desktop"]}}
        config.save("dock", {"icon_size": 90, "position": "left", "autohide": True, "pinned": pins,
                             "folders": folders, "minimize_effect": "scale"})
        config.save("topbar", {"battery_percent": True, "clock_format": "%H:%M"})
        config.update("appearance", menu_text="Vini", menu_logo="text", accent="pink")
        win = S.Settings(None, "dock")
        win.present()
        win.select("dock", from_sidebar=True)
        settle(150)
        before = win.pages["dock"]
        with mock.patch("sonata2.ui.dialog.alert") as alert, mock.patch.object(S.system, "run_async") as run:
            win.ask_reset_dock()
            answered = alert.call_args[0][3]
            answered("cancel")
            self.assertEqual(config.load("dock", D.DEFAULTS)["icon_size"], 90)
            answered("reset")
            run.assert_called()
        d = config.load("dock", D.DEFAULTS)
        for k in S.Settings.DOCK_RESET:
            self.assertEqual(d[k], D.DEFAULTS[k], k)
        self.assertEqual((d["pinned"], d["folders"]), (pins, folders))           # the Dock's contents stay
        self.assertEqual(config.load("topbar", T.DEFAULTS), T.DEFAULTS)
        a = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        self.assertEqual((a["menu_text"], a["menu_logo"]), ("", icons.APPEARANCE_DEFAULTS["menu_logo"]))
        self.assertEqual(a["accent"], "pink")                                      # another section's
        settle(150)
        self.assertIsNot(win.pages["dock"], before)
        win.destroy()
        config.save("dock", {})
        config.save("topbar", {})
        config.save("appearance", {})

    def test_about_resets_ask_first(self):
        """Vini: About > Reset Settings / Reset Sonata, asked first, then log out."""
        from unittest import mock
        from sonata2 import factory_reset
        win = S.Settings(None, "about")
        win.select("about", from_sidebar=True)
        settle(100)
        for what, fn in (("settings", "settings_only"), ("everything", "everything")):
            with mock.patch("sonata2.ui.dialog.alert") as alert, \
                    mock.patch.object(factory_reset, fn, return_value=[]) as run, \
                    mock.patch.object(win, "ask_restart") as restart:
                win.ask_factory_reset(what)
                answered = alert.call_args[0][3]
                answered("cancel")
                run.assert_not_called()
                answered("reset")
                run.assert_called_once()
                self.assertEqual(restart.call_args[0][0], "session")
        win.destroy()

    def test_opens_on_general_with_the_shared_sidebar(self):
        """Settings opened on the pane left open last time: General, unless a pane is asked for.
        Every app's source list: one width (ui.window.SIDEBAR_W)."""
        win = S.Settings(None)
        self.assertEqual(win.current, "appearance")
        win.destroy()
        import re as _re
        src = open(os.path.join(os.path.dirname(S.__file__), "..", "__main__.py")).read()
        self.assertIn('DEFAULT_SETTINGS_PAGE = "appearance"', src)
        self.assertIn("win.select(DEFAULT_SETTINGS_PAGE)", src)
        self.assertEqual(ui.window.SIDEBAR_W, 280)
        root = os.path.join(os.path.dirname(S.__file__), "..")
        for f in ("settings/app.py", "files/window.py", "diskutil/window.py", "music/window.py",
                  "calendar/window.py", "notes/window.py", "activity/performance.py"):
            self.assertIn("SIDEBAR_W", open(os.path.join(root, f)).read(), f)
        self.assertFalse(_re.search(r"sidebar\.set_size_request\(\d", open(os.path.join(root, "diskutil/window.py")).read()))

    def test_sections_merged_nothing_lost(self):
        """Vini: fewer sections (27 -> 19), nothing removed: every old section's
        builder still runs inside its new section, old ids still open it."""
        old_ids = ["wifi", "network", "bluetooth", "printers", "sound", "displays", "battery", "wallpaper",
                   "keyboard", "trackpad", "shortcuts", "mouse", "gamepad", "datetime", "notifications", "users",
                   "privacy", "sharing", "accessibility", "appearance", "dock", "menubar", "launchpad", "hidden",
                   "updates", "about"]
        ids = [x[0] for x in S.SECTIONS]
        self.assertEqual(len(ids), 21)                         # 19 + App Icons + Default Apps (new)
        built = [p for sid in ids for p in S.parts_of(sid)]
        self.assertEqual(sorted(built), sorted(old_ids + ["appicons", "defaults"]))      # every builder, once
        for old in old_ids:
            self.assertIn(S.section_of(old), ids, old)
            self.assertTrue(hasattr(S.Settings, f"_page_{old}"), old)
        self.assertEqual(S.section_of("wallpaper"), "displays")
        self.assertEqual(S.section_of("updates"), "about")
        self.assertIn("wallpaper", S.KEYWORDS["displays"].casefold())
        self.assertIn("topbar", S.PAGE_CONFIGS["dock"])                       # Menu Bar's file still watched
        win = S.Settings(None, "wallpaper")                                   # an old id (menu bar shortcuts)
        win.present()
        settle(200)
        self.assertEqual(win.current, "displays")
        self.assertIn("wallpaper", win.pages)
        for sid in ids:
            win.select(sid)
            settle(80)
            self.assertIn(sid, win.pages, sid)
        win._reload_page("menubar")                                          # a part reloads its section
        settle(80)
        win.select("hidden")
        settle(80)
        self.assertEqual(win.current, "launchpad")
        win.destroy()

    def test_save_keeps_other_keys(self):
        config.save("dock", {"pinned": ["a"], "icon_size": 48})
        win = S.Settings(None)
        win._save("dock", "icon_size", 64)
        with open(os.path.join(config.CONFIG_DIR, "dock.json")) as f:
            data = json.load(f)
        self.assertEqual(data, {"pinned": ["a"], "icon_size": 64})
        win.destroy()


if __name__ == "__main__":
    unittest.main()
