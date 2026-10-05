"""Vini: sending a screen recording in the WhatsApp web app froze the whole
computer and it restarted. WhatsApp hands the video to an <img>; WebKitGTK
decodes every frame of it into memory (50 GB). Chrome refuses a video
there, and so do web apps now; a web app also runs with a memory cap, so a
site that runs away is killed alone."""
import json
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import GLib, Gtk, WebKit  # noqa: E402

from sonata2 import webapps as W  # noqa: E402
from sonata2.webapps import window as WW  # noqa: E402

PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
PAGE = """<script>
const png = Uint8Array.from(atob('%s'), c => c.charCodeAt(0));
function load(blob) { return new Promise(r => { const i = new Image(); i.onload = () => r('load');
  i.onerror = () => r('error'); i.src = URL.createObjectURL(blob); }); }
function attr(blob) { return new Promise(r => { const i = document.createElement('img'); i.onload = () => r('load');
  i.onerror = () => r('error'); i.setAttribute('src', URL.createObjectURL(blob)); }); }
window.result = Promise.all([
  load(new Blob([png], {type: 'image/png'})),
  load(new Blob([png], {type: 'video/mp4'})),
  attr(new Blob([png], {type: 'video/mp4'})),
  createImageBitmap(new Blob([png], {type: 'video/mp4'})).then(() => 'bitmap', () => 'rejected'),
  createImageBitmap(new Blob([png], {type: 'image/png'})).then(() => 'bitmap', () => 'rejected'),
]).then(JSON.stringify);
</script>""" % PNG


class NoVideoImagesTest(unittest.TestCase):
    def test_in_webkit(self):
        win = Gtk.Window()
        view = WebKit.WebView(user_content_manager=WW.content_manager(WebKit))
        win.set_child(view)
        win.present()
        loop, out = GLib.MainLoop(), {}

        def loaded(v, ev):
            if ev != WebKit.LoadEvent.FINISHED:
                return

            def got(v, res):
                try:
                    out["r"] = json.loads(v.evaluate_javascript_finish(res).to_string())
                finally:
                    loop.quit()
            v.call_async_javascript_function("return await window.result;", -1, None, None, None, None, got)
        view.connect("load-changed", loaded)
        view.load_html(PAGE, "https://web.example.com/")
        GLib.timeout_add(15000, loop.quit)
        loop.run()
        win.destroy()
        self.assertEqual(out.get("r"), ["load", "error", "error", "rejected", "bitmap"])


class GpuDecodersOffTest(unittest.TestCase):
    """Vini: then WhatsApp said the mp4 was not supported -- WebKit's video
    through the GPU decoders failed ("Media failed to decode"); software works."""

    def test_ranks(self):
        r = W.gst_ranks("")
        self.assertIn("nvh264dec:0", r.split(","))
        self.assertIn("vah264dec:0", r.split(","))
        self.assertIn("vulkanh264dec:0", r.split(","))
        kept = W.gst_ranks("nvh264dec:300,foo:1")
        self.assertTrue(kept.startswith("nvh264dec:300,foo:1,"))      # what was set stays
        self.assertEqual(kept.split(",").count("nvh264dec:0"), 0)

    def test_main_sets_it(self):
        with mock.patch.dict(W.os.environ, {W.SCOPED_ENV: "1"}), mock.patch.object(W.MemoryGuard, "start"), \
                mock.patch("sonata2.webapps.window.main", return_value=0):
            W.os.environ.pop("GST_PLUGIN_FEATURE_RANK", None)
            W.main(["abc"])
            self.assertIn("nvh264dec:0", W.os.environ["GST_PLUGIN_FEATURE_RANK"])


class MemoryCapTest(unittest.TestCase):
    def test_cap_follows_free_memory(self):
        self.assertEqual(W.memory_cap(2000, 20000), 2000 + 20000 - W.RESERVE_MB)   # room: big cap
        self.assertEqual(W.memory_cap(6000, 500), W.FLOOR_MB if 6500 - W.RESERVE_MB < W.FLOOR_MB else 6500 - W.RESERVE_MB)
        self.assertEqual(W.memory_cap(100, 100), W.FLOOR_MB)

    def test_guard_writes_the_cap(self):
        import os
        import tempfile
        d = tempfile.mkdtemp(suffix=".scope")
        for name, val in (("memory.current", str(3000 * 1024 * 1024)), ("memory.max", "max")):
            with open(os.path.join(d, name), "w") as f:
                f.write(val)
        g = W.MemoryGuard(d)
        self.assertTrue(g.ok())
        with mock.patch.object(W, "_meminfo", return_value={"MemAvailable": 10000}):
            self.assertTrue(g.tick())
        with open(os.path.join(d, "memory.max")) as f:
            self.assertEqual(int(f.read()), W.memory_cap(3000, 10000) * 1024 * 1024)
        first = g.last
        with mock.patch.object(W, "_meminfo", return_value={"MemAvailable": 10050}):
            g.tick()
        self.assertEqual(g.last, first)                                         # small move: not written
        with mock.patch.object(W, "_meminfo", return_value={"MemAvailable": 2000}):
            g.tick()
        self.assertEqual(g.last, W.memory_cap(3000, 2000))                      # less free: lower cap
        self.assertFalse(W.MemoryGuard("/sys/fs/cgroup/user.slice").ok())              # not a scope

    def test_scoped_command(self):
        cmd = W.scoped_command(["waee32c088c"], exe="/usr/bin/python3")
        self.assertEqual(cmd[:3], ["systemd-run", "--user", "--scope"])
        self.assertIn("MemorySwapMax=0", cmd)
        self.assertEqual(cmd[-5:], ["/usr/bin/python3", "-m", "sonata2", "webapp", "waee32c088c"])

    def test_main_reexecs_once(self):
        with mock.patch.dict(W.os.environ, {}, clear=False), \
                mock.patch.object(W.shutil, "which", return_value="/usr/bin/systemd-run"), \
                mock.patch.object(W.os, "execvp") as ex, mock.patch.object(W.MemoryGuard, "start"):
            W.os.environ.pop(W.SCOPED_ENV, None)
            with mock.patch("sonata2.webapps.window.main", return_value=0):
                W.main(["abc"])
            ex.assert_called_once()
            self.assertEqual(W.os.environ[W.SCOPED_ENV], "1")
        with mock.patch.dict(W.os.environ, {W.SCOPED_ENV: "1"}), mock.patch.object(W.os, "execvp") as ex, \
                mock.patch("sonata2.webapps.window.main", return_value=0), \
                mock.patch.object(W.MemoryGuard, "start") as guard:
            W.main(["abc"])
            ex.assert_not_called()
            guard.assert_called_once()

    def test_forms_not_scoped(self):
        with mock.patch.object(W.os, "execvp") as ex, mock.patch("sonata2.webapps.window.main", return_value=0):
            W.main(["new"])
        ex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
