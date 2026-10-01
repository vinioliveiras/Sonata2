"""Clock: alarms, when they go off, the menu bar's ringing service and the
window (xvfb-run python3 -m unittest tests.test_clock)."""
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)

from sonata2 import config, ui  # noqa: E402
from sonata2.clock import alarms as A  # noqa: E402

MON = dt.datetime(2026, 9, 28, 6, 0)          # a Monday


def alarm(h, m, repeat=(), enabled=True, label="Alarm"):
    a = A.new(h, m)
    a.update(repeat=list(repeat), enabled=enabled, label=label)
    return a


class AlarmModelTest(unittest.TestCase):
    def test_next_time(self):
        self.assertEqual(A.next_time(alarm(7, 0), MON), MON.replace(hour=7))
        self.assertEqual(A.next_time(alarm(5, 0), MON), MON.replace(hour=5) + dt.timedelta(days=1))
        sat = A.next_time(alarm(7, 0, repeat=[5, 6]), MON)
        self.assertEqual(sat.weekday(), 5)
        self.assertIsNone(A.next_time(alarm(7, 0, enabled=False), MON))
        at = MON.replace(hour=7, second=30)                        # 07:00:30: today's has gone off
        self.assertEqual(A.next_time(alarm(7, 0), at).date(), (MON + dt.timedelta(days=1)).date())

    def test_due_after_a_suspend(self):
        """Asleep from 6:00 to 8:00: the 7:00 alarm rings when the computer is back."""
        a = alarm(7, 0)
        self.assertEqual(A.due([a], MON, MON.replace(hour=8)), [a])
        self.assertEqual(A.due([a], MON.replace(hour=7, minute=1), MON.replace(hour=8)), [])

    def test_texts(self):
        self.assertEqual(A.repeat_text([0, 1, 2, 3, 4]), "Weekdays")
        self.assertEqual(A.repeat_text([5, 6]), "Weekends")
        self.assertEqual(A.repeat_text(range(7)), "Every day")
        self.assertEqual(A.repeat_text([0, 2]), "Mon, Wed")
        self.assertEqual(A.time_text(alarm(7, 5)), "07:05")
        self.assertEqual(A.time_text(alarm(19, 5), h24=False), "7:05 PM")

    def test_storage_cleans(self):
        A.save([{"hour": "25", "minute": 61, "repeat": [9, 1, 1]}, "junk"])
        [a] = A.load()
        self.assertEqual((a["hour"], a["minute"], a["repeat"]), (1, 1, [1]))


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.clockservice")
        cls.app.register(None)

    def service(self, alarms):
        from sonata2.shell import alarmservice as S
        A.save(alarms)
        self.ringer = mock.patch.object(S.Ringer, "start").start()
        self.addCleanup(mock.patch.stopall)
        return S.AlarmService(self.app, now=MON)

    def test_rings_once_and_turns_one_time_off(self):
        once, daily = alarm(7, 0, label="Once"), alarm(7, 0, repeat=range(7), label="Daily")
        sv = self.service([once, daily])
        self.assertEqual(sv.check(MON.replace(hour=6, minute=59)), [])
        rung = sv.check(MON.replace(hour=7, second=5))
        self.assertEqual({a["label"] for a in rung}, {"Once", "Daily"})
        self.assertTrue(self.ringer.called)
        self.assertIsNotNone(sv.card)
        states = {a["label"]: a["enabled"] for a in A.load()}
        self.assertEqual(states, {"Once": False, "Daily": True})
        self.assertEqual(sv.check(MON.replace(hour=7, minute=2)), [])       # not twice
        sv.stop()
        self.assertIsNone(sv.card)

    def test_rings_with_do_not_disturb(self):
        config.save("notifications", {"dnd": True, "apps": {}})
        sv = self.service([alarm(7, 0)])
        self.assertEqual(len(sv.check(MON.replace(hour=7, second=1))), 1)
        self.assertIsNotNone(sv.card)
        sv.stop()

    def test_snooze(self):
        sv = self.service([alarm(7, 0, repeat=range(7))])
        sv.check(MON.replace(hour=7, second=1))
        sv.snooze()
        self.assertIsNone(sv.card)
        self.assertEqual(len(sv.snoozed), 1)
        later = dt.datetime.now() + dt.timedelta(minutes=A.SNOOZE_MIN, seconds=5)
        sv.last = later - dt.timedelta(seconds=30)                         # (the daily one isn't due then)
        self.assertEqual(len(sv.check(later)), 1)                          # again, 9 minutes on
        sv.stop()


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.clockwin")
        cls.app.register(None)

    def test_add_edit_toggle_delete(self):
        from sonata2.clock.window import ClockWindow
        A.save([])
        w = ClockWindow(self.app)
        w.present()
        settle()
        self.assertTrue(w.empty.get_visible())
        pop = w.edit(A.new(8, 0), w.add_btn, new=True)
        pop.hour.set_value(6)
        pop.minute.set_value(45)
        pop.days[0].set_active(True)
        pop.label.set_text("Work")
        pop.save()
        settle()
        [a] = A.load()
        self.assertEqual((a["hour"], a["minute"], a["repeat"], a["label"]), (6, 45, [0], "Work"))
        row = w.list.get_row_at_index(0)
        row.switch.set_active(False)
        settle()
        self.assertFalse(A.load()[0]["enabled"])
        w.delete(a["id"])
        settle()
        self.assertEqual(A.load(), [])
        self.assertTrue(w.empty.get_visible())
        w.destroy()

    def test_time_picker_two_digits_and_own_icon(self):
        """The hour/minute fields wrapped their big digits; Clock has its own icon."""
        from sonata2.clock import window as W
        w = W.ClockWindow(self.app)
        w.present()
        settle()
        pop = w.edit(A.new(8, 5), w.add_btn, new=True)
        self.assertEqual((pop.hour.get_width_chars(), pop.minute.get_max_width_chars()), (2, 2))
        pop.popdown()
        with mock.patch("sonata2.apps.write_desktop_file", side_effect=lambda _n, text: text):
            self.assertIn("Icon=sonata-clock\n", W.clock_desktop_file("sonata2"))
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(W.__file__), "..", "data", "icons", "Sonata",
                                                    "apps", "scalable", "sonata-clock.svg")))
        w.destroy()


