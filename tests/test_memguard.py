"""Vini (memory review): manage memory as well as possible before any
alert -- background apps give memory back first; the alert ("run out of
application memory") only as a last resort, and not again for a while."""
import os
import tempfile
import unittest
from unittest import mock

from sonata2.backend import memguard as MG


def fake_slice(apps):
    root = tempfile.mkdtemp()
    for unit, mem, pids in apps:
        d = os.path.join(root, unit)
        os.makedirs(d)
        for name, val in (("cgroup.procs", "\n".join(map(str, pids))), ("memory.current", str(mem)),
                          ("memory.reclaim", ""), ("cgroup.kill", "")):
            with open(os.path.join(d, name), "w") as f:
                f.write(val)
    return root


GB = 1024 ** 3


class LevelTest(unittest.TestCase):
    def test_levels(self):
        total = 32 * 1024
        self.assertEqual(MG.level(total, 20000, {"some": 0, "full": 0}), "ok")
        self.assertEqual(MG.level(total, 3000, {"some": 0, "full": 0}), "pressure")       # < 15% free
        self.assertEqual(MG.level(total, 20000, {"some": 25, "full": 0}), "pressure")     # stalls
        self.assertEqual(MG.level(total, 1000, {"some": 50, "full": 30}), "critical")
        self.assertEqual(MG.level(total, 1000, {"some": 50, "full": 2}), "pressure")      # low, but not stalling
        self.assertEqual(MG.level(0, 0, {}), "ok")

    def test_psi_and_meminfo(self):
        f = tempfile.NamedTemporaryFile("w", delete=False)
        f.write("some avg10=12.50 avg60=1.00 avg300=0.00 total=1\nfull avg10=3.25 avg60=0.00 avg300=0.00 total=1\n")
        f.close()
        self.assertEqual(MG.psi(f.name), {"some": 12.5, "full": 3.25})
        self.assertEqual(MG.psi("/nonexistent"), {"some": 0.0, "full": 0.0})
        self.assertIn("total", MG.meminfo())


class ScopeTest(unittest.TestCase):
    def test_background_first_biggest(self):
        root = fake_slice([("app-sonata2-steam-10.scope", 3 * GB, [10, 11]),
                           ("app-sonata2-org.gnome.Nautilus-20.scope", 1 * GB, [20]),
                           ("run-p30-i1.scope", 2 * GB, [30])])
        scopes = MG.scopes(root)
        self.assertEqual(len(scopes), 3)
        views = [{"pid": 20, "mapped": True, "minimized": False},          # Files on screen
                 {"pid": 10, "mapped": True, "minimized": True}]           # Steam minimized
        bg = MG.background(scopes, views)
        self.assertEqual([s["unit"] for s in bg], ["app-sonata2-steam-10.scope", "run-p30-i1.scope"])

    def test_reclaim_and_kill(self):
        root = fake_slice([("app-sonata2-steam-10.scope", 8 * GB, [10])])
        s = MG.scopes(root)[0]
        self.assertEqual(MG.reclaim_amount(2 * GB), GB // 2)                    # a quarter
        amount = MG.reclaim_amount(s["memory"])
        self.assertEqual(amount, MG.RECLAIM_MAX_MB * 1024 * 1024)               # capped
        self.assertTrue(MG.reclaim(s["path"], amount))
        with open(os.path.join(s["path"], "memory.reclaim")) as f:
            self.assertEqual(f.read(), str(amount))
        self.assertFalse(MG.reclaim(s["path"], 1024))                            # too little to bother
        self.assertTrue(MG.kill(s["path"]))
        with open(os.path.join(s["path"], "cgroup.kill")) as f:
            self.assertEqual(f.read(), "1")

    def test_app_of(self):
        self.assertEqual(MG.app_of({"unit": "app-sonata2-org.gnome.Nautilus-20.scope", "pids": []}),
                         ("org.gnome.Nautilus", None))
        self.assertEqual(MG.app_of({"unit": "app-sonata2-my\\x2dapp-7.scope", "pids": []}), ("my-app", None))


class WatchTest(unittest.TestCase):
    def make(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from sonata2.shell import memorywatch as W
        with mock.patch.object(W.GLib, "timeout_add_seconds"):
            w = W.MemoryWatch(None)
        return W, w

    def run_ticks(self, W, w, lv, n, scopes):
        shown = []
        with mock.patch.object(W.MG, "meminfo", return_value={"total": 32000, "available": 100}), \
                mock.patch.object(W.MG, "psi", return_value={}), mock.patch.object(W.MG, "level", return_value=lv), \
                mock.patch("sonata2.backend.system.run_async", side_effect=lambda fn, cb: cb((scopes, 1))), \
                mock.patch.object(w, "_show", side_effect=lambda s: (shown.append(1), setattr(w, "alert", object()))):
            for _ in range(n):
                w.tick()
        return shown

    def test_alert_only_after_a_while(self):
        W, w = self.make()
        scopes = [{"path": "/x", "unit": "u", "memory": GB, "pids": [1]}]
        self.assertEqual(self.run_ticks(W, w, "critical", W.CRITICAL_CHECKS - 1, scopes), [])
        self.assertEqual(self.run_ticks(W, w, "critical", 1, scopes), [1])

    def test_pressure_never_alerts(self):
        W, w = self.make()
        self.assertEqual(self.run_ticks(W, w, "pressure", 20, [{"path": "/x", "unit": "u", "memory": GB, "pids": [1]}]),
                         [])

    def test_cooldown_after_dismiss(self):
        import time
        W, w = self.make()
        w.dismissed_at = time.monotonic()
        self.assertEqual(self.run_ticks(W, w, "critical", 10, [{"path": "/x", "unit": "u", "memory": GB, "pids": [1]}]),
                         [])

    def test_wired_into_menu_bar(self):
        import inspect
        from sonata2.shell import topbar
        self.assertIn("MemoryWatch(app)", inspect.getsource(topbar.TopBarWindow.__init__))


if __name__ == "__main__":
    unittest.main()
