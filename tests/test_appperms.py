"""Ask before apps use things (Vini): a new Flatpak app, at its first open,
asks about the microphone / network / home folder (unchecked = refused);
the camera and location are asked by Sonata's Access portal; with the
switch off everything is granted without asking. Apps already installed
count as seen; each app is asked once."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import appperms as P, config  # noqa: E402
from sonata2.backend import appmanage  # noqa: E402

FLATPAK_PATH = "/var/lib/flatpak/exports/share/applications/com.discordapp.Discord.desktop"


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class Info:
    def __init__(self, path=FLATPAK_PATH):
        self.path = path

    def get_filename(self):
        return self.path

    def get_display_name(self):
        return "Discord"


WANTS = [("camera", "Camera", True), ("microphone", "Microphone", True), ("network", "Network", True),
         ("files", "Home Folder", False)]


class GateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        config.save(P.NAME, {"ask": True, "reviewed": []})

    def gate(self, info=None):
        ran = []
        with mock.patch.object(appmanage, "permissions", return_value=WANTS), \
                mock.patch.object(P, "ask") as ask:
            took = P.review_gate(info or Info(), lambda: ran.append(1))
        return took, ask, ran

    def test_new_flatpak_asks_once(self):
        took, ask, _ = self.gate()
        self.assertTrue(took)
        wants = ask.call_args[0][2]
        self.assertEqual(wants, [("microphone", "Microphone"), ("network", "Network")])   # what it wants, asked now
        self.assertEqual(self.gate()[:1], (False,))                   # second open: no question

    def test_not_asking_grants(self):
        P.set_asking(False)
        took, ask, _ = self.gate()
        self.assertFalse(took)
        ask.assert_not_called()

    def test_packages_never_asked(self):
        took, ask, _ = self.gate(Info("/usr/share/applications/discord.desktop"))
        self.assertFalse(took)
        ask.assert_not_called()

    def test_installed_before_count_as_seen(self):
        config.save(P.NAME, {"ask": True})
        with mock.patch.object(P, "installed_flatpaks", return_value=["com.discordapp.Discord"]):
            self.assertFalse(self.gate()[0])

    def test_unchecked_is_refused_then_opens(self):
        owner = SimpleNamespace(kind="flatpak", name="com.discordapp.Discord")
        ran = []
        with mock.patch("sonata2.ui.dialog.alert") as alert:
            alert.return_value = mock.Mock()
            dlg = P.ask(Info(), owner, [("microphone", "Microphone"), ("network", "Network")],
                        lambda: ran.append(1))
        boxes = dlg.set_extra_child.call_args[0][0]
        checks = []
        c = boxes.get_first_child()
        while c is not None:
            checks.append(c)
            c = c.get_next_sibling()
        self.assertTrue(all(isinstance(c, Gtk.CheckButton) and c.get_active() for c in checks))
        checks[0].set_active(False)                                  # no microphone
        with mock.patch.object(appmanage, "set_permission") as setp:
            alert.call_args[0][3]("open")
            settle(300)
        setp.assert_called_once_with(owner, "microphone", False)
        self.assertEqual(ran, [1])

    def test_dont_open(self):
        ran = []
        with mock.patch("sonata2.ui.dialog.alert") as alert:
            P.ask(Info(), None, [("network", "Network")], lambda: ran.append(1))
        alert.call_args[0][3]("cancel")
        settle(100)
        self.assertEqual(ran, [])

    def test_launch_wrapper_reviews_after_the_lock(self):
        import inspect
        from sonata2 import apps
        src = inspect.getsource(apps._gpu_aware)
        self.assertLess(src.index("applock.gate"), src.index("appperms.review_gate"))


class AccessTest(unittest.TestCase):
    def setUp(self):
        config.save(P.NAME, {"ask": True, "reviewed": []})

    def test_asks_and_answers(self):
        got = []
        with mock.patch("sonata2.ui.dialog.alert") as alert:
            P.access_answer("Allow Discord to use the camera?", "", "", {}, got.append)
        respond = alert.call_args[0][3]
        respond("grant")
        respond("deny")
        self.assertEqual(got, [0, 1])
        self.assertEqual(alert.call_args.kwargs["close"], "deny")       # Escape refuses

    def test_not_asking_grants_at_once(self):
        P.set_asking(False)
        got = []
        with mock.patch("sonata2.ui.dialog.alert") as alert:
            P.access_answer("t", "", "", {}, got.append)
        alert.assert_not_called()
        self.assertEqual(got, [0])

    def test_portal_registers_access(self):
        from sonata2 import portal
        node = Gio_node(portal.ACCESS_XML)
        self.assertEqual(node.interfaces[0].name, "org.freedesktop.impl.portal.Access")
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "install.sh")) as f:
            inst = f.read()
        self.assertIn("org.freedesktop.impl.portal.Access=sonata", inst)
        self.assertIn("org.freedesktop.impl.portal.Access;", inst)


def Gio_node(xml):
    from gi.repository import Gio
    return Gio.DBusNodeInfo.new_for_xml(xml)


if __name__ == "__main__":
    unittest.main()
