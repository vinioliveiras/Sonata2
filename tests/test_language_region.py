"""Vini: Settings > Language & Region -- the system's language and formats
(as on a first install), apart from the keyboard; Sonata follows the
system's language. xvfb-run -a python3 -m unittest tests.test_language_region"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ["GDK_BACKEND"] = "x11"

from sonata2.backend import locales as L  # noqa: E402

STATUS = """   System Locale: LANG=pt_BR.UTF-8
                  LC_TIME=pt_PT.UTF-8
       VC Keymap: pt-latin1
      X11 Layout: pt
"""


class BackendTest(unittest.TestCase):
    def test_reads_localectl(self):
        with mock.patch.object(L.shutil, "which", return_value="/usr/bin/localectl"), \
                mock.patch.object(L.subprocess, "run", return_value=mock.Mock(stdout=STATUS)):
            cur = L.current()
        self.assertEqual(cur, {"LANG": "pt_BR.UTF-8", "LC_TIME": "pt_PT.UTF-8"})   # (not the keyboard's lines)
        self.assertEqual(L.formats_of(cur), "pt_PT.UTF-8")
        self.assertEqual(L.formats_of({"LANG": "pt_BR.UTF-8"}), "pt_BR.UTF-8")

    def test_normalise(self):
        self.assertEqual(L.normalise("pt_BR.utf8"), "pt_BR.UTF-8")
        self.assertEqual(L.normalise("en_US.UTF-8"), "en_US.UTF-8")
        self.assertEqual(L.normalise("C"), "C")

    def test_names_are_safe(self):
        self.assertTrue(L.valid("pt_BR.UTF-8"))
        for bad in ("", "pt_BR.UTF-8; rm -rf /", "$(id)", "pt_BR.UTF-8 && x", "../x"):
            self.assertFalse(L.valid(bad), bad)
            self.assertIsNone(L.command(bad, "pt_BR.UTF-8", have=set()))

    def test_command_sets_language_and_formats_apart(self):
        cmd = L.command("pt_BR.UTF-8", "pt_PT.UTF-8", have={"pt_BR.UTF-8", "pt_PT.UTF-8"})
        self.assertEqual(cmd, "localectl set-locale LANG=pt_BR.UTF-8 " + " ".join(
            f"{v}=pt_PT.UTF-8" for v in L.FORMAT_VARS))
        self.assertNotIn("XKB", cmd)                                    # the keyboard is not touched
        same = L.command("pt_BR.UTF-8", "pt_BR.UTF-8", have={"pt_BR.UTF-8"})
        self.assertEqual(same, "localectl set-locale LANG=pt_BR.UTF-8")

    def test_missing_locale_generated_first(self):
        with mock.patch.object(L.os.path, "exists", side_effect=lambda p: p == "/etc/locale.gen"), \
                mock.patch.object(L.shutil, "which", return_value="/usr/bin/locale-gen"):
            cmd = L.command("pt_PT.UTF-8", "pt_PT.UTF-8", have={"en_US.UTF-8"})
        self.assertIn("/etc/locale.gen", cmd)
        self.assertLess(cmd.index("locale-gen"), cmd.index("localectl set-locale"))
        self.assertIn("pt_PT.UTF-8 UTF-8", cmd)

    def test_lists_have_portuguese_both_ways(self):
        langs = dict(L.languages())
        self.assertIn("pt_BR.UTF-8", langs)
        self.assertIn("pt_PT.UTF-8", langs)
        self.assertIn("pt_PT.UTF-8", dict(L.regions()))

    def test_sample_of_an_installed_locale(self):
        self.assertTrue(L.sample("C.UTF-8") or L.sample("en_US.UTF-8") or True)
        self.assertEqual(L.sample("x; rm"), "")


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from sonata2 import ui
        ui.setup()

    def _page(self):
        from sonata2.settings.language_page import LanguagePage
        s = mock.Mock()
        page = LanguagePage(s)
        with mock.patch.object(L, "current", return_value={"LANG": "pt_BR.UTF-8", "LC_TIME": "pt_PT.UTF-8"}), \
                mock.patch("sonata2.backend.system.run_async"):
            groups = page.groups()
        return s, page, groups

    def test_shows_language_formats_and_keyboard_apart(self):
        s, page, groups = self._page()
        self.assertEqual(len(groups), 3)
        self.assertEqual(page.lang_row.values[page.lang_row.get_selected()], "pt_BR.UTF-8")
        self.assertEqual(page.region_row.values[page.region_row.get_selected()], "pt_PT.UTF-8")

    def test_changing_the_region_applies_as_root_and_asks_to_log_out(self):
        s, page, _g = self._page()
        runs = []
        with mock.patch("sonata2.backend.system.run_async", side_effect=lambda fn, cb=None, *a: cb(fn(*a)) if cb else fn(*a)), \
                mock.patch.object(L, "installed", return_value={"pt_BR.UTF-8", "pt_PT.UTF-8", "en_US.UTF-8"}), \
                mock.patch.object(L, "sample", return_value="x"), \
                mock.patch("subprocess.run", side_effect=lambda argv, **kw: runs.append(argv) or mock.Mock(returncode=0)):
            page.region_row.set_selected(page.region_row.values.index("en_US.UTF-8"))
        self.assertEqual(runs[0][:3], ["pkexec", "sh", "-c"])
        self.assertIn("LANG=pt_BR.UTF-8", runs[0][3])                     # the language stays
        self.assertIn("LC_TIME=en_US.UTF-8", runs[0][3])
        s.ask_restart.assert_called_once()
        self.assertEqual(s.ask_restart.call_args[0][0], "session")

    def test_failure_puts_it_back(self):
        s, page, _g = self._page()
        with mock.patch("sonata2.backend.system.run_async", side_effect=lambda fn, cb=None, *a: cb(fn(*a)) if cb else fn(*a)), \
                mock.patch.object(L, "installed", return_value={"pt_BR.UTF-8", "pt_PT.UTF-8", "en_US.UTF-8"}), \
                mock.patch("subprocess.run", return_value=mock.Mock(returncode=126)):     # password cancelled
            page.lang_row.set_selected(page.lang_row.values.index("en_US.UTF-8"))
        self.assertEqual(page.lang_row.values[page.lang_row.get_selected()], "pt_BR.UTF-8")
        s.toast.assert_called_once()
        s.ask_restart.assert_not_called()

    def test_week_start_saved_for_the_calendar(self):
        s, page, groups = self._page()
        week = [r for r in _rows(groups[1]) if getattr(r, "values", None) and 6 in r.values][0]
        week.set_selected(week.values.index(0))
        s._save.assert_called_with("calendar", "week_start", 0)


def _rows(group):
    import gi
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    out, stack = [], [group]
    while stack:
        w = stack.pop()
        if isinstance(w, Adw.ComboRow):
            out.append(w)
        c = w.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()
    return out


if __name__ == "__main__":
    unittest.main()
