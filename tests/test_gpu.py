"""Discrete GPU launches: an app marked for it starts with the GPU's
environment (python3 -m unittest tests.test_gpu)."""
import os
import tempfile
import time
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from sonata2 import apps, config, gpu  # noqa: E402

config.CONFIG_DIR = os.path.join(os.environ["XDG_CONFIG_HOME"], "sonata2")


class GpuTest(unittest.TestCase):
    def test_launch_env(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "env.txt")
        path = os.path.join(d, "gputest.desktop")
        with open(path, "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=T\nExec=sh -c 'env > {out}'\n")
        info = apps.DesktopAppInfo.new_from_filename(path)
        gpu._env = {"SONATA_TEST_GPU": "dGPU"}
        self.assertFalse(gpu.wants_discrete(info))
        gpu.set_discrete(info, True)
        self.assertTrue(gpu.wants_discrete(info))
        info.launch([], None)
        for _ in range(50):
            if os.path.exists(out) and os.path.getsize(out):
                break
            time.sleep(0.1)
        with open(out) as f:
            self.assertIn("SONATA_TEST_GPU=dGPU", f.read())
        gpu.set_discrete(info, False)
        self.assertFalse(gpu.wants_discrete(info))


if __name__ == "__main__":
    unittest.main()
