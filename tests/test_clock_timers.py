"""Clock: Stopwatch and Timers (Vini: "conseguimos botar cronometro e
timer"). Both keep going with Clock closed (a file), the menu bar process
rings the timer like an alarm."""
import datetime as dt
import os
import tempfile
import time
import unittest
from unittest import mock

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.clock import timers as T  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class StopwatchModelTest(unittest.TestCase):
    def test_runs_stops_and_laps(self):
        sw = T.new_stopwatch()
        T.sw_start(sw, now=100)
        self.assertAlmostEqual(T.elapsed(sw, 103.5), 3.5)
        T.sw_lap(sw, now=103.5)
        T.sw_lap(sw, now=105.0)
        self.assertEqual(sw["laps"], [3.5, 1.5])
        self.assertAlmostEqual(T.current_lap(sw, 106), 1.0)
        T.sw_stop(sw, now=110)
        self.assertAlmostEqual(T.elapsed(sw, 999), 10)               # stopped: no longer counts
        T.sw_start(sw, now=200)
        self.assertAlmostEqual(T.elapsed(sw, 202), 12)               # goes on from there
        T.sw_lap(T.sw_stop(sw, 203), now=203)
        self.assertEqual(len(sw["laps"]), 2)                         # no lap while stopped
        T.sw_reset(sw)
        self.assertEqual(sw, T.new_stopwatch())

    def test_best_and_worst(self):
        self.assertEqual(T.best_worst([5.0]), (None, None))
        self.assertEqual(T.best_worst([5.0, 3.0, 8.0]), (1, 2))

    def test_texts(self):
        self.assertEqual(T.stopwatch_text(83.456), "01:23,45")
        self.assertEqual(T.stopwatch_text(3725.5), "1:02:05,50")
        self.assertEqual(T.timer_text(392.2), "06:33")              # rounded up
        self.assertEqual(T.timer_text(3600), "1:00:00")
        self.assertEqual(T.duration_text(300), "5 min")
        self.assertEqual(T.duration_text(5400), "1 h 30 min")
        self.assertEqual(T.duration_text(45), "45 s")


class TimerModelTest(unittest.TestCase):
    def test_start_pause_resume_cancel(self):
        tm = T.new_timer()
        T.tm_start(tm, 60, "Pasta", now=1000)
        self.assertEqual(T.left(tm, 1015), 45)
        self.assertAlmostEqual(T.progress(tm, 1015), 0.75)
        T.tm_pause(tm, now=1015)
        self.assertEqual(T.left(tm, 5000), 45)                       # paused: stays
        T.tm_resume(tm, now=2000)
        self.assertFalse(T.due(tm, 2044))
        self.assertTrue(T.due(tm, 2045))
        T.tm_cancel(tm)
        self.assertEqual((tm["state"], T.left(tm)), ("idle", 60))    # ready again with the same time

    def test_storage_survives_damage(self):
        with open(T.path(), "w") as f:
            f.write('{"stopwatch": {"laps": "x", "since": "y"}, "timer": {"state": "boom", "duration": -3}}')
        sw, tm = T.load()
        self.assertEqual(sw["laps"], [])
        self.assertEqual(tm["state"], "idle")
        self.assertGreater(tm["duration"], 0)
        sw["running"], sw["since"] = True, 5.0
        T.save(sw, tm)
        self.assertEqual(T.load()[0]["since"], 5.0)


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.clocktimers")
        cls.app.register(None)

    def test_the_menu_bar_rings_the_timer(self):
        from sonata2.clock import alarms as A
        from sonata2.shell import alarmservice as S
        A.save([])
        sw, tm = T.new_stopwatch(), T.tm_start(T.new_timer(), 60, "Tea", now=time.time())
        T.save(sw, tm)
        ring = mock.patch.object(S.Ringer, "start").start()
        self.addCleanup(mock.patch.stopall)
        sv = S.AlarmService(self.app)
        self.assertEqual(sv.clock_timer["state"], "running")
        sv.check(dt.datetime.now())
        ring.assert_not_called()                                     # not yet
        sv.check(dt.datetime.now() + dt.timedelta(seconds=61))
        ring.assert_called_once_with(T.SOUND)
        self.assertIsNotNone(sv.card)
        self.assertEqual(sv.card.get_title(), "Timer")
        self.assertFalse(hasattr(sv.card, "snooze_btn"))             # Stop only
        self.assertEqual(T.load()[1]["state"], "idle")               # back to idle for Clock
        sv.stop()


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.clocktimerswin")
        cls.app.register(None)

    def window(self):
        from sonata2.clock.window import ClockWindow
        T.save(T.new_stopwatch(), T.new_timer())
        w = ClockWindow(self.app)
        w.present()
        self.addCleanup(w.destroy)
        settle()
        return w

    def test_tabs(self):
        w = self.window()
        self.assertEqual([b.get_label() for b in w.tabs.buttons.values()], ["Alarms", "Stopwatch", "Timers"])
        self.assertIn("plain", w.tabs.get_css_classes())             # the UI kit's toolbar tabs
        w.tabs.buttons["timers"].emit("clicked")
        self.assertEqual(w.pages.get_visible_child_name(), "timers")
        self.assertFalse(w.add_btn.get_visible())                    # + is the alarms'
        from sonata2 import config
        self.assertEqual(config.load("clock", {"page": None})["page"], "timers")  # opens there next time

    def test_stopwatch_buttons(self):
        w = self.window()
        sp = w.stopwatch
        self.assertEqual((sp.go_btn.get_label(), sp.lap_btn.get_sensitive()), ("Start", False))
        sp.go_btn.emit("clicked")
        self.assertEqual(sp.go_btn.get_label(), "Stop")
        self.assertIn("red", sp.go_btn.get_css_classes())
        sp.lap_btn.emit("clicked")
        self.assertEqual(len(T.load()[0]["laps"]), 1)                # saved: goes on with Clock closed
        sp.go_btn.emit("clicked")
        self.assertEqual(sp.lap_btn.get_label(), "Reset")
        sp.lap_btn.emit("clicked")
        self.assertEqual(T.load()[0], T.new_stopwatch())

    def test_timer_start_pause_cancel(self):
        w = self.window()
        tp = w.timer
        self.assertEqual(tp.root.get_visible_child_name(), "setup")
        tp.spins[1].set_value(2)                                     # 2 min
        tp.name.set_text("Eggs")
        tp.start_btn.emit("clicked")
        tm = T.load()[1]
        self.assertEqual((tm["state"], tm["duration"], tm["label"]), ("running", 120, "Eggs"))
        self.assertEqual(tp.root.get_visible_child_name(), "run")
        self.assertIn("Eggs", tp.sub.get_label())
        tp.pause_btn.emit("clicked")
        self.assertEqual((T.load()[1]["state"], tp.pause_btn.get_label()), ("paused", "Resume"))
        tp.cancel_btn.emit("clicked")
        self.assertEqual(tp.root.get_visible_child_name(), "setup")
        self.assertEqual(int(tp.spins[1].get_value()), 2)           # the last time, ready again

    def test_nothing_ticks_when_idle(self):
        w = self.window()
        self.assertEqual((w.stopwatch._tick_id, w.timer._tick_id), (0, 0))


class UiKitTest(unittest.TestCase):
    def test_circle_button(self):
        Adw.init()
        ui.setup()
        b = ui.controls.circle_button("Start", tone="green")
        b.set_tone("red")
        self.assertIn("red", b.get_css_classes())
        self.assertNotIn("green", b.get_css_classes())


if __name__ == "__main__":
    unittest.main()
