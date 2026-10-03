"""Steam's window sometimes never showed (Vini): its web helper drawing on
the GPU under Xwayland with NVIDIA. Sonata starts it with -cef-disable-gpu.
Run: python3 -m unittest tests.test_steam_launch"""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio  # noqa: E402

from sonata2 import apps, gpu  # noqa: E402


class SteamArgsTest(unittest.TestCase):
    def info(self, did):
        i = mock.Mock()
        i.get_id.return_value = did
        return i

    def test_only_steam_with_nvidia(self):
        with mock.patch.object(gpu, "_cards", return_value=["amdgpu", "nvidia"]):
            self.assertEqual(gpu.extra_args(self.info("steam.desktop")), ["-cef-disable-gpu"])
            self.assertEqual(gpu.extra_args(self.info("com.valvesoftware.Steam.desktop")), ["-cef-disable-gpu"])
            self.assertEqual(gpu.extra_args(self.info("firefox.desktop")), [])
        with mock.patch.object(gpu, "_cards", return_value=["amdgpu"]):
            self.assertEqual(gpu.extra_args(self.info("steam.desktop")), [])

    def test_args_go_before_the_field_codes(self):
        self.assertEqual(gpu.with_args("/usr/bin/steam %U", ["-x"]), "/usr/bin/steam -x %U")
        self.assertEqual(gpu.with_args("flatpak run com.valvesoftware.Steam @@u %U @@", ["-x"]),
                         "flatpak run com.valvesoftware.Steam -x @@u %U @@")
        self.assertEqual(gpu.with_args("steam -silent", ["-x"]), "steam -silent -x")

    def test_launch_uses_the_extended_line(self):
        info = Gio.DesktopAppInfo.new_from_keyfile(self._keyfile())
        launched = []
        real = Gio.AppInfo.create_from_commandline

        def created(cmd, name, flags):
            launched.append(cmd)
            other = real("true", name, flags)
            return other
        with mock.patch.object(gpu, "_cards", return_value=["nvidia"]), \
                mock.patch.object(gpu, "wants_discrete", return_value=False), \
                mock.patch.object(Gio.AppInfo, "create_from_commandline", side_effect=created), \
                mock.patch.object(info, "get_id", return_value="steam.desktop"):
            info.launch([], None)
        self.assertEqual(launched, ["true -cef-disable-gpu %U"])

    @staticmethod
    def _keyfile():
        from gi.repository import GLib
        kf = GLib.KeyFile()
        text = "[Desktop Entry]\nType=Application\nName=Steam\nExec=true %U\n"
        kf.load_from_data(text, len(text), GLib.KeyFileFlags.NONE)
        return kf


if __name__ == "__main__":
    unittest.main()


class SteamKeepsTheGraphicsCardTest(unittest.TestCase):
    """Vini: with Everyday Apps on the Integrated Graphics on, Steam saw only
    the AMD GPU (its games too) -- the copy made to add its args had no
    desktop id and was taken for an everyday app."""

    def test_no_integrated_env_for_steam(self):
        from gi.repository import GLib
        text = "[Desktop Entry]\nType=Application\nName=Steam\nCategories=Network;FileTransfer;Game;\nExec=true %U\n"
        kf = GLib.KeyFile()
        kf.load_from_data(text, len(text), GLib.KeyFileFlags.NONE)
        info = Gio.DesktopAppInfo.new_from_keyfile(kf)
        envs = []
        real_setenv = Gio.AppLaunchContext.setenv

        def setenv(ctx, k, v):
            envs.append(k)
            return real_setenv(ctx, k, v)
        with mock.patch.object(gpu, "_cards", return_value=["amdgpu", "nvidia"]), \
                mock.patch.object(gpu, "smart", return_value=True), \
                mock.patch.object(gpu, "wants_discrete", return_value=False), \
                mock.patch.object(Gio.AppLaunchContext, "setenv", setenv), \
                mock.patch.object(info, "get_id", return_value="steam.desktop"):
            info.launch([], None)
        self.assertNotIn("__GLX_VENDOR_LIBRARY_NAME", envs)
        self.assertNotIn("VK_ICD_FILENAMES", envs)
