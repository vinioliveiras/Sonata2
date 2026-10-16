"""Vini: the apps' names weren't translated -- Sonata's desktop entries had
only their English Name. Each is written with Name / Comment in pt_BR and
pt_PT too (apps.localize_desktop), from Sonata's catalogs.
xvfb-run -a python3 -m unittest tests.test_app_names_translated"""
import importlib
import os
import re
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from sonata2 import apps, i18n  # noqa: E402

FUNCS = [("sonata2.activity.window", "activity_desktop_file"), ("sonata2.terminal.window", "terminal_desktop_file"),
         ("sonata2.videos.window", "videos_desktop_file"), ("sonata2.feedback.window", "feedback_desktop_file"),
         ("sonata2.notes.window", "notes_desktop_file"), ("sonata2.shell.launchpad", "launchpad_desktop_file"),
         ("sonata2.shell.capture", "capture_desktop_file"), ("sonata2.files", "files_desktop_file"),
         ("sonata2.diskutil.window", "diskutil_desktop_file"), ("sonata2.clock.window", "clock_desktop_file"),
         ("sonata2.settings.app", "settings_desktop_file"), ("sonata2.textedit.window", "textedit_desktop_file"),
         ("sonata2.assistant.window", "assistant_desktop_file"),
         ("sonata2.calculator.window", "calculator_desktop_file"), ("sonata2.camera.window", "camera_desktop_file"),
         ("sonata2.preview.window", "preview_desktop_file"), ("sonata2.calendar.window", "calendar_desktop_file")]


def entries():
    """Every Sonata app's desktop entry text, as its module writes it."""
    texts = []
    with mock.patch.object(apps, "write_desktop_file", side_effect=lambda f, t: texts.append(t) or f):
        for mod, fn in FUNCS:
            try:
                getattr(importlib.import_module(mod), fn)("sonata2")
            except (ImportError, ValueError):          # (a library this machine lacks: GStreamer...)
                continue
    return texts


class AppNamesTest(unittest.TestCase):
    def test_every_name_and_comment_translated(self):
        texts = entries()
        self.assertGreater(len(texts), 10)
        for lang in i18n.SUPPORTED:
            cat = i18n._load(lang)
            missing = sorted({v for t in texts for v in re.findall(r"^(?:Name|Comment)=(.*)$", t, re.M)
                              if not cat.get(v)})
            self.assertEqual(missing, [], lang)

    def test_written_in_both_languages(self):
        out = apps.localize_desktop("[Desktop Entry]\nType=Application\nName=Files\nComment=Browse your files\n"
                                    "Exec=x\n\n[Desktop Action new-window]\nName=New Window\n")
        self.assertIn("Name[pt_BR]=Arquivos\n", out)
        self.assertIn("Name[pt_PT]=Ficheiros\n", out)
        self.assertIn("Comment[pt_BR]=", out)
        self.assertIn("Name[pt_PT]=Nova janela", out)                    # actions too
        self.assertIn("Exec=x\n", out)

    def test_web_apps_keep_their_own_name(self):
        text = "[Desktop Entry]\nType=Application\nName=Calendar\nX-Sonata-WebApp=w1\n"
        self.assertEqual(apps.localize_desktop(text), text)

    def test_gio_reads_it(self):
        from gi.repository import Gio, GLib
        d = tempfile.mkdtemp()
        path = os.path.join(d, "t.desktop")
        with open(path, "w") as f:
            f.write(apps.localize_desktop("[Desktop Entry]\nType=Application\nName=Files\nExec=true\n"))
        kf = GLib.KeyFile()
        kf.load_from_file(path, GLib.KeyFileFlags.KEEP_TRANSLATIONS)
        self.assertEqual(kf.get_locale_string("Desktop Entry", "Name", "pt_BR"), "Arquivos")
        self.assertEqual(kf.get_locale_string("Desktop Entry", "Name", "pt_PT"), "Ficheiros")
        self.assertIsNotNone(Gio.DesktopAppInfo.new_from_filename(path))


if __name__ == "__main__":
    unittest.main()
