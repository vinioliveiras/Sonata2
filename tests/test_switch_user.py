"""Switch User (Vini: with another user added, the lock screen had no way
to switch to them). userswitch.py decides; the lock screen shows the link;
the login screen goes back to a user's running session instead of a second
login; greeter-setup.sh installs the second greetd and its polkit rule.

    xvfb-run -a python3 -m unittest tests.test_switch_user
"""
import os
import types
import unittest
from unittest import mock

from sonata2 import userswitch as U

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sess(sid, user, cls="user", kind="wayland", seat="seat0", state="active", remote=False):
    return {"id": sid, "user": user, "uid": 1000, "seat": seat, "type": kind, "class": cls,
            "active": state == "active", "state": state, "remote": remote, "path": f"/s/{sid}"}


def user(name):
    return types.SimpleNamespace(name=name, real=name.title())


class SessionsTest(unittest.TestCase):
    def test_graphical_session_of_a_user(self):
        listed = [sess("c1", "greeter", cls="greeter"), sess("3", "ana", kind="tty"),
                  sess("4", "ana", remote=True), sess("5", "ana", seat="seat1"), sess("6", "ana", state="closing"),
                  sess("7", "ana", state="online")]
        self.assertEqual(U.graphical_session("ana", listed)["id"], "7")      # in the background: still hers
        self.assertIsNone(U.graphical_session("vini", listed))
        self.assertEqual(U.greeter_session(listed)["id"], "c1")
        self.assertIsNone(U.greeter_session([sess("2", "vini")]))

    def test_login_method(self):
        no = lambda *_a: False                                                   # noqa: E731
        self.assertEqual(U.login_method(lambda p: p == U.SWITCH_UNIT_FILE, lambda c: None), "sonata")
        self.assertEqual(U.login_method(no, lambda c: "/usr/bin/x" if c == "gdmflexiserver" else None), "gdm")
        self.assertEqual(U.login_method(no, lambda c: "/usr/bin/x" if c == "dm-tool" else None), "lightdm")
        self.assertIsNone(U.login_method(no, lambda c: None))


class AvailableTest(unittest.TestCase):
    def test_alone_never(self):
        self.assertFalse(U.available("vini", [user("vini")], method="sonata"))

    def test_another_user_and_a_login_screen(self):
        self.assertTrue(U.available("vini", [user("vini"), user("ana")], method="sonata"))

    def test_without_a_login_screen_only_to_a_running_session(self):
        everyone = [user("vini"), user("ana")]
        self.assertFalse(U.available("vini", everyone, method="", listed=[sess("2", "vini")]))
        self.assertTrue(U.available("vini", everyone, method="", listed=[sess("2", "vini"), sess("3", "ana")]))


