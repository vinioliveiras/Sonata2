"""Vini felt Sonata got slower. What was found, kept from coming back:

- every sound stream's events (sink-input) read the volume again, many a second;
- no DDC/CI display found was asked again on every Control Center opening
  (`ddcutil detect`: seconds);
- quit-on-close listed every window on every title change for an app whose
  process it never found;
- an app_id matched read and parsed its .desktop file again each time
  (~60 us, many times per window event).

    python3 -m unittest tests.test_perf_regressions
"""
import unittest
from unittest import mock

from sonata2.backend import system as S
from sonata2.shell import quitonclose as Q


class AudioEventsTest(unittest.TestCase):
    def test_only_devices(self):
        self.assertTrue(S._AUDIO_EVENT.search("Event 'change' on sink #56"))
        self.assertTrue(S._AUDIO_EVENT.search("Event 'new' on source #3"))
        self.assertFalse(S._AUDIO_EVENT.search("Event 'change' on sink-input #812"))
        self.assertFalse(S._AUDIO_EVENT.search("Event 'change' on source-output #9"))


class DdcDetectTest(unittest.TestCase):
    def test_none_found_is_kept_too(self):
        with mock.patch.dict(S._detected, {"at": 0.0, "displays": []}), \
                mock.patch.object(S, "_run", return_value=(0, "Invalid display\n")) as run:
            self.assertEqual(S.ddc_displays(), [])
            self.assertEqual(S.ddc_displays(), [])
        run.assert_called_once()


class QuitOnCloseRetryTest(unittest.TestCase):
    def test_unknown_process_asked_again_only_now_and_then(self):
        class Mgr:
            toplevels, listeners = [], []
        m = Mgr()
        m.toplevels = [type("T", (), {"app_id": "flatpak.app"})()]
        clock = [100.0]
        views = mock.Mock(return_value=[])
        with mock.patch.object(Q.QuitOnClose, "key_of", staticmethod(lambda a: a)), \
                mock.patch.object(Q.quitapps, "app_pids", return_value=set()):
            q = Q.QuitOnClose(m, views=views, enabled=lambda: True, later=lambda ms, fn: None,
                              now=lambda: clock[0])
            clock[0] += Q.RETRY_S                                 # (its first moments: asked freely)
            for _ in range(20):                                   # title changes
                q.changed()
            self.assertEqual(views.call_count, 2)
            clock[0] += Q.RETRY_S
            q.changed()
            self.assertEqual(views.call_count, 3)


class MatchAppIdTest(unittest.TestCase):
    def test_a_match_is_kept_a_while(self):
        from sonata2 import apps
        apps.refresh()
        with mock.patch.object(apps, "_match", return_value="org.gnome.Calculator") as match:
            for _ in range(50):
                self.assertEqual(apps.match_app_id("gnome-calculator"), "org.gnome.Calculator")
            match.assert_called_once()
            with mock.patch.object(apps.time, "monotonic", return_value=apps.time.monotonic() + apps.HIT_KEEP_S + 1):
                apps.match_app_id("gnome-calculator")
            self.assertEqual(match.call_count, 2)                         # asked again after a while
            apps.refresh()                                                # apps installed / removed
            apps.match_app_id("gnome-calculator")
            self.assertEqual(match.call_count, 3)
        apps.refresh()


class MixerLearnTest(unittest.TestCase):
    def test_learns_seldom_and_never_a_new_streams_start(self):
        """A playing app's 'change' events read every stream (pactl) each
        LEARN_MS; a new stream's own start level stays unlearned (FRESH_S)."""
        from sonata2.backend import mixer as M
        self.assertGreaterEqual(M.LEARN_MS, 5000)
        self.assertGreater(M.FRESH_S * 1000, M.LEARN_MS)


class DownloadsStackTest(unittest.TestCase):
    def test_a_download_being_written_is_not_listed_again(self):
        """The Dock's Downloads stack listed the whole folder (a stat per
        file) every second while a download was being written."""
        import os
        import tempfile
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, GLib
        from sonata2.shell import dock_stack as D
        folder = tempfile.mkdtemp()
        mon = Gio.File.new_for_path(folder).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        seen, kept = [], []
        mon.connect("changed", lambda _m, _f, _o, ev: (seen.append(ev), D.stack_event(ev) and kept.append(ev)))

        def spin(ms=300):
            ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + ms * 1000
            while GLib.get_monotonic_time() < end:
                ctx.iteration(False)
        with open(os.path.join(folder, "video.mp4"), "wb") as f:
            spin()
            for _ in range(20):
                f.write(b"x" * 4096)
                f.flush()
                spin(20)
        spin()
        writes = seen.count(Gio.FileMonitorEvent.CHANGED)
        self.assertGreaterEqual(writes, 1)       # (the download's writes: Gio sends one every ~0.8 s)
        self.assertNotIn(Gio.FileMonitorEvent.CHANGED, kept)
        self.assertIn(Gio.FileMonitorEvent.CREATED, kept)                 # it came
        self.assertIn(Gio.FileMonitorEvent.CHANGES_DONE_HINT, kept)       # and is done
        mon.cancel()


class DockJsonTest(unittest.TestCase):
    def test_only_the_glass_switch_restyles(self):
        """Every dock.json write (an icon pinned, the Dock resized) re-read the
        appearance in every Sonata process; only its old "glass" key is theirs."""
        from sonata2.ui import theme as T
        cfg = {"glass": True, "pinned": ["a"]}
        with mock.patch.object(T, "_dock_glass", side_effect=lambda: cfg.get("glass")), \
                mock.patch.object(T, "_appearance_changed") as restyle, \
                mock.patch.object(T, "_dock_glass_seen", True):
            for n in range(5):
                cfg["pinned"].append(str(n))
                T._dock_changed()
            restyle.assert_not_called()
            cfg["glass"] = False
            T._dock_changed()
            restyle.assert_called_once()


class MenuBarIconsTest(unittest.TestCase):
    """The menu bar's icons were drawn anew on every window change, a
    browser's title changes included (many a second)."""

    def test_only_when_the_front_app_or_the_windows_change(self):
        import types
        from sonata2.shell import topbar as T
        win = lambda app, on=False: types.SimpleNamespace(app_id=app, activated=on, title="")   # noqa: E731
        chrome, files = win("chrome", True), win("files")
        bar = types.SimpleNamespace(manager=types.SimpleNamespace(toplevels=[chrome, files]), _icons_soon=mock.Mock())
        redraw = lambda: T.Bar._icons_if_changed(bar)                                         # noqa: E731
        redraw()
        for n in range(20):
            chrome.title = f"({n}) Inbox"                                 # titles only
            redraw()
        self.assertEqual(bar._icons_soon.call_count, 1)
        chrome.activated, files.activated = False, True                   # another app in front
        redraw()
        bar.manager.toplevels.append(win("term"))                         # a window opened
        redraw()
        bar.manager.toplevels.pop()                                       # and closed (Vini: icons went blank)
        redraw()
        self.assertEqual(bar._icons_soon.call_count, 4)


if __name__ == "__main__":
    unittest.main()
