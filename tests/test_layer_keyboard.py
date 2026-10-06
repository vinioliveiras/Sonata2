"""Vini: a new desktop folder's name had to be clicked before typing. The
desktop is a layer surface under the windows, and Wayfire gives "exclusive"
keyboard only to the top layers: Sonata asks sonata-corners for it."""
import pathlib
import unittest
from types import SimpleNamespace
from unittest import mock

from gi.repository import GLib

from sonata2.shell import layer

ROOT = pathlib.Path(__file__).resolve().parent.parent


def fake_ls(layer_name):
    Layer = SimpleNamespace(BACKGROUND="bg", BOTTOM="bottom", TOP="top", OVERLAY="overlay")
    Mode = SimpleNamespace(EXCLUSIVE="exclusive", ON_DEMAND="on_demand", NONE="none")
    mon = SimpleNamespace(get_connector=lambda: "eDP-1")
    return SimpleNamespace(Layer=Layer, KeyboardMode=Mode, is_layer_window=lambda w: True,
                           set_keyboard_mode=mock.Mock(), get_layer=lambda w: getattr(Layer, layer_name),
                           get_monitor=lambda w: mon, get_namespace=lambda w: "sonata2-wallpaper")


def spin(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class TakeKeyboardTest(unittest.TestCase):
    def take(self, layer_name, on=True):
        LS = fake_ls(layer_name)
        win = mock.Mock()
        with mock.patch.object(layer, "layer_shell", return_value=LS), \
                mock.patch.object(layer, "focus_layer") as focus:
            self.assertTrue(layer.take_keyboard(win, on, rest="on_demand"))
            spin(layer.FOCUS_AFTER_MS + 80)
        return LS, win, focus

    def test_under_the_windows_asks_the_compositor(self):
        LS, win, focus = self.take("BACKGROUND")
        LS.set_keyboard_mode.assert_called_once_with(win, "exclusive")
        win.queue_draw.assert_called()                       # the mode goes out with a frame
        focus.assert_called_once_with({"namespace": "sonata2-wallpaper", "output": "eDP-1"})

    def test_top_layers_and_giving_back_need_nothing(self):
        _LS, _win, focus = self.take("OVERLAY")
        focus.assert_not_called()                            # Wayfire focuses those itself
        LS, win, focus = self.take("BACKGROUND", on=False)
        LS.set_keyboard_mode.assert_called_once_with(win, "on_demand")
        focus.assert_not_called()

    def test_the_plugin_hands_it_over(self):
        cpp = (ROOT / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()
        self.assertIn('register_method("sonata/focus-layer", ipc_focus_layer)', cpp)
        self.assertIn('unregister_method("sonata/focus-layer")', cpp)
        body = cpp[cpp.index("ipc_focus_layer = "):cpp.index("  public:", cpp.index("ipc_focus_layer = "))]
        self.assertIn("VIEW_ROLE_DESKTOP_ENVIRONMENT", body)           # only layer surfaces, never apps
        self.assertIn("v->get_app_id() != ns", body)
        self.assertIn("seat->focus_view(v)", body)

    def test_focus_layer_reads_the_reply(self):
        with mock.patch("sonata2.wl.wfipc.WayfireIPC") as ipc:
            ipc.return_value.call.return_value = {"result": "ok", "id": 3}
            self.assertTrue(layer.focus_layer({"namespace": "x"}))
            ipc.return_value.call.assert_called_once_with("sonata/focus-layer", {"namespace": "x"})
            ipc.return_value.call.return_value = None
            self.assertFalse(layer.focus_layer({"namespace": "x"}))


if __name__ == "__main__":
    unittest.main()
