"""Vini: apps opened from Sonata sometimes stayed behind other windows --
launched with a plain Gio.AppLaunchContext, they got no xdg-activation
token (Wayfire: "token was rejected at creation"). Every launch (and every
link / file opened with its default app) gets the display's context."""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from sonata2 import apps  # noqa: E402


class LaunchFrontTest(unittest.TestCase):
    def test_display_context(self):
        Gtk.init()
        ctx = apps.launch_context()
        self.assertIsInstance(ctx, Gdk.AppLaunchContext)

    def test_no_display(self):
        with mock.patch.object(Gdk.Display, "get_default", return_value=None):
            self.assertNotIsInstance(apps.launch_context(), Gdk.AppLaunchContext)

    def test_launch_gets_it(self):
        seen = []
        info = mock.Mock(get_id=lambda: "t.desktop", get_commandline=lambda: "true")
        info._sonata_env_set = False
        with mock.patch.object(apps, "launch_context", return_value="CTX"), \
                mock.patch("sonata2.gpu.launch_env", side_effect=lambda *_a: seen.append("gpu") or {}), \
                mock.patch("sonata2.gpu.extra_env", return_value={}), \
                mock.patch("sonata2.gpu.extra_args", return_value=[]), \
                mock.patch("sonata2.gpu.launcher_args", return_value=[]), \
                mock.patch("sonata2.appscope.watch"), mock.patch("sonata2.shell.quitonclose.mark_launch"):
            wrapped = apps._gpu_aware(lambda self, arg, ctx, *r: seen.append(ctx))
            wrapped(info, [], None)
        self.assertIn("CTX", seen)

    def test_default_for_uri_patched(self):
        self.assertTrue(getattr(Gio.AppInfo.launch_default_for_uri, "_sonata_ctx", False))


if __name__ == "__main__":
    unittest.main()
