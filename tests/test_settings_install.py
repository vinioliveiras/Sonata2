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


def close(dlg):
    """emit("response") alone leaves it open (a click closes it)."""
    for m in ("force_close", "close", "destroy"):
        if hasattr(dlg, m):
            getattr(dlg, m)()
            return


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
            end = GLib.get_monotonic_time() + 5_000_000      # timeout_add_seconds is coarse
            while not done and GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
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
        self.assertIn("keyboard and mouse too", rows[0].get_subtitle())      # Vini: warned up front

    def test_usb_set_up_asks_first(self):
        """Vini: a warning before it runs -- USBGuard alone blocks the keyboard too."""
        s = self.settings()
        with mock.patch.object(U, "status", return_value="missing"), \
                mock.patch("sonata2.files.packages.family", return_value="arch"), \
                mock.patch.object(system, "run_async", side_effect=lambda fn, cb, *a: cb(fn(*a))):
            g = s._usb_group({})
        win = Gtk.Window()
        win.set_child(g)
        win.present()
        row = self.rows(g)[0]
        with mock.patch.object(system, "run_in_terminal", return_value=True) as rt:
            row.button.emit("clicked")
            settle(300)
            rt.assert_not_called()
            self.assertIn("keyboard and mouse", row.confirm.get_body())
            row.confirm.emit("response", "cancel")
            close(row.confirm)
            settle(100)
            rt.assert_not_called()
            row.button.emit("clicked")
            settle(300)
            row.confirm.emit("response", "go")
            close(row.confirm)
            settle(100)
            rt.assert_called_once_with(U.setup_command("arch"))
        win.destroy()
        with mock.patch.object(U, "status", return_value="ready"), \
                mock.patch.object(system, "run_async", side_effect=lambda fn, cb, *a: cb(fn(*a))):
            g = s._usb_group({"usb_protection": True})
        self.assertEqual(self.rows(g), [])


class BazaarTest(unittest.TestCase):
    """Vini: Bazaar (the app store) comes with Sonata."""
    def run_block(self, installed, flag="1"):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent / "install.sh").read_text()
        start = src.index("# -- Bazaar, the app store")
        block = src[start:src.index("# -- Sonata's login screen", start)]
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "flatpak"), "w") as f:
                f.write(f'#!/bin/sh\necho "flatpak $*" >> {d}/log\n'
                        f'[ "$1" = info ] && exit {0 if installed else 1}\nexit 0\n')
            with open(os.path.join(d, "sudo"), "w") as f:
                f.write('#!/bin/sh\nexec "$@"\n')
            for n in ("flatpak", "sudo"):
                os.chmod(os.path.join(d, n), 0o755)
            r = subprocess.run(["bash", "-c", f"set -euo pipefail\nBAZAAR={flag}\n" + block],
                               env=dict(os.environ, PATH=d + ":" + os.environ["PATH"]), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            log = open(os.path.join(d, "log")).read() if os.path.exists(os.path.join(d, "log")) else ""
            return r.stdout, log

    def test_installed_from_flathub(self):
        out, log = self.run_block(False)
        self.assertIn("remote-add --if-not-exists flathub", log)
        self.assertIn("install -y --noninteractive flathub io.github.kolunmi.Bazaar", log)
        self.assertIn("Bazaar: installed", out)

    def test_already_there_or_skipped(self):
        out, log = self.run_block(True)
        self.assertNotIn("install", log.replace("info", ""))
        out, log = self.run_block(False, "0")
        self.assertEqual(log, "")


class InstallShTest(unittest.TestCase):
    """Vini: what Settings would ask to install comes with install.sh."""
    def setUp(self):
        import pathlib
        self.root = pathlib.Path(__file__).resolve().parent.parent
        self.src = (self.root / "install.sh").read_text()

    def test_packages(self):
        arch = next(ln for ln in self.src.splitlines() if ln.strip().startswith('OPT="vte4 '))
        for p in ("wlsunset", "swayidle", "ufw", "cups", "wayvnc", "usbguard", "openrgb", "flatpak"):
            self.assertIn(f" {p}", arch, p)

    def test_usb_setup_block(self):
        start = self.src.index("# -- what Settings would otherwise ask to install")
        block = self.src[start:self.src.index("# -- Bazaar", start)]
        self.assertIn("keyboard and mouse included", block)          # warned
        self.assertIn("from sonata2.backend.usbprotect import SETUP", block)   # one set-up, not a copy
        with tempfile.TemporaryDirectory() as d:
            for name, body in (("sudo", f'echo "$*" >> {d}/sudo.log'), ("usbguard", "true"),
                               ("systemctl", "exit 1")):
                with open(os.path.join(d, name), "w") as f:
                    f.write("#!/bin/sh\n" + body + "\n")
                os.chmod(os.path.join(d, name), 0o755)
            import sys
            os.symlink(sys.executable, os.path.join(d, "python3"))     # the one with PyGObject, as on a desktop
            script = f'set -euo pipefail\nSRC={self.root}\nUSBGUARD=1\n' + block.replace(
                "USB_RULE=/etc/", f"USB_RULE={d}/")
            env = dict(os.environ, PATH=d + ":" + os.environ["PATH"])
            r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("USB protection: on", r.stdout)
            log = open(os.path.join(d, "sudo.log")).read()
            self.assertIn("ImplicitPolicyTarget=allow", log)
            r = subprocess.run(["bash", "-c", script.replace("USBGUARD=1", "USBGUARD=0")], env=env,
                               capture_output=True, text=True)
            self.assertNotIn("USB protection", r.stdout)                # --no-usbguard


if __name__ == "__main__":
    unittest.main()