class SwitchTest(unittest.TestCase):
    def test_sonata_starts_the_switch_greeter(self):
        with mock.patch.object(U, "sessions", return_value=[sess("2", "vini")]), \
                mock.patch.object(U, "_unit", return_value=True) as unit, \
                mock.patch.object(U, "activate") as act:
            self.assertTrue(U.switch("vini", [user("vini"), user("ana")], method="sonata", bus=object()))
        unit.assert_called_once()
        self.assertEqual(unit.call_args[0][0], "start")
        act.assert_not_called()

    def test_sonata_switch_greeter_already_up(self):
        """Switched away from the login screen earlier: back to it, not a third greetd."""
        with mock.patch.object(U, "sessions", return_value=[sess("2", "vini"), sess("c3", "greeter", cls="greeter")]), \
                mock.patch.object(U, "_unit") as unit, mock.patch.object(U, "activate", return_value=True) as act:
            self.assertTrue(U.show_login_screen("sonata", bus=object()))
        unit.assert_not_called()
        self.assertEqual(act.call_args[0][0], "c3")

    def test_gdm_and_lightdm(self):
        for method, cmd in (("gdm", ["gdmflexiserver"]), ("lightdm", ["dm-tool", "switch-to-greeter"])):
            with mock.patch.object(U.subprocess, "Popen") as popen:
                self.assertTrue(U.show_login_screen(method))
            self.assertEqual(popen.call_args[0][0], cmd)

    def test_no_login_screen_goes_to_the_other_session(self):
        with mock.patch.object(U, "sessions", return_value=[sess("2", "vini"), sess("3", "ana")]), \
                mock.patch.object(U, "activate", return_value=True) as act:
            self.assertTrue(U.switch("vini", [user("vini"), user("ana")], method="", bus=object()))
        self.assertEqual(act.call_args[0][0], "3")

    def test_leave_only_from_the_switch_greeter(self):
        with mock.patch.object(U, "_unit") as unit, mock.patch.dict(os.environ, {U.SWITCH_ENV: ""}):
            U.leave_switch_greeter()
        unit.assert_not_called()
        with mock.patch.object(U, "_unit") as unit, mock.patch.dict(os.environ, {U.SWITCH_ENV: "1"}):
            U.leave_switch_greeter()
        unit.assert_called_once_with("stop", None)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class LockScreenTest(unittest.TestCase):
    def column_buttons(self, switchable):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tests"))
        from gi.repository import Gtk
        from sonata2.shell import lock as L
        ls = L.LockScreen.__new__(L.LockScreen)
        ls._switchable = switchable
        ls._mirrors()
        col = ls._login()["column"]
        found, stack = [], [col]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Button):
                found.append(w.get_label())
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        return ls, found

    def test_link_with_another_user(self):
        from sonata2.i18n import _
        ls, found = self.column_buttons(True)
        self.assertIn(_("Switch User"), found)

    def test_no_link_alone(self):
        from sonata2.i18n import _
        _ls, found = self.column_buttons(False)
        self.assertNotIn(_("Switch User"), found)

    def test_asks_userswitch(self):
        from sonata2.shell import lock as L
        ls = L.LockScreen.__new__(L.LockScreen)
        with mock.patch.object(U, "available", return_value=True) as av:
            self.assertTrue(ls._can_switch())
            self.assertTrue(ls._can_switch())
        av.assert_called_once()                                                  # once per lock

    def test_switch_failure_says_so(self):
        from gi.repository import GLib
        from sonata2.i18n import _
        ls, _found = self.column_buttons(True)
        with mock.patch.object(U, "switch", return_value=False):
            ls._switch_user()
            ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + 500_000
            while GLib.get_monotonic_time() < end:
                ctx.iteration(False)
        self.assertEqual(ls.hint.items[0].get_label(), _("Couldn't switch user"))


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class GreeterResumeTest(unittest.TestCase):
    """Vini: lock, Switch User, back to the same user -- the password was
    asked twice (the login screen, then the lock screen). Now the login
    screen checks it once and the session comes back unlocked; a wrong one
    never unlocks anything."""

    def greeter(self, name="vini"):
        from sonata2 import greetd
        from sonata2.shell import greeter as G
        g = G.Greeter.__new__(G.Greeter)
        g.fake, g._busy, g.state = False, False, {}
        g.user = G.User(name, name.title())
        g.entry, g.links, g.progress, g.slot, g.hint, g.power = (mock.Mock() for _ in range(6))
        g.guard = mock.Mock(blocked=mock.Mock(return_value=False))
        g._session = mock.Mock(return_value=mock.Mock(key="sonata", desktops="Sonata", cmd=["sonata-session"]))
        g._show = mock.Mock()
        return G, g, greetd

    def run_login(self, g, G, greetd, pw, listed):
        g.entry.get_text.return_value = pw
        from gi.repository import GLib
        with mock.patch.object(G.greetd, "Client", side_effect=lambda: greetd.Fake()), \
                mock.patch.object(U, "sessions", return_value=listed), \
                mock.patch.object(U, "unlock", return_value=True) as unlock, \
                mock.patch.object(U, "activate", return_value=True) as act, \
                mock.patch.object(U, "leave_switch_greeter") as leave, \
                mock.patch.object(g, "_started", return_value=False) as started, \
                mock.patch.object(g, "_failed", return_value=False) as failed, \
                mock.patch.object(g, "_resumed", return_value=False):
            g._login()
            ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + 1_500_000
            while GLib.get_monotonic_time() < end and not (act.called or started.called or failed.called):
                ctx.iteration(False)
            ctx.iteration(False)
        return unlock, act, leave, started, failed

    def test_running_session_one_password(self):
        G, g, greetd = self.greeter()
        unlock, act, leave, started, failed = self.run_login(g, G, greetd, "sonata", [sess("2", "vini")])
        unlock.assert_called_once_with("2", mock.ANY)                      # its lock screen goes
        act.assert_called_once_with("2", mock.ANY)
        started.assert_not_called()                                        # no second session
        failed.assert_not_called()

    def test_wrong_password_unlocks_nothing(self):
        G, g, greetd = self.greeter()
        unlock, act, _leave, started, failed = self.run_login(g, G, greetd, "nope", [sess("2", "vini")])
        failed.assert_called_once()
        unlock.assert_not_called()
        act.assert_not_called()

    def test_not_logged_in_logs_in(self):
        G, g, greetd = self.greeter("ana")
        unlock, act, _leave, started, failed = self.run_login(g, G, greetd, "sonata", [sess("2", "vini")])
        started.assert_called_once()
        unlock.assert_not_called()

    def test_picking_a_logged_in_user_asks_the_password(self):
        G, g, _greetd = self.greeter()
        with mock.patch.object(U, "activate") as act:
            g._pick(G.User("vini", "Vini"))
        act.assert_not_called()                                            # (it used to go back without one)
        g._show.assert_called_once()

    def test_lock_screen_follows_logind_unlock(self):
        from sonata2.shell import lock as L
        src = open(L.__file__).read()
        self.assertIn('"org.freedesktop.login1.Session", "Unlock"', src)
        ls = L.LockScreen.__new__(L.LockScreen)
        ls.column, ls.power, ls.lock = mock.Mock(), mock.Mock(), mock.Mock()
        with mock.patch.object(L.GLib, "timeout_add", side_effect=lambda ms, fn: fn()):
            ls._unlocked_elsewhere()
            ls._unlocked_elsewhere()                                        # (twice: once)
        ls.lock.unlock.assert_called_once()


class SetupTest(unittest.TestCase):
    def test_greeter_setup_installs_and_reverts(self):
        src = open(os.path.join(ROOT, "tools", "greeter-setup.sh")).read()
        for part in ('vt = "next"', "SONATA_GREETER_SWITCH=1", "sonata-greeter-switch.service",
                     "60-sonata2-switch-user.rules", '$SUDO rm -f "$SWITCH_TOML" "$SWITCH_UNIT" "$SWITCH_RULE"'):
            self.assertIn(part, src)
        self.assertEqual(U.SWITCH_UNIT_FILE, "/etc/systemd/system/sonata-greeter-switch.service")

    def test_polkit_rule_is_narrow(self):
        rule = open(os.path.join(ROOT, "config", "polkit", "60-sonata2-switch-user.rules")).read()
        self.assertIn('action.lookup("unit") == "sonata-greeter-switch.service"', rule)
        self.assertIn('action.lookup("verb") == "start" || action.lookup("verb") == "stop"', rule)
        self.assertIn("subject.local", rule)


if __name__ == "__main__":
    unittest.main()
