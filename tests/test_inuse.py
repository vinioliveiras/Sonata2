"""Vini (security review): no sign that the microphone or the camera was
recording (GNOME, KDE and macOS show one). The menu bar shows macOS' dots:
green for the camera, orange for the microphone; who, on hover."""
import os
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.backend import inuse as I  # noqa: E402


class MicTest(unittest.TestCase):
    def test_real_inputs_only(self):
        outs = [{"source_name": "alsa_input.pci-0000.analog-stereo", "properties": {"application.name": "Discord"}},
                {"source_name": "alsa_output.pci-0000.analog-stereo.monitor",              # screen recording sound
                 "properties": {"application.name": "wf-recorder"}},
                {"source_name": "alsa_input.x", "properties": {"application.name": "Settings",
                                                               "media.name": "Peak detect"}},   # a level meter
                {"source_name": "alsa_input.x", "properties": {"application.name": "Discord"}}]
        self.assertEqual(I.mic_users(outs), ["Discord"])
        self.assertEqual(I.mic_users([]), [])


class CameraTest(unittest.TestCase):
    def test_holders(self):
        root = tempfile.mkdtemp()
        dev = tempfile.mkdtemp()
        os.symlink("/dev/null", os.path.join(dev, "x"))

        def proc(pid, comm, target):
            d = os.path.join(root, str(pid), "fd")
            os.makedirs(d)
            os.symlink(target, os.path.join(d, "3"))
            with open(os.path.join(root, str(pid), "comm"), "w") as f:
                f.write(comm + "\n")
        proc(10, "pipewire", "/dev/video0")
        proc(11, "bash", "/dev/null")
        self.assertEqual(I.camera_users(root), ["An app"])       # only PipeWire: an app records through it
        proc(12, "obs", "/dev/video2")
        self.assertEqual(I.camera_users(root), ["obs"])
        self.assertEqual(I.camera_users(tempfile.mkdtemp()), [])


class BarDotsTest(unittest.TestCase):
    def test_dots(self):
        from sonata2.shell import topbar

        class W:
            state = {"mic": [], "camera": []}
            listeners = []
        b = topbar.Bar.__new__(topbar.Bar)
        b.inuse = W()
        b.inuse_btn, b.mic_dot, b.cam_dot = Gtk.Button(), Gtk.Box(), Gtk.Box()
        b._inuse_changed()
        self.assertFalse(b.inuse_btn.get_visible())
        W.state = {"mic": ["Discord"], "camera": []}
        b._inuse_changed()
        self.assertTrue(b.inuse_btn.get_visible() and b.mic_dot.get_visible())
        self.assertIn("Microphone: Discord", b.inuse_btn.get_tooltip_text())
        W.state = {"mic": ["Chrome"], "camera": ["Chrome"]}
        b._inuse_changed()
        self.assertTrue(b.cam_dot.get_visible())
        self.assertFalse(b.mic_dot.get_visible())                # the camera's dot covers both (macOS)

    @unittest.skipUnless(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"), "needs a display")
    def test_dot_moved_the_menu_bar_items(self):
        """Vini: the microphone / camera dot should be right of the date, as on
        macOS Tahoe, and moving nothing. It was an item of its own beside
        Control Center: shown, it pushed the clock and every item left of it.
        It sits over the bar's right margin now, every item where it was."""
        from gi.repository import GLib, Graphene
        from sonata2 import ui
        from sonata2.shell import topbar
        ui.setup()
        bar = topbar.Bar(None)
        try:
            w = 2400                                                     # one width, both times

            def layout():
                ctx = GLib.MainContext.default()
                for _i in range(3):                                      # (whatever fills in on its own)
                    while ctx.pending():
                        ctx.iteration(False)
                    bar.measure(Gtk.Orientation.HORIZONTAL, -1)
                    bar.allocate(w, 32, -1, None)
                out = {}
                for b in bar.items:
                    if b.get_visible():
                        ok, r = b.compute_bounds(bar)
                        out[id(b)] = (round(w - r.get_x()), round(r.get_width())) if ok else None
                return out, w
            bar.inuse.state = {"mic": [], "camera": []}
            bar._inuse_changed()
            layout()                                                     # (settled first)
            before, w = layout()
            bar.inuse.state = {"mic": ["Discord"], "camera": []}
            bar._inuse_changed()
            after, _w = layout()
            self.assertEqual(after, before)                              # nothing moved
            self.assertNotIn(bar.inuse_btn, bar.items)
            ok, dot = bar.mic_dot.compute_bounds(bar)
            ok2, clock = bar.clock.compute_bounds(bar)
            self.assertTrue(ok and ok2)
            ok3, text = bar.clock.get_child().get_last_child().compute_bounds(bar)
            text_end = text.get_x() + text.get_width()
            self.assertGreater(dot.get_width(), 0)
            self.assertGreaterEqual(dot.get_x(), text_end)                            # right of the date
            self.assertLessEqual(dot.get_x() + dot.get_width(), w)                    # still on the bar
            # Vini: centred between the end of the date and the screen's edge
            self.assertAlmostEqual(dot.get_x() + dot.get_width() / 2, (text_end + w) / 2, delta=1)
        finally:
            bar.stop()

    def test_watcher_reads_on_events_only(self):
        with mock.patch.object(I.shutil, "which", return_value=None), \
                mock.patch.object(I.GLib, "timeout_add_seconds"):
            w = I.Watcher()
        with mock.patch.object(w, "_read_mic") as rm:
            w._line("Event 'change' on sink-input #3")
            rm.assert_not_called()
            w._line("Event 'new' on source-output #7")
            rm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
