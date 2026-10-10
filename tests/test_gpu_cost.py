"""Sonata's own GPU cost (Wayfire can't run here: the source is checked).

- The corners transformer hid the windows' opaque regions from Wayfire:
  everything behind an opaque window (other windows, the wallpaper) was
  redrawn on every damage.
- Every window kept its corners buffer (~33 MB for a maximized 4K window)
  even minimized or on another workspace.
- Sharing one window rendered it again for every frame the client asked.
- The display capture rebuilt the whole scene's render instances per picture.
- The recording dot's smooth blink repainted the menu bar every frame."""
import os
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()


def body(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


class OcclusionTest(unittest.TestCase):
    def setUp(self):
        self.inst = body(SRC, "class corners_render_instance_t", "    void render(const wf::scene::render_instruction_t")

    def test_opaque_region_culled_after_the_instruction(self):
        sched = body(self.inst, "    void schedule_instructions(", "    void compute_visibility")
        self.assertIn("damage ^= self->get_opaque_region();", sched)
        self.assertLess(sched.index("instructions.push_back"), sched.index("damage ^= self->get_opaque_region()"))
        # the buffer-failure fallback stays as it was: the children cull themselves
        fallback = sched[sched.index("FAILED"):sched.index("return;")]
        self.assertIn("ch->schedule_instructions(instructions, target, damage)", fallback)
        self.assertNotIn("get_opaque_region", fallback)

    def test_visibility(self):
        vis = body(self.inst, "    void compute_visibility(", "\n    }\n")
        # children see the whole window: their damage keeps the buffer up to date
        self.assertIn("wf::regionf_t whole{self->get_children_bounding_box()};", vis)
        self.assertIn("compute_visibility_from_list(this->children, output, whole, {0, 0})", vis)
        # the windows below lose what this one covers, as Wayfire's surfaces do
        self.assertIn("options().opaque_visibility", vis)
        self.assertIn("visible ^= self->get_opaque_region();", vis)
        self.assertIn('"workarounds/enable_opaque_region_damage_optimizations") == "true"', SRC)

    def test_opaque_region_still_carves_corners_and_glass(self):
        opaque = body(SRC, "    wf::regionf_t get_opaque_region() const override", "    std::string stringify()")
        self.assertIn("region ^= wf::regionf_t{corner};", opaque)
        self.assertIn("TOP_GLASS", opaque)


class IdleBufferTest(unittest.TestCase):
    def test_hidden_windows_free_their_buffer(self):
        node = body(SRC, "class corners_node_t", "    wf::regionf_t get_opaque_region()")
        self.assertIn("wf::wl_timer<true> idle_check;", node)
        hidden = body(node, "    bool hidden() const", "\n    }\n")
        for cond in ("!view->is_mapped()", "view->minimized", "get_relative_geometry() & view->get_bounding_box()"):
            self.assertIn(cond, hidden)
        drawn = body(node, "    void note_drawn()", "\n    }\n\n")
        self.assertIn("last_drawn >= IDLE_FREE_MS) && hidden()", drawn)
        self.assertIn("release_buffers();", drawn)
        self.assertIn("cached_damage |= get_children_bounding_box();", drawn)
        sched = body(SRC, "    void schedule_instructions(", "    void render(const wf::scene::render_instruction_t")
        self.assertIn("self->note_drawn();", sched)
        # a freed buffer is reallocated whole on the next draw
        self.assertIn("self->cached_damage |= bbox", sched)


class WindowCaptureTest(unittest.TestCase):
    def test_paced_and_only_when_changed(self):
        req = body(SRC, "static void window_source_request_frame", "\n}\n")
        self.assertIn("1000000 / CAPTURE_MAX_FPS", req)
        self.assertIn("produce_if_changed()", req)
        self.assertNotIn("s->produce()", req)
        fn = body(SRC, "struct window_source_t", "static void window_source_start")
        self.assertIn("if (changed || !buffer.get_buffer())", fn)
        self.assertIn("[this] (auto) { changed = true; }, nullptr);", fn)
        start = body(SRC, "static void window_source_start", "\n}\n")
        self.assertIn("watch_damage(true)", start)
        stop = body(SRC, "static void window_source_stop", "\n}\n")
        self.assertIn("watch_damage(false)", stop)
        self.assertIn("paced.disconnect()", stop)
        # the constant is shared with the display capture, outside its #ifdef
        self.assertLess(SRC.index("static const int CAPTURE_MAX_FPS = 60;"), SRC.index("/* ---- Screen capture without Sonata"))


class OutputCaptureInstancesTest(unittest.TestCase):
    def test_instances_kept_between_pictures(self):
        produce = body(SRC, "    /* the display's picture without the hidden surfaces", "    void frame_event")
        self.assertNotIn("scene()->gen_render_instances", produce)
        self.assertIn("render_instance_manager_t", produce)
        self.assertIn("&scene_instances->get_instances()", produce)
        self.assertIn("s->scene_instances.reset();", body(SRC, "static void output_source_stop", "\n}\n"))
        self.assertIn("scene_instances.reset();", body(SRC, "on_output_removed =", "};"))


class BuildTest(unittest.TestCase):
    def test_build_bumped(self):
        self.assertNotIn('#define SONATA_CORNERS_BUILD "2026-10-09.1 ', SRC)   # bumped since


class RecDotTest(unittest.TestCase):
    def test_two_step_blink(self):
        css = (ROOT / "sonata2" / "shell" / "capture.py").read_text()
        self.assertIn("animation: rec-blink 1.4s steps(1) infinite;", css)
        self.assertIn("@keyframes rec-blink { 50%% { opacity: 0.3; } }", css)
        self.assertNotIn("rec-blink 1.4s ease-in-out", css)

    def test_gtk_parses_it(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        errors = []
        p = Gtk.CssProvider()
        p.connect("parsing-error", lambda _p, _s, e: errors.append(e.message))
        p.load_from_string(".rec-dot { animation: rec-blink 1.4s steps(1) infinite; }"
                           " @keyframes rec-blink { 50% { opacity: 0.3; } }")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
