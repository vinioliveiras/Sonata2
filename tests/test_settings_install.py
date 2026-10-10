"""Vini: everything Settings needs installed has a button that opens
Terminal with the command running; USBGuard was installed and still didn't
work (services off, its default blocks every device, polkit refuses the
user) -- Set Up does all of it."""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2.backend import system, usbprotect as U  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class InstallCommandTest(unittest.TestCase):
    def test_families(self):
        self.assertEqual(system.install_command("ufw", "arch"), "sudo pacman -S --needed ufw")
        self.assertEqual(system.install_command("ufw", "debian"), "sudo apt install ufw")
        self.assertEqual(system.install_command("ufw", "rpm"), "sudo dnf install ufw")
        self.assertIsNone(system.install_command("ufw", ""))
        self.assertEqual(system.install_command({"arch": "a", "debian": "b"}, "debian"), "sudo apt install b")
        self.assertIsNone(system.install_command({"arch": "a"}, "rpm"))


class UsbSetupTest(unittest.TestCase):
    def test_setup_script_in_a_fake_root(self):
        """The root part, run against a temporary /etc with systemctl faked."""
        with tempfile.TemporaryDirectory() as root:
            etc = os.path.join(root, "etc")
            os.makedirs(os.path.join(etc, "usbguard"))
            with open(os.path.join(etc, "usbguard", "usbguard-daemon.conf"), "w") as f:
                f.write("RuleFile=/etc/usbguard/rules.conf\nImplicitPolicyTarget=block\nPresentDevicePolicy=apply-policy\n")
            bin_ = os.path.join(root, "bin")
            os.makedirs(bin_)
            with open(os.path.join(bin_, "systemctl"), "w") as f:
                f.write(f'#!/bin/sh\necho "$*" >> {root}/systemctl.log\n')
            os.chmod(os.path.join(bin_, "systemctl"), 0o755)
            script = U.SETUP.replace("/etc/", etc + "/")
            r = subprocess.run(["sh", "-c", script], env=dict(os.environ, PATH=bin_ + ":" + os.environ["PATH"]),
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            conf = open(os.path.join(etc, "usbguard", "usbguard-daemon.conf")).read()
            self.assertIn("ImplicitPolicyTarget=allow", conf)          # the keyboard is never blocked unlocked
            self.assertIn("PresentDevicePolicy=apply-policy", conf)
            rules = os.path.join(etc, "usbguard", "rules.conf")
            self.assertEqual(os.stat(rules).st_mode & 0o777, 0o600)
            rule = open(os.path.join(etc, "polkit-1", "rules.d", "70-sonata2-usbguard.rules")).read()
            self.assertEqual(rule.strip(), U.RULE.strip())
            log = open(os.path.join(root, "systemctl.log")).read()
            self.assertIn("enable usbguard.service usbguard-dbus.service", log)
            self.assertIn("restart usbguard.service usbguard-dbus.service", log)

    @unittest.skipUnless(shutil.which("node"), "no node")
    def test_polkit_rule_allows_the_policy_only(self):
        js = ("var rule; var polkit = {addRule: function(f) { rule = f; }, Result: {YES: 'yes'}};\n" + U.RULE +
              "\nfunction s(g, act) { return {active: act, local: true, isInGroup: function(x) { return x == g; }}; }\n"
              "console.log([rule({id: 'org.usbguard1.setParameter'}, s('wheel', true)),"
              " rule({id: 'org.usbguard1.getParameter'}, s('sudo', true)),"
              " rule({id: 'org.usbguard.Policy1.appendRule'}, s('wheel', true)),"
              " rule({id: 'org.usbguard1.setParameter'}, s('users', true)),"
              " rule({id: 'org.usbguard1.setParameter'}, s('wheel', false))].join(','));")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "yes,yes,,,")

    def test_whole_command(self):
        c = U.setup_command("arch")
        self.assertTrue(c.startswith("sudo pacman -S --needed usbguard && sudo sh -c "))
        self.assertEqual(subprocess.run(["sh", "-n", "-c", c]).returncode, 0)
        self.assertIsNone(U.setup_command(""))


