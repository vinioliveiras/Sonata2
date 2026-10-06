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
        req = body(self.src, "static void output_source_request_frame", "\n}\n")
        self.assertIn("schedule_redraw()", req)
        self.assertNotRegex(req, r"else\s*\{\s*s->idle\.run_once")
        self.assertIn("wf::signal::connection_t<wf::frame_done_signal> on_frame", self.src)
        self.assertIn("out->connect(&src->on_frame);", self.src)
        self.assertEqual(self.src.count("on_frame.disconnect();"), 2)       # display gone, plugin unloaded

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
