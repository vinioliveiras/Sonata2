"""Settings > Installed Apps (Vini): every app, and one app's page -- its
size and data, permissions (Flatpak: camera, microphone, network,
location, background, notifications, home folder), Lock App, Clear Data
(to the Trash), Uninstall; back (‹) to the list."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from sonata2 import applock, config  # noqa: E402
from sonata2.backend import appmanage as A, permstore  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FakeInfo:
    def __init__(self, did="org.test.App"):
        self.did = did

    def get_id(self):
        return self.did + ".desktop"

    def get_display_name(self):
        return "Test App"

    def get_executable(self):
        return "/usr/bin/testapp"

    def get_icon(self):
        return Gio.ThemedIcon.new("application-x-executable")

    def should_show(self):
        return True

    def get_name(self):
        return "Test App"

    def get_commandline(self):
        return "testapp"

    def get_startup_wm_class(self):
        return None

    def get_filename(self):
        return None


FLATPAK = SimpleNamespace(kind="flatpak", name="org.test.App", manager="flatpak-user")
PACKAGE = SimpleNamespace(kind="package", name="testapp", manager="pacman")
SHOW = """[Application]
name=org.test.App

[Context]
shared=network;ipc;
sockets=x11;wayland;
filesystems=xdg-download;host:ro;
"""


class BackendTest(unittest.TestCase):
    def test_flatpak_permissions(self):
        with mock.patch.object(A, "_run", return_value=SHOW), \
                mock.patch.object(permstore, "lookup", lambda table, ident: {"org.test.App": ["no"]}
                                  if ident == "camera" else {}):
            got = {k: on for k, _t, on in A.permissions(FLATPAK)}
        self.assertEqual(got, {"camera": False, "microphone": False, "network": True, "location": True,
                               "background": True, "notifications": True, "files": True})

    def test_only_flatpak_has_permissions(self):
        self.assertEqual(A.permissions(PACKAGE), [])
        self.assertEqual(A.permissions(None), [])
        self.assertFalse(A.set_permission(PACKAGE, "network", False))

    def test_override_args(self):
        self.assertEqual(A.override_args("network", False), ["--unshare=network"])
        self.assertEqual(A.override_args("microphone", True), ["--socket=pulseaudio"])
        self.assertEqual(A.override_args("files", False), ["--nofilesystem=home", "--nofilesystem=host"])

    def test_set_uses_the_store_or_an_override(self):
        with mock.patch.object(permstore, "set_allowed", return_value=True) as store:
            self.assertTrue(A.set_permission(FLATPAK, "location", False))
        store.assert_called_once_with("location", "org.test.App", False)
        with mock.patch.object(A.shutil, "which", return_value="/usr/bin/flatpak"), \
                mock.patch.object(A.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(A.set_permission(FLATPAK, "network", False))
        self.assertEqual(run.call_args[0][0], ["flatpak", "override", "--user", "--unshare=network",
                                               "org.test.App"])

    def test_sizes(self):
        self.assertEqual(A._human_size("312.4 MB"), int(312.4 * 1024 ** 2))
        self.assertEqual(A._human_size("1,5 GiB"), int(1.5 * 1024 ** 3))
        self.assertEqual(A._human_size("12 KiB"), 12 * 1024)
        self.assertEqual(A._human_size(""), 0)
        with mock.patch.object(A, "_run", return_value="Name : testapp\nInstalled Size  : 2.00 MiB\n"):
            self.assertEqual(A.app_size(PACKAGE), 2 * 1024 ** 2)

    def test_data_dirs_and_clear(self):
        home = tempfile.mkdtemp()
        cfg, cache = os.path.join(home, ".config"), os.path.join(home, ".cache")
        for d in (os.path.join(cfg, "testapp"), os.path.join(cache, "org.test.App"), os.path.join(cfg, "other")):
            os.makedirs(d)
        with open(os.path.join(cfg, "testapp", "a"), "w") as f:
            f.write("x" * 10)
        with mock.patch.object(A.GLib, "get_home_dir", return_value=home), \
                mock.patch.object(A.GLib, "get_user_config_dir", return_value=cfg), \
                mock.patch.object(A.GLib, "get_user_data_dir", return_value=os.path.join(home, ".local/share")), \
                mock.patch.object(A.GLib, "get_user_cache_dir", return_value=cache):
            dirs = A.data_dirs(FakeInfo(), PACKAGE)
            self.assertEqual(sorted(dirs), sorted([os.path.join(cfg, "testapp"), os.path.join(cache, "org.test.App")]))
            self.assertEqual(A.folder_size(dirs), 10)
            os.makedirs(os.path.join(home, ".var/app/org.test.App"))
            self.assertEqual(A.data_dirs(FakeInfo(), FLATPAK), [os.path.join(home, ".var/app/org.test.App")])
        self.assertEqual(A.data_dirs(FakeInfo("io.github.vinioliveiras.sonata2.files"), PACKAGE), [])
        with mock.patch.object(A.Gio.File, "new_for_path") as f:
            self.assertEqual(A.clear_data(dirs), [])
        self.assertEqual(f.return_value.trash.call_count, 2)       # the Trash, never deleted


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        from sonata2.settings import app as st, applist
        config.save(applock.NAME, {})
        self.patches = [mock.patch.object(applist, "app_list", return_value=[("org.test.App", FakeInfo())])]
        for p in self.patches:
            p.start()
        self.w = st.Settings(None, "apps")
        self.w.present()
        self.w.select("apps", from_sidebar=True)          # (an old id: the Apps section)
        settle(300)
        self.page = self.w.apps_page

    def tearDown(self):
        self.w.destroy()
        for p in self.patches:
            p.stop()

    def open(self, owner=FLATPAK, perms=None):
        res = {"owner": owner, "size": 5 * 1024 ** 2, "dirs": ["/home/x/.var/app/org.test.App"],
               "data": 1024 ** 2, "perms": perms if perms is not None else
               [("camera", "Camera", False), ("network", "Network", True)]}
        with mock.patch.object(self.page, "_look", return_value=res):
            self.page.open(self.page.rows["org.test.App"])
            settle(300)
        return self.w.pages["launchpad"]

    def test_one_apps_section(self):
        """Vini: Installed Apps and Apps are one section, Apps."""
        from sonata2.settings import app as st
        self.assertNotIn("apps", [s[0] for s in st.SECTIONS])
        self.assertEqual(st.section_of("apps"), "launchpad")
        self.assertIn("apps", st.parts_of("launchpad"))
        self.assertEqual(self.w.current, "launchpad")
        self.assertIn("uninstall", st.KEYWORDS["launchpad"])

    def test_focus_an_app(self):
        with mock.patch.object(self.page, "focus") as focus:
            self.w.select("apps/org.test.App", from_sidebar=True)
        focus.assert_called_once_with("org.test.App")

    def test_detail_and_back(self):
        tv = self.open()
        self.assertIs(tv.get_content(), tv.detail)
        self.assertEqual(tv.title.get_label(), "Test App")
        self.assertTrue(tv.back.get_visible())
        app = self.page.app
        self.assertIn("Flatpak", app["hero"].get_subtitle())
        self.assertIn("data", app["hero"].get_subtitle())
        self.assertTrue(self.page.clear_btn.get_sensitive())
        self.assertTrue(self.page.rm_btn.get_sensitive())
        tv.back.emit("clicked")
        self.assertIs(tv.get_content(), tv.main)
        self.assertEqual(tv.title.get_label(), "Apps")
        self.assertFalse(tv.back.get_visible())

    def test_permission_switch_changes_it(self):
        self.open()
        with mock.patch.object(A, "set_permission", return_value=True) as setp:
            self.page.set_permission(self.page.app, "network", False)
            settle(200)
        self.assertEqual(setp.call_args[0][:3], (FLATPAK, "network", False))

    def test_sonatas_own_apps_have_none(self):
        self.open(owner=PACKAGE, perms=[])
        self.assertIn("Part of Sonata", self._texts(self.page.app["perms"]))

    def test_web_app_can_be_uninstalled(self):
        """Vini: web apps can be uninstalled too (from Settings > Apps, the Dock's Trash...)."""
        from sonata2 import webapps
        info = FakeInfo(webapps.desktop_id("wabc1234567"))
        row = self.page.rows["org.test.App"]
        row.info = info
        res = {"owner": None, "size": None, "dirs": [], "data": 0, "perms": []}
        with mock.patch.object(self.page, "_look", return_value=res):
            self.page.open(row)
            settle(300)
        self.assertTrue(self.page.rm_btn.get_sensitive())
        self.assertIn("Web App", self.page.app["hero"].get_subtitle())
        self.assertIn("Asked by the site", self._texts(self.page.app["perms"]))
        from sonata2.shell import uninstall_ui
        with mock.patch("sonata2.ui.dialog.alert") as alert, \
                mock.patch.object(webapps, "remove") as remove, mock.patch("sonata2.sounds.play"):
            self.page.ask_uninstall()
            self.assertIn("Delete", alert.call_args[0][0])
            alert.call_args[0][3]("delete")
        remove.assert_called_once_with("wabc1234567")
        self.assertNotIn("org.test.App", self.page.rows)            # gone from the list
        self.assertIsNone(getattr(self.w.pages["launchpad"], "detail", None))
        self.assertTrue(uninstall_ui.ask_webapp)

    def test_web_app_data_is_its_own(self):
        from sonata2 import webapps
        d = tempfile.mkdtemp()
        with mock.patch.object(webapps, "data_dir", return_value=d):
            self.assertEqual(A.data_dirs(FakeInfo(webapps.desktop_id("wabc1234567")), None), [d])

    def test_packaged_app_permissions(self):
        """Vini: an app's permissions managed in it -- packaged apps too."""
        self.open(owner=PACKAGE, perms=A.permissions(PACKAGE, FakeInfo()))
        texts = self._texts(self.page.app["perms"])
        for t in ("Camera", "Sound & Microphone", "Network", "Home Folder"):
            self.assertIn(t, texts)
        self.assertIn("opened from Sonata", self.page.app["perms"].get_description())
        with mock.patch.object(A, "set_permission", wraps=A.set_permission) as setp:
            self.page.set_permission(self.page.app, "network", False)
            settle(200)
        self.assertEqual(setp.call_args[0][:3], (PACKAGE, "network", False))
        from sonata2 import appperms
        self.assertEqual(appperms.limits("org.test.App.desktop"), ["network"])
        appperms.set_limit("org.test.App", "network", False)
        self.assertEqual(appperms.limits("org.test.App"), [])

    def test_permissions_live_in_apps(self):
        """Vini: app permission options belong in Apps, not Security & Privacy."""
        import inspect
        from sonata2.settings import app as st
        self.assertIn("_permissions_group", inspect.getsource(st.Settings._page_apps))
        self.assertNotIn("_permissions_group", inspect.getsource(st.Settings._page_privacy))

    def _texts(self, grp):
        out, stack = [], [grp]
        while stack:
            w = stack.pop()
            if isinstance(w, Adw.ActionRow):
                out += [w.get_title(), w.get_subtitle() or ""]
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        return " ".join(out)

    def test_clear_asks_then_trashes(self):
        self.open()
        with mock.patch("sonata2.ui.dialog.alert") as alert:
            self.page.ask_clear()
        self.assertIn(".var/app/org.test.App", alert.call_args[0][1])
        on_response = alert.call_args[0][3]
        with mock.patch.object(A, "clear_data", return_value=[]) as clear:
            on_response("clear")
            settle(200)
        clear.assert_called_once_with(["/home/x/.var/app/org.test.App"])

    def test_lock_switch(self):
        self.open()
        lock = self.page.app["lock"]
        lock.set_active(True)
        self.assertTrue(applock.locked("org.test.App.desktop"))
        with mock.patch.object(applock, "ask_unlock") as ask:
            lock.set_active(False)
        self.assertTrue(lock.get_active())                    # stays on until the password
        ask.call_args[0][1]()
        self.assertFalse(lock.get_active())

    def test_same_section_clicked_again_returns_to_the_list(self):
        """Vini: in an app's page, clicking Apps in the sidebar goes back to Apps."""
        tv = self.open()
        self.w.listbox.emit("row-activated", self.w.rows["launchpad"])
        self.assertIs(tv.get_content(), tv.main)
        self.assertFalse(tv.back.get_visible())

    def test_another_section_returns_to_the_list(self):
        tv = self.open()
        self.w.select("dock", from_sidebar=True)
        settle(100)
        # (Apps is rebuilt when left: its Hidden part locks again) -- either way, the list
        self.assertTrue("launchpad" not in self.w.pages or tv.get_content() is tv.main)
        self.w.select("launchpad", from_sidebar=True)
        new = self.w.pages["launchpad"]
        self.assertIs(new.get_content(), new.main)


if __name__ == "__main__":
    unittest.main()


class PermissionsSpinnerTest(PageTest):
    """Vini: an app's permissions took a moment -- a spinner meanwhile."""
    def test_spinner_until_read(self):
        from sonata2.backend import system
        from sonata2.ui.progress import Spinner
        pending = []
        with mock.patch.object(system, "run_async", side_effect=lambda fn, cb, *a: pending.append((fn, cb, a))):
            self.page.open(self.page.rows["org.test.App"])
            settle(200)
        perms = self.page.app["perms"]

        def find(kind):
            out, stack = [], [perms]
            while stack:
                w = stack.pop()
                if isinstance(w, kind):
                    out.append(w)
                c = w.get_first_child()
                while c is not None:
                    stack.append(c)
                    c = c.get_next_sibling()
            return out
        self.assertTrue(find(Spinner))
        self.assertIn("Checking permissions…", [r.get_title() for r in find(Adw.ActionRow)])
        res = {"owner": FLATPAK, "size": 1, "dirs": [], "data": 0, "perms": [("camera", "Camera", False)]}
        pending[0][1](res)
        settle(200)
        self.assertFalse(find(Spinner))
        self.assertIn("Camera", [r.get_title() for r in find(Adw.ActionRow)])
