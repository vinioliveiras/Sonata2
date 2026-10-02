"""What took the session down (feedback/report.py: classify, history).
Vini: deal with the crashes with data -- each one's cause, and how often.
Run: python3 -m unittest tests.test_crash_kinds"""
import os
import tempfile
import time
import unittest

os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()

from sonata2.feedback import report as R  # noqa: E402

AMD = """kernel: amdgpu 0000:36:00.0: [gfxhub] page fault (src_id:0 ring:24 vmid:4 pasid:1589)
kernel: amdgpu 0000:36:00.0: ring gfx_0.0.0 timeout, signaled seq=1153010, emitted seq=1153011
kernel: amdgpu 0000:36:00.0: Ring gfx_0.0.0 reset succeeded"""
AMD_WF = "EE [GLES2] GL_CONTEXT_LOST in context lost\nEE [render/gles2/pass.c:315] GPU reset (guilty)"
NV_OOM = "kernel: NVRM: GPU0 nvAssertOkFailedNoLog: Assertion failed: Out of memory [NV_ERR_NO_MEMORY]"
NV_WF = ("EE [render/allocator/gbm.c:89] gbm_bo_create failed: Invalid argument\n"
         "EE [src/render.cpp:465] Failed to allocate auxilliary buffer!\n"
         "EE [src/api/wayfire/dassert.hpp:26] Cannot run a render pass without a valid target!")


class KindsTest(unittest.TestCase):
    def setUp(self):
        os.makedirs(R.log_dir(), exist_ok=True)
        for n in (R.HISTORY, "kernel-at-crash.log", "session.old.log"):
            try:
                os.remove(os.path.join(R.log_dir(), n))
            except OSError:
                pass

    def test_todays_crashes(self):
        self.assertEqual(R.classify(AMD, AMD_WF), "amd-reset")
        self.assertEqual(R.classify(NV_OOM, NV_WF), "nvidia-memory")              # the memory, not the buffer
        self.assertEqual(R.classify("", NV_WF), "gpu-buffer")
        self.assertEqual(R.classify("", "EE [src/src/main.cpp:144] Fatal error: Fatal error(SIGABRT)"),
                         "wayfire-abort")
        self.assertEqual(R.classify("kernel: wayfire[2150]: segfault at 21 ip 0 error 4", ""), "wayfire-segfault")
        self.assertEqual(R.classify("", ""), "unknown")
        self.assertIn("AMD", R.describe("amd-reset"))

    def test_noted_once_with_history(self):
        def write(name, text):
            with open(os.path.join(R.log_dir(), name), "w") as f:
                f.write(text)
        write("kernel-at-crash.log", AMD)
        write("session.old.log", AMD_WF)
        now = time.time()
        c = {"time": int(now) - 60, "code": 1}
        self.assertEqual(R.note_crash(c), "amd-reset")
        self.assertEqual(R.note_crash(c), "amd-reset")                         # once
        self.assertEqual(len(R.history()), 1)
        write("kernel-at-crash.log", NV_OOM)
        write("session.old.log", NV_WF)
        R.note_crash({"time": int(now) - 30, "code": 134})
        with open(os.path.join(R.log_dir(), R.HISTORY), "a") as f:
            f.write(f"{int(now) - 30 * 86400} amd-reset 1\n")               # a month ago: not counted
        self.assertEqual(R.history_text(7, now), "2 crashes in the last 7 days: 1 AMD reset, 1 NVIDIA memory")

    def test_feedbacker_says_it(self):
        src = open(os.path.join(os.path.dirname(R.__file__), "window.py")).read()
        self.assertIn("report.note_crash(self.crash)", src)
        self.assertTrue(R.HISTORY.endswith(".log"))                            # it goes into the reports


if __name__ == "__main__":
    unittest.main()
