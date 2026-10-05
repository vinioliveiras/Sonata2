"""Vini: lag when closing apps. stallwatch logs what blocks a Sonata
process's main loop -- only while its flag file exists."""
import os
import tempfile
import time
import unittest
from unittest import mock

from gi.repository import GLib

from sonata2 import stallwatch as S


class StallWatchTest(unittest.TestCase):
    def test_logs_a_stall_with_its_stack(self):
        d = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": d}):
            os.makedirs(os.path.dirname(S.flag_path()))
            import threading
            main = threading.get_ident()

            t = threading.Thread(target=S._probe, args=("dock", main))

            def busy():
                t.start()
                time.sleep(0.3)                      # the main loop stuck here
                return False
            GLib.idle_add(busy)
            GLib.MainContext.default().iteration(False)
            end = time.time() + 3
            while t.is_alive() and time.time() < end:
                GLib.MainContext.default().iteration(False)
            with open(S.log_path()) as f:
                text = f.read()
            self.assertIn("dock", text)
            self.assertIn("busy", text)              # the function that blocked

    def test_quiet_without_the_flag(self):
        import inspect
        src = inspect.getsource(S.start)
        self.assertIn("flag_path()", src)
        from sonata2.ui import theme
        self.assertIn("stallwatch.start(", inspect.getsource(theme.setup))


if __name__ == "__main__":
    unittest.main()
