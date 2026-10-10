"""Task Manager: every GPU listed (Vini: "only one GPU shows" on his laptop,
Radeon 680M + NVIDIA). The NVIDIA driver has no gpu_busy_percent, so the
dGPU was dropped; a sleeping dGPU must still show, without nvidia-smi."""
import os
import shutil
import unittest
from unittest import mock

from sonata2.activity.performance import GPU_GONE_SAMPLES  # noqa: E402
from test_activity import FakeSystem  # noqa: E402


def card(fs, n, driver, status=None):
    d = os.path.join(fs.sys, f"class/drm/card{n}/device")
    os.makedirs(d, exist_ok=True)
    drv = os.path.join(fs.root, "drivers", driver)
    os.makedirs(drv, exist_ok=True)
    os.symlink(drv, os.path.join(d, "driver"))
    if status:
        fs.write(f"class/drm/card{n}/device/power/runtime_status", status + "\n", fs.sys)
    return d


class HybridGpuTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSystem()
        card(self.fs, 0, "nvidia", "suspended")
        card(self.fs, 1, "amdgpu", "active")
        self.fs.write("class/drm/card1/device/gpu_busy_percent", "23\n", self.fs.sys)
        self.fs.write("class/drm/card1/device/mem_info_vram_used", str(256 << 20), self.fs.sys)
        self.fs.write("class/drm/card1/device/mem_info_vram_total", str(512 << 20), self.fs.sys)
        os.makedirs(os.path.join(self.fs.sys, "class/drm/card1-eDP-1"))        # a connector: no card

    def tearDown(self):
        shutil.rmtree(self.fs.root)

    def test_sleeping_nvidia_listed_without_waking_it(self):
        s = self.fs.sampler()
        with mock.patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
                mock.patch("subprocess.run") as run:
            snap = s.sample()
        run.assert_not_called()                                        # never woken to ask
        self.assertEqual(snap.gpus, {"card0": None, "card1": 23.0})
        self.assertEqual(snap.gpu_info["card0"]["maker"], "NVIDIA")
        self.assertTrue(snap.gpu_info["card0"]["asleep"])
        self.assertEqual(snap.gpu_info["card1"], {"maker": "AMD", "asleep": False,
                                                  "vram": (256 << 20, 512 << 20)})

    def test_awake_nvidia_reads_its_load(self):
        self.fs.write("class/drm/card0/device/power/runtime_status", "active\n", self.fs.sys)
        s = self.fs.sampler()
        s._stats = mock.Mock(_cards=[("nvidia", "NVIDIA", "card0"), ("amd", "AMD", "card1")])
        s._stats.gpus.return_value = {"nvidia": 41.0, "amd": 23.0}
        s._stats.vrams.return_value = {"nvidia": (1 << 30, 4 << 30), "amd": None}
        snap = s.sample()
        self.assertEqual(snap.gpus, {"card0": 41.0, "card1": 23.0})
        self.assertEqual(snap.gpu_info["card0"]["vram"], (1 << 30, 4 << 30))
        self.assertFalse(snap.gpu_info["card0"]["asleep"])


class PerformancePageGpuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        from sonata2 import ui
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.activity.gpus")
        cls.app.register(None)

    def setUp(self):
        self.fs = FakeSystem()
        card(self.fs, 0, "nvidia", "suspended")
        card(self.fs, 1, "amdgpu", "active")
        self.fs.write("class/drm/card1/device/gpu_busy_percent", "23\n", self.fs.sys)

    def tearDown(self):
        shutil.rmtree(self.fs.root)

    def test_both_gpus_rows_sleeping_one_says_so(self):
        from sonata2.activity.window import TaskManagerWindow
        s = self.fs.sampler()
        w = TaskManagerWindow(self.app, sampler=s)
        w.icons = {}
        with mock.patch("shutil.which", return_value=None):
            w.show_page("performance")
            w.apply_snapshot(s.sample())
            self.fs.now += 2
            for _ in range(GPU_GONE_SAMPLES + 1):                  # a sleeping card never "goes away"
                w.apply_snapshot(s.sample())
                self.fs.now += 1
        perf = w.page_by_id["performance"]
        self.assertIn("gpu:card0", perf.resources)
        self.assertIn("gpu:card1", perf.resources)
        self.assertEqual(perf.resources["gpu:card0"].subtitle, "NVIDIA  Sleeping")
        self.assertEqual(perf.resources["gpu:card1"].subtitle, "AMD  23%")
        self.assertEqual(perf.resources["gpu:card0"].a.values[-1], 0.0)
        w.show_page("processes")
        with mock.patch("shutil.which", return_value=None):
            w.apply_snapshot(s.sample())
        procs = w.page_by_id["processes"]
        self.assertEqual(procs.cols["gpu"].get_title(), "23%\nGPU")       # None values don't crash max()
        w.destroy()


if __name__ == "__main__":
    unittest.main()
