"""wl.scan: Python bindings for the Wayland protocols Sonata speaks itself
(the on-screen keyboard's virtual-keyboard, the gamepad's virtual pointer),
generated once into the cache. A bad cache file is imported on every later
start, so how it is written matters more than how fast.
Run: python3 -m unittest tests.test_wl_scan"""
import os
import tempfile
import unittest
from unittest import mock

try:
    import pywayland  # noqa: F401
    from pywayland.scanner import Protocol
except ImportError:                     # the session needs it; a test machine may not have it
    Protocol = None

from sonata2.wl import scan  # noqa: E402

XML, PROTO = "virtual-keyboard-unstable-v1.xml", "virtual_keyboard_unstable_v1"


@unittest.skipIf(Protocol is None, "pywayland not installed")
class ScanTest(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": self.cache})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def folder(self):
        return os.path.join(self.cache, "sonata2", "pywayland-" + pywayland.__version__)

    def test_every_shipped_protocol_loads(self):
        """Each XML in wl/protocols becomes a module with its interfaces, the
        core ones (wl_seat...) taken from pywayland, not a missing .wayland."""
        for xml in sorted(os.listdir(scan.HERE)):
            proto = xml[:-4].replace("-", "_")
            mod = scan.load(xml, proto)
            with open(os.path.join(self.folder(), proto + ".py"), encoding="utf-8") as f:
                self.assertNotIn("from .wayland import", f.read(), xml)
            self.assertTrue(any(n.startswith("Zw") for n in dir(mod)), xml)

    def test_made_once(self):
        """The second load imports the cached module: no scanning again."""
        scan.load(XML, PROTO)
        with mock.patch.object(Protocol, "parse_file", side_effect=AssertionError("scanned again")):
            mod = scan.load(XML, PROTO)
        self.assertTrue(hasattr(mod, "ZwpVirtualKeyboardManagerV1"))

    def test_a_generation_that_dies_leaves_nothing_behind(self):
        """The scanner writes the module in several appends; killed (or read
        by another process) halfway, a header-only module stayed in the cache
        and was imported from then on -- without its interfaces. Now it's
        made aside and renamed into place whole."""
        real = Protocol.output

        def output_then_die(p, out_dir, imports):
            real(p, out_dir, imports)
            path = os.path.join(out_dir, PROTO + ".py")
            with open(path, encoding="utf-8") as f:
                head = f.read()[:400]                     # what the first write had put there
            with open(path, "w", encoding="utf-8") as f:
                f.write(head.rsplit("\n", 1)[0] + "\n")
            raise KeyboardInterrupt("killed")
        with mock.patch.object(Protocol, "output", output_then_die):
            with self.assertRaises(KeyboardInterrupt):
                scan.load(XML, PROTO)
        self.assertFalse(os.path.exists(os.path.join(self.folder(), PROTO + ".py")))
        self.assertEqual([n for n in os.listdir(self.folder()) if n.startswith(".scan-")], [])
        mod = scan.load(XML, PROTO)                      # the next start: made again, whole
        self.assertTrue(hasattr(mod, "ZwpVirtualKeyboardManagerV1"))


if __name__ == "__main__":
    unittest.main()
