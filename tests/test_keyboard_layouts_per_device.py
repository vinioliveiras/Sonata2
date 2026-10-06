"""Vini: a layout per keyboard -- his USB keyboard is U.S. International,
the laptop's is Portuguese. Wayfire reads [input:<device name>] for a
keyboard instead of [input]; Sonata writes it (with the other keyboard
options, kept in step) and removes it to follow Input Sources again."""
import os
import tempfile
import unittest
from unittest import mock


class PerKeyboardTest(unittest.TestCase):
    def setUp(self):
        self.cfg, self.run = tempfile.mkdtemp(), tempfile.mkdtemp()
        os.makedirs(os.path.join(self.cfg, "sonata2"))
        with open(os.path.join(self.run, "sonata2-wayfire.ini"), "w") as f:
            f.write("[input]\nxkb_layout = pt\nkb_repeat_rate = 40\nxkb_options = caps:escape\n")
        p = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.cfg, "XDG_RUNTIME_DIR": self.run})
        p.start()
        self.addCleanup(p.stop)

    def ini(self):
        return open(os.path.join(self.run, "sonata2-wayfire.ini")).read()

    def test_own_layout_with_the_other_options_then_back(self):
        from sonata2.backend import system as S
        usb = "HyperX Alloy Origins 65"
        self.assertEqual(S.keyboard_device_layout(usb), "")
        self.assertTrue(S.set_keyboard_device_layout(usb, "us(intl)"))
        text = self.ini()
        self.assertIn(f"[input:{usb}]", text)
        self.assertEqual(S.keyboard_device_layout(usb), "us(intl)")
        self.assertIn("xkb_options = caps:escape", text.split(f"[input:{usb}]")[1])     # copied
        self.assertIn("kb_repeat_rate = 40", text.split(f"[input:{usb}]")[1])
        self.assertEqual(S.keyboard_sections(), [usb])
        S.sync_keyboard_option("kb_repeat_rate", 60)                         # Settings changed it
        self.assertIn("kb_repeat_rate = 60", self.ini().split(f"[input:{usb}]")[1])
        self.assertTrue(S.set_keyboard_device_layout(usb, ""))               # Same as Input Sources
        self.assertNotIn(f"[input:{usb}]", self.ini())                       # no empty section left
        self.assertIn("xkb_layout = pt", self.ini())                         # the laptop's untouched
        overrides = open(os.path.join(self.cfg, "sonata2", "wayfire-overrides.ini")).read()
        self.assertNotIn(f"[input:{usb}]", overrides)                        # (kept across logins too)

    def test_keyboards_listed_without_buttons(self):
        from sonata2.backend import system as S
        devs = [{"name": "AT Translated Set 2 keyboard", "type": "keyboard"},
                {"name": "HyperX Alloy Origins 65", "type": "keyboard"},
                {"name": "HyperX Alloy Origins 65 Consumer Control", "type": "keyboard"},
                {"name": "Power Button", "type": "keyboard"}, {"name": "Lid Switch", "type": "switch"},
                {"name": "ASUP1205:00 093A:2008 Touchpad", "type": "pointer"}]
        ipc = mock.Mock()
        ipc.return_value.call.return_value = devs
        with mock.patch("sonata2.wl.wfipc.WayfireIPC", ipc):
            self.assertEqual(S.keyboards(), ["AT Translated Set 2 keyboard", "HyperX Alloy Origins 65"])

    def test_kernel_list_when_wayfire_cant_tell(self):
        from sonata2.backend import system as S
        proc = ('I: Bus=0011\nN: Name="AT Translated Set 2 keyboard"\nB: EV=120013\n\n'
                'I: Bus=0019\nN: Name="Power Button"\nB: EV=3\n\n'
                'I: Bus=0003\nN: Name="HyperX Alloy Origins 65"\nB: EV=120013\n')
        ipc = mock.Mock()
        ipc.return_value.call.return_value = None
        real_open = open

        def fake_open(path, *a, **k):
            if path == "/proc/bus/input/devices":
                import io
                return io.StringIO(proc)
            return real_open(path, *a, **k)
        with mock.patch("sonata2.wl.wfipc.WayfireIPC", ipc), mock.patch("builtins.open", fake_open):
            self.assertEqual(S.keyboards(), ["AT Translated Set 2 keyboard", "HyperX Alloy Origins 65"])

    def test_settings_page_has_it(self):
        import inspect
        from sonata2.settings import app
        src = inspect.getsource(app.Settings._page_keyboard)
        self.assertIn("self._keyboards_group(names)", src)
        self.assertIn("system.sync_keyboard_option(k, v)", inspect.getsource(app.Settings._wf))


if __name__ == "__main__":
    unittest.main()