class SoundTest(unittest.TestCase):
    """The default sound was the freedesktop alarm: high and sharp (Vini).
    Soft sounds now, chosen per alarm, louder step by step."""

    def test_sounds_exist_default_soft(self):
        self.assertEqual(A.SOUND, "morning")
        for key in A.SOUNDS:
            self.assertTrue(os.path.isfile(A.sound_path(key)), key)
        self.assertEqual(A.sound_path("nope"), A.sound_path(A.SOUND))
        self.assertEqual(A.new()["sound"], "morning")

    def test_old_alarms_get_the_default(self):
        A.save([{"hour": 7, "minute": 0}])                  # saved before sounds existed
        self.assertEqual(A.load()[0]["sound"], A.SOUND)

    def test_ramp_then_loop(self):
        from sonata2.shell.alarmservice import RAMP, ring_command
        cmd = ring_command("/s.oga", "pw-play")
        self.assertIn(f"--volume {RAMP[0]}", cmd[2])
        self.assertIn("while :", cmd[2])
        self.assertLess(cmd[2].index(f"--volume {RAMP[0]}"), cmd[2].index("while :"))
        once = ring_command("/s.oga", "paplay", loop=False)
        self.assertNotIn("while", once[2])
        self.assertIn("paplay --volume", once[2])

    def test_picker_saves_the_sound(self):
        from sonata2.clock.window import ClockWindow
        Adw.init()
        ui.setup()
        app = Adw.Application(application_id="io.test.clocksound")
        app.register(None)
        A.save([])
        w = ClockWindow(app)
        w.present()
        settle()
        with mock.patch.object(ClockWindow, "preview") as preview:
            pop = w.edit(A.new(7, 0), w.add_btn, new=True)
            pop.sound.set_selected(list(A.SOUNDS).index("chimes"))
            preview.assert_called_with("chimes")                # a preview of the pick
            pop.save()
        settle()
        self.assertEqual(A.load()[0]["sound"], "chimes")
        w.destroy()


if __name__ == "__main__":
    unittest.main()
