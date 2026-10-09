"""Review of sonata-corners (Wayfire can't run here: the source is checked).

- Unloading the plugin left the drawing palette's capture-hiding node in the
  scene; its code went with the library (the next frame crashed Wayfire).
- The frame-time graph kept a stopped app's last seconds forever.
- A display capture rendered a new frame on every request, as fast as a
  recorder asked (hundreds a second); now one per display frame.
- A frame with no picture was never failed: the client waited forever."""
import os
import re
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "wayfire-plugin", "src", "sonata-corners.cpp")


def body(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


class CornersReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = open(SRC).read()

    def test_fini_removes_the_capture_hiding_nodes(self):
        fini = body(self.src, "    void fini() override", "program_ref_count")
        self.assertIn("get_transformer(capture_hide_name)", fini)
        self.assertIn("get_transformer(transformer_name)", fini)

    def test_frametimes_age_out_without_commits(self):
        ask = body(self.src, "wf::json_t ask(", "return response;")
        i = ask.index("micros.pop_front()")
        self.assertLess(i, ask.index('response["frametimes"]'))

    def test_display_capture_paced_by_the_display(self):
        """At most one picture per refresh; the first one at once (waiting for
        the display's own frame broke screenshots)."""
        req = body(self.src, "static void output_source_request_frame", "\n}\n")
        self.assertIn("1000000000LL / mhz", req)
        self.assertIn("s->idle.run_once", req)
        self.assertIn("s->later.set_timeout", req)
        self.assertNotIn("frame_done_signal", self.src)
        self.assertEqual(self.src.count("later.disconnect();"), 3)          # stopped, display gone, unloaded

    def test_capture_only_what_changed(self):
        """Vini: sharing the screen slowed everything. A picture renders the
        whole display again: only when it repainted (or the captured pointer
        moved), and at most CAPTURE_MAX_FPS a second (it went at 180 Hz)."""
        self.assertIn("static const int CAPTURE_MAX_FPS = 60;", self.src)
        req = body(self.src, "static void output_source_request_frame", "\n}\n")
        self.assertIn("1000000 / CAPTURE_MAX_FPS", req)
        self.assertIn("produce_if_changed()", req)
        self.assertNotIn("s->produce()", req)
        fn = body(self.src, "    void produce_if_changed()", "    /* the display's picture")
        self.assertIn("if (changed || moved || !buffer.get_buffer())", fn)
        self.assertIn("get_cursor_position()", fn)
        hook = body(self.src, "    void hook(bool on)", "    /* A picture when")
        self.assertIn("add_effect(&on_repaint, wf::OUTPUT_EFFECT_PRE)", hook)
        self.assertIn("rem_effect(&on_repaint)", hook)
        stop = body(self.src, "static void output_source_stop", "\n}\n")
        self.assertIn("s->hook(false)", stop)
        gone = body(self.src, "on_output_removed =", "};")
        self.assertLess(gone.index("hook(false)"), gone.index("output = nullptr"))

    def test_screenshots_keep_the_compositors_own_capture(self):
        """Vini: screenshots failed ("failed to copy output") once Sonata's
        display capture stood in for wlroots' for every program. Only
        recorders and screen sharing get it now."""
        flt = body(self.src, "filter->set_filter(", "});")
        self.assertIn("capture_client(c)", flt)
        self.assertIn("(g == ours) ? recorder : !recorder", flt)
        xml = open(os.path.join(os.path.dirname(SRC), "..", "metadata", "sonata-corners.xml")).read()
        self.assertIn('<option name="capture_clients"', xml)
        self.assertIn("wl-screenrec xdg-desktop-portal-wlr", xml)
        self.assertNotIn("grim", xml.split('name="capture_clients"')[1].split("</option>")[0])

    def test_frames_without_a_picture_fail(self):
        for name in ("static void window_source_copy_frame", "static void output_source_copy_frame"):
            copy = body(self.src, name, "\n}\n")
            self.assertIn("wlr_ext_image_copy_capture_frame_v1_fail(", copy, name)
        produce = body(self.src, "    /* the display's picture without the hidden surfaces", "    void frame_event")
        failed = produce[produce.index("FAILED"):produce.index("auto size")]
        self.assertIn("frame_event(0, 0)", failed)


class FpsModuleRepliesTest(unittest.TestCase):
    def test_any_reply_is_safe(self):
        import types
        from sonata2.shell import fpsmodule as F
        m = types.SimpleNamespace(graph=types.SimpleNamespace(set_times=lambda *a: setattr(m, "note", a)),
                                  app=types.SimpleNamespace(set_label=lambda *_: None),      # (the app's name)
                                  history=[], _app_id=None,
                                  live=types.SimpleNamespace(set_label=lambda *_: None),
                                  avg=types.SimpleNamespace(set_label=lambda *_: None),
                                  low=types.SimpleNamespace(set_label=lambda *_: None),
                                  cap=types.SimpleNamespace(set_visible=lambda *_: None))
        for reply in (None, [], ["x"], {"error": "no"}, {"frametimes": ["a", 0, -1], "app-id": "g"},
                      {"frametimes": [16.0], "app-id": "g"}):
            F.FpsModule.show_frames(m, reply)                  # never raises
        F.FpsModule.show_frames(m, ["not", "a", "dict"])
        self.assertEqual(m.note[1], "Update Sonata's Wayfire plugin (./install.sh)")


if __name__ == "__main__":
    unittest.main()