class UsbStatusTest(unittest.TestCase):
    def test_states(self):
        with mock.patch.object(U, "available", return_value=False), mock.patch.object(U, "installed", return_value=False):
            self.assertEqual(U.status(), "missing")
        with mock.patch.object(U, "available", return_value=False), mock.patch.object(U, "installed", return_value=True):
            self.assertEqual(U.status(), "off")

        def bus(err=None):
            b = mock.Mock()
            if err:
                b.call_sync.side_effect = GLib.Error(err)
            else:
                b.call_sync.return_value.unpack.return_value = ["apply-policy"]
            return b
        with mock.patch.object(U, "available", return_value=True):
            with mock.patch.object(U, "_bus", return_value=bus("GDBus.Error:org.freedesktop.DBus.Error."
                                                               "AccessDenied: Not authorized")):
                self.assertEqual(U.status(), "denied")
            with mock.patch.object(U, "_bus", return_value=bus("Timeout was reached")):
                self.assertEqual(U.status(), "off")
            b = bus()
            with mock.patch.object(U, "_bus", return_value=b):
                self.assertEqual(U.status(), "ready")
            set_call = b.call_sync.call_args_list[-1][0]
            self.assertEqual(set_call[3], "setParameter")
            self.assertEqual(set_call[4].unpack(), ("InsertedDevicePolicy", "apply-policy"))   # what it was

    def test_activatable_counts(self):
        """Not running yet but started by the bus when asked."""
        with mock.patch.object(U, "_bus_call", side_effect=lambda m, a, r: False if m == "NameHasOwner"
                               else ["org.usbguard1"]):
            self.assertTrue(U.available())
        with mock.patch.object(U, "_bus_call", side_effect=lambda m, a, r: False if m == "NameHasOwner" else []):
            self.assertFalse(U.available())


class InstallRowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def test_button_opens_terminal_and_refreshes(self):
        from sonata2.settings import app as st
        done = []
        state = {"ok": False}
        row = st.install_row("Needs x", "Not installed", "sudo pacman -S --needed x", lambda: state["ok"],
                             lambda: done.append(1))
        win = Gtk.Window()
        g = Adw.PreferencesGroup()
        g.add(row)
        win.set_child(g)
        win.present()
        with mock.patch.object(st.system, "run_in_terminal", return_value=True) as rt, \
                mock.patch.object(st, "INSTALL_POLL_S", 1):
            row.button.emit("clicked")
            rt.assert_called_once_with("sudo pacman -S --needed x")
            state["ok"] = True
            settle(1500)
        self.assertEqual(done, [1])
        win.destroy()

    def test_unknown_distro_no_button(self):
        from sonata2.settings import app as st
        row = st.install_row("Needs x", "Not installed", None)
        self.assertFalse(hasattr(row, "button"))


class PagesTest(unittest.TestCase):
    """Every "needs X installed" row in Settings has the button."""
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def rows(self, groups):
        out = []
        for g in groups if isinstance(groups, list) else [groups]:
            stack = [g]
            while stack:
                w = stack.pop()
                if getattr(w, "command", None):
                    out.append(w)
                c = w.get_first_child()
                while c is not None:
                    stack.append(c)
                    c = c.get_next_sibling()
        return out

    def settings(self):
        from sonata2.settings import app as st
        s = st.Settings.__new__(st.Settings)
        s._reload_page = mock.Mock()
        s._save = mock.Mock()
        return s

    def test_missing_tools(self):
        from sonata2.backend import security, screenshare
        s = self.settings()
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("sonata2.files.packages.family", return_value="arch"):
            cmds = [r.command for r in self.rows(s._night_shift_group())]
            self.assertEqual(cmds, ["sudo pacman -S --needed wlsunset"])
            with mock.patch.object(security, "firewall", return_value={"kind": None, "on": False}):
                self.assertEqual([r.command for r in self.rows(s._firewall_group())], ["sudo pacman -S --needed ufw"])
            with mock.patch.object(screenshare, "installed", return_value=False):
                self.assertEqual([r.command for r in self.rows(s._screen_sharing_group())],
                                 ["sudo pacman -S --needed wayvnc"])

    def test_usb_set_up(self):
        s = self.settings()
        with mock.patch.object(U, "status", return_value="off"), \
                mock.patch("sonata2.files.packages.family", return_value="arch"), \
                mock.patch.object(system, "run_async", side_effect=lambda fn, cb, *a: cb(fn(*a))):
            g = s._usb_group({})
        rows = self.rows(g)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].command, U.setup_command("arch"))
        self.assertEqual(rows[0].button.get_label(), "Set Up…")
        with mock.patch.object(U, "status", return_value="ready"), \
                mock.patch.object(system, "run_async", side_effect=lambda fn, cb, *a: cb(fn(*a))):
            g = s._usb_group({"usb_protection": True})
        self.assertEqual(self.rows(g), [])


if __name__ == "__main__":
    unittest.main()
