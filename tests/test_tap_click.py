"""Vini: in games the touchpad moved the pointer but a tap never clicked
(a USB mouse did). A tap's press and release come together and games read
the button once a frame -- sonata-corners holds a release that comes less
than MIN_CLICK_MS after its press (short_click_stretch_t); real clicks are
untouched, a new press lets a held release through first."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()

HARNESS = r"""
#include <cstdint>
#include <cstdio>
%s
int main()
{
    int fails = 0;
#define CHECK(c) if (!(c)) { std::printf("FAIL line %%d\n", __LINE__); fails++; }
    CHECK(click_hold_ms(1000, 1000) == MIN_CLICK_MS);       // a tap: press and release together
    CHECK(click_hold_ms(1000, 1020) == MIN_CLICK_MS - 20);
    CHECK(click_hold_ms(1000, 1000 + MIN_CLICK_MS) == 0);   // long enough: now
    CHECK(click_hold_ms(1000, 1150) == 0);                  // a real click
    CHECK(click_hold_ms(0xFFFFFFF0u, 5) == MIN_CLICK_MS - 21);   // the clock wrapped
    return fails;
}
"""


class TapClickTest(unittest.TestCase):
    def test_hold(self):
        cxx = shutil.which("g++") or shutil.which("c++")
        if not cxx:
            self.skipTest("no C++ compiler")
        start, end = SRC.index("static constexpr uint32_t MIN_CLICK_MS"), SRC.index("struct short_click_stretch_t")
        d = tempfile.mkdtemp()
        src, exe = os.path.join(d, "t.cpp"), os.path.join(d, "t")
        with open(src, "w") as f:
            f.write(HARNESS % SRC[start:end])
        subprocess.run([cxx, "-std=c++17", "-o", exe, src], check=True)
        r = subprocess.run([exe], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_wired(self):
        part = SRC[SRC.index("struct short_click_stretch_t"):SRC.index("class sonata_corners_t")]
        self.assertIn("sig->mode = wf::input_event_processing_mode_t::IGNORE", part)   # held back
        self.assertIn("wl_signal_emit_mutable(&key.first->events.button, &ev)", part)  # then let through
        self.assertIn("release(key);                                         /* a held one first */", part)
        self.assertIn("on_destroy.connect(&ptr->base.events.destroy)", part)          # unplugged meanwhile
        self.assertIn("short_clicks.init();", SRC)
        self.assertIn("short_clicks.fini();", SRC)
        self.assertIn("burst", SRC[SRC.index("#define SONATA_CORNERS_BUILD"):][:120])

    def test_burst(self):
        """Vini's log: a game kept the compositor busy, input came in bursts --
        a 100 ms mouse click went out as press + release together. The hold
        also counts from when the press was handed on (Wayfire's clock)."""
        part = SRC[SRC.index("struct short_click_stretch_t"):SRC.index("class sonata_corners_t")]
        self.assertIn("sent_at[key]    = wf::get_current_time();", part)
        self.assertIn("click_hold_ms(sent_at[key], wf::get_current_time())", part)
        self.assertIn("sent_at.erase(it)", part)

    def test_debug_log(self):
        """[sonata-corners] debug_input: each button, its device and the window that gets it."""
        part = SRC[SRC.index("struct short_click_stretch_t"):SRC.index("class sonata_corners_t")]
        self.assertIn('option_str("sonata-corners/debug_input") != "true"', part)
        self.assertIn("ev->pointer->base.name", part)
        self.assertIn("post_input_event_signal<wlr_pointer_button_event>", part)
        self.assertIn("keyboard_focus_changed_signal", part)                 # games ignore unfocused clicks
        self.assertIn("seat->get_active_view()", part)
        self.assertIn("node->stringify()", part)                            # a frame over the app took it?
        meta = (ROOT / "wayfire-plugin" / "metadata" / "sonata-corners.xml").read_text()
        self.assertIn('<option name="debug_input" type="bool">', meta)


if __name__ == "__main__":
    unittest.main()
