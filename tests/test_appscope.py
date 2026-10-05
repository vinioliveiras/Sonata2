"""Vini: an app that ran the memory out (WhatsApp, 50 GB) froze the whole
computer until it restarted. Apps opened from Sonata run in their own scopes
under sonata-apps.slice: systemd-oomd closes the one causing the pressure,
and apps together never take the memory kept for Sonata and the system."""
import os
import tempfile
import unittest
from unittest import mock

from gi.repository import Gio, GLib

from sonata2 import appscope as A


class SliceTest(unittest.TestCase):
    def test_text(self):
        t = A.slice_text(64 * 1024)
        self.assertIn("ManagedOOMMemoryPressure=kill", t)
        self.assertIn("ManagedOOMSwap=kill", t)
        top = 64 * 1024 - A.RESERVE_MAX_MB                          # 10% would be 6.4 GB: capped at 4
        self.assertIn(f"MemoryMax={top}M", t)
        self.assertIn(f"MemoryHigh={top - 512}M", t)
        self.assertIn(f"MemoryMax={8192 - A.RESERVE_MIN_MB}M", A.slice_text(8192))   # small: at least 1.5 GB kept
        self.assertNotIn("MemoryMax", A.slice_text(0))                # unknown: no cap

    def test_reserve(self):
        self.assertEqual(A.reserve_mb(4096), A.RESERVE_MIN_MB)
        self.assertEqual(A.reserve_mb(20000), 2000)
        self.assertEqual(A.reserve_mb(128000), A.RESERVE_MAX_MB)

    def test_written_once_and_reloaded(self):
        d = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}), mock.patch.object(A, "_bus", return_value="bus"), \
                mock.patch.object(A, "_manager") as mgr:
            self.assertTrue(A.ensure_slice())
            self.assertTrue(A.ensure_slice())
            self.assertTrue(os.path.exists(os.path.join(d, "systemd", "user", A.SLICE)))
        self.assertEqual([c.args[1] for c in mgr.call_args_list], ["Reload"])           # only when it changed


class ScopeTest(unittest.TestCase):
    def test_unit_name(self):
        self.assertEqual(A.unit_name("org.gnome.Nautilus.desktop", 42), "app-sonata2-org.gnome.Nautilus-42.scope")
        self.assertEqual(A.unit_name("my-app", 7), "app-sonata2-my\\x2dapp-7.scope")

    def test_move(self):
        with mock.patch.object(A, "_bus", return_value="bus"), mock.patch.object(A, "_manager") as mgr, \
                mock.patch.object(A, "raise_oom_score") as oom:
            self.assertTrue(A.move(1234, "steam.desktop"))
        method, params = mgr.call_args.args[1], mgr.call_args.args[2]
        self.assertEqual(method, "StartTransientUnit")
        name, _mode, props, _aux = params.unpack()
        self.assertEqual(name, "app-sonata2-steam-1234.scope")
        self.assertEqual(dict(props)["PIDs"], [1234])
        self.assertEqual(dict(props)["Slice"], A.SLICE)
        oom.assert_called_once_with(1234)

    def test_web_apps_make_their_own(self):
        with mock.patch.object(A, "_manager") as mgr:
            self.assertFalse(A.move(5, "io.github.vinioliveiras.sonata2.webapp.abc"))
            self.assertFalse(A.move(0, "steam"))
        mgr.assert_not_called()

    def test_watch_connects_once(self):
        ctx = Gio.AppLaunchContext()
        A.watch(ctx, "steam")
        A.watch(ctx, "steam")
        moved = []
        with mock.patch.object(A, "move", side_effect=lambda pid, app: moved.append((pid, app))):
            ctx.emit("launched", None, GLib.Variant("a{sv}", {"pid": GLib.Variant("i", 99)}))
        self.assertEqual(moved, [(99, "steam")])

    def test_oom_score_only_raised(self):
        f = tempfile.NamedTemporaryFile("w", delete=False)
        f.write("0")
        f.close()
        real = open
        with mock.patch("builtins.open", side_effect=lambda p, *a, **k: real(f.name, *a, **k)):
            self.assertTrue(A.raise_oom_score(1))
        with open(f.name) as g:
            self.assertEqual(g.read(), str(A.APP_OOM_SCORE))

    def test_login_items_flushed(self):
        """The login items' launcher exits right away: the moves are sent before it does."""
        import inspect
        from sonata2 import autostart
        self.assertIn("appscope.flush()", inspect.getsource(autostart.run))

        class Bus:
            flushed = False

            def flush_sync(self, _c):
                Bus.flushed = True
        with mock.patch.object(A, "_bus", return_value=Bus()):
            A.flush()
        self.assertTrue(Bus.flushed)

    def test_launches_go_through_it(self):
        import inspect
        from sonata2 import apps
        self.assertIn("appscope.watch(context", inspect.getsource(apps._gpu_aware))
        from sonata2 import webapps
        self.assertIn("--slice=sonata-apps.slice", webapps.scoped_command(["x"]))


if __name__ == "__main__":
    unittest.main()
