"""Vini: with two cards, Control Center's GPU, video memory and temperature
modules couldn't be told apart (the title gets cut): the maker is inside the graph."""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from sonata2.backend import stats as S  # noqa: E402
from sonata2.shell import statsui  # noqa: E402

Adw.init()


class MakerTagTest(unittest.TestCase):
    def test_each_card_named_in_its_graph(self):
        with mock.patch.dict(S.GPU_MAKERS, {"gpu_nvidia": "NVIDIA"}), \
                mock.patch.dict(S.VRAM_MAKERS_BY_KIND, {"vram_amd": "AMD"}), \
                mock.patch.dict(S.TEMP_MAKERS_BY_KIND, {"temp_nvidia": "NVIDIA"}), \
                mock.patch.dict(statsui.TITLES, {"gpu_nvidia": "GPU (NVIDIA)", "vram_amd": "Video Memory (AMD)",
                                                 "temp_nvidia": "GPU Temperature (NVIDIA)"}), \
                mock.patch.dict(statsui.ICONS, {k: "gpu-symbolic" for k in ("gpu_nvidia", "vram_amd", "temp_nvidia")}), \
                mock.patch.dict(statsui.SERIES, {k: (k,) for k in ("gpu_nvidia", "vram_amd", "temp_nvidia")}):
            for kind, maker in (("gpu_nvidia", "NVIDIA"), ("vram_amd", "AMD"), ("temp_nvidia", "NVIDIA")):
                m = statsui.module(kind)
                self.assertEqual(m.maker_tag.get_label(), maker)
                self.assertFalse(m.maker_tag.get_parent().get_can_target())   # clicks go through to the module
        self.assertIsNone(statsui.module("cpu").maker_tag)              # one of a kind: no tag



class FigureWholeTest(unittest.TestCase):
    def test_video_memory_title_covered_its_gb(self):
        """Vini, in the Game Bar: "Video …  … .5 GB" -- the title and the
        figure shared a narrow card and both were cut, the GB (what matters)
        losing its start. The figure stays whole; the title is cut instead."""
        from gi.repository import Gtk
        m = statsui.module("ram")
        stack, value, title = [m], None, None
        while stack:
            w = stack.pop()
            if w.has_css_class("cc-stat-value"):
                value = w
            elif w.has_css_class("panel-module-title"):
                title = w
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        value.set_label("9.5 / 16 GB")                      # (gib(): the longest it gets)
        title.set_label("Video Memory (AMD)")
        natural = value.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        head = value.get_parent()
        head.measure(Gtk.Orientation.HORIZONTAL, -1)
        head.allocate(natural + 60, 30, -1, None)             # room for the figure and little else
        self.assertGreaterEqual(value.get_width(), natural)
        self.assertLess(title.get_width(), title.measure(Gtk.Orientation.HORIZONTAL, -1)[1])


if __name__ == "__main__":
    unittest.main()
