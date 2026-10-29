"""Vini: for some apps Sonata showed the shortcut arrow although they aren't
shortcuts. Flatpak, Nix, Snap, Stow, Homebrew, alternatives... put an app's
entry (and its program, its icon) in place as a link, and every link got the
arrow -- in Files (the Applications view, or any folder) and on the Desktop.
The arrow is for what a person calls a shortcut now: a link in their own
places (the home folder outside its hidden folders, a disk plugged in, the
Desktop), as Create Shortcut makes.

Each install is mimicked in temporary folders: a fake home and, outside it,
a fake root (/var/lib/flatpak, /nix/store...), so no root is needed.

Run: xvfb-run -a python3 -m unittest tests.test_shortcut_arrow_installs"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from sonata2 import apps  # noqa: E402
from sonata2.files import folder as F  # noqa: E402
from sonata2.files import ops  # noqa: E402

ENTRY = "[Desktop Entry]\nType=Application\nName={}\nExec=true\n"


def wait(cond, ms=4000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


class _Layout(unittest.TestCase):
    """A fake home (…/home/vini) and a fake root beside it (…/root)."""

    def setUp(self):
        self.top = os.path.realpath(tempfile.mkdtemp())
        self.home = self.p("home/vini")
        self.root = self.p("root")
        os.makedirs(self.home)
        os.makedirs(self.root)
        patches = [mock.patch.object(GLib, "get_home_dir", return_value=self.home),
                   mock.patch.object(apps, "app_dirs", return_value=[])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def p(self, *parts):
        return os.path.join(self.top, *parts)

    def file(self, path, text=""):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def link(self, path, target):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(target, path)
        return path

    def entry(self, path, name="App"):
        return self.file(path, ENTRY.format(name))

    def arrow(self, path) -> bool:
        """The arrow as Files' icons draw it: the info of a listed item."""
        f = Gio.File.new_for_path(path)
        info = f.query_info(F.ATTRS, Gio.FileQueryInfoFlags.NONE, None)
        info.set_attribute_object("sonata::file", f)
        from sonata2.files.views import icon_with_badge, show_badge
        img = Gtk.Image()
        icon_with_badge(img)
        show_badge(img, info)
        return img.badge.get_visible()


class InstalledLinksHaveNoArrowTest(_Layout):
    def test_flatpak_apps_showed_the_shortcut_arrow(self):
        """Every Flatpak app had the arrow: exports/share/applications/<id>.desktop
        is a link into the app's deploy folder (system and per-user)."""
        for base in (self.p("root/var/lib/flatpak"), os.path.join(self.home, ".local/share/flatpak")):
            real = self.entry(os.path.join(base, "app/org.example.App/x86_64/stable/abc/export/share/"
                                                 "applications/org.example.App.desktop"))
            listed = self.link(os.path.join(base, "exports/share/applications/org.example.App.desktop"), real)
            icon = self.link(os.path.join(base, "exports/share/icons/hicolor/scalable/apps/org.example.App.svg"),
                             self.file(os.path.join(os.path.dirname(real), "x.svg")))
            self.assertFalse(self.arrow(listed), listed)
            self.assertFalse(self.arrow(icon), icon)

    def test_nix_profile_apps_showed_the_shortcut_arrow(self):
        """Nix / home-manager: ~/.nix-profile and /run/current-system/sw are
        made of links into /nix/store -- each app had the arrow."""
        store = self.entry(self.p("root/nix/store/abc-firefox/share/applications/firefox.desktop"))
        profile = self.p("root/nix/var/nix/profiles/per-user/vini/profile")
        self.link(os.path.join(profile, "share/applications/firefox.desktop"), store)
        self.link(os.path.join(self.home, ".nix-profile"), profile)
        system = self.link(self.p("root/run/current-system/sw/share/applications/firefox.desktop"), store)
        self.assertFalse(self.arrow(os.path.join(self.home, ".nix-profile/share/applications/firefox.desktop")))
        self.assertFalse(self.arrow(system))

    def test_snap_and_system_links_showed_the_shortcut_arrow(self):
        """Snap's entries, alternatives in /usr/bin, `ln -s` installs in
        /usr/local, Homebrew's /home/linuxbrew: links of the system, not
        the person's -- they had the arrow when browsed in Files."""
        snap = self.link(self.p("root/var/lib/snapd/desktop/applications/firefox_firefox.desktop"),
                         self.entry(self.p("root/snap/firefox/current/meta/gui/firefox.desktop")))
        alt = self.link(self.p("root/etc/alternatives/editor"), self.file(self.p("root/usr/bin/vim.basic")))
        usr_bin = self.link(self.p("root/usr/bin/editor"), alt)
        local = self.link(self.p("root/usr/local/share/applications/tool.desktop"),
                          self.entry(self.p("root/opt/tool/tool.desktop")))
        brew = self.link(self.p("home/linuxbrew/.linuxbrew/bin/gh"),
                         self.file(self.p("home/linuxbrew/.linuxbrew/Cellar/gh/2.0/bin/gh")))
        for path in (snap, alt, usr_bin, local, brew):
            self.assertFalse(self.arrow(path), path)

    def test_links_in_hidden_folders_showed_the_shortcut_arrow(self):
        """GNU Stow / dotfiles and `ln -s` into ~/.local/share/applications,
        an autostart entry linked into ~/.config/autostart, an AppImage's
        program linked into ~/.local/bin: how something was installed."""
        stow = self.link(os.path.join(self.home, ".local/share/applications/editor.desktop"),
                         self.entry(os.path.join(self.home, "dotfiles/apps/editor.desktop")))
        auto = self.link(os.path.join(self.home, ".config/autostart/syncthing.desktop"),
                         self.entry(self.p("root/usr/share/applications/syncthing.desktop")))
        appimage = self.link(os.path.join(self.home, ".local/bin/tool"),
                             self.file(os.path.join(self.home, "Applications/Tool.AppImage")))
        for path in (stow, auto, appimage):
            self.assertFalse(self.arrow(path), path)

    def test_an_applications_folder_never_shows_the_arrow(self):
        """An applications folder holds apps, even one outside the hidden
        folders (XDG_DATA_HOME=~/data): its links are installs."""
        apps_dir = os.path.join(self.home, "data/applications")
        listed = self.link(os.path.join(apps_dir, "editor.desktop"),
                           self.entry(os.path.join(self.home, "dotfiles/editor.desktop")))
        with mock.patch.object(apps, "app_dirs", return_value=[apps_dir]):
            self.assertFalse(self.arrow(listed))

    def test_regular_entries_never_had_the_arrow(self):
        """AppImageLauncher / Gear Lever, Steam's game entries, Sonata's web
        apps: written as plain files -- no arrow (and none before)."""
        for path in (os.path.join(self.home, ".local/share/applications/appimagekit_tool.desktop"),
                     os.path.join(self.home, ".local/share/applications/Portal 2.desktop"),
                     os.path.join(self.home, "Desktop/Portal 2.desktop"),
                     os.path.join(self.home, ".local/share/applications/sonata-webapp-w123.desktop")):
            self.assertFalse(self.arrow(self.entry(path)), path)

    def test_files_applications_view_showed_the_shortcut_arrow(self):
        """Files > Applications: the Flatpak and Nix apps had the arrow."""
        flatpak = self.p("root/var/lib/flatpak/exports/share/applications")
        nix = self.p("root/run/current-system/sw/share/applications")
        self.link(os.path.join(flatpak, "org.example.Flat.desktop"),
                  self.entry(self.p("root/var/lib/flatpak/app/org.example.Flat/export/org.example.Flat.desktop"),
                             "Flat"))
        self.link(os.path.join(nix, "nixapp.desktop"),
                  self.entry(self.p("root/nix/store/abc-nixapp/share/applications/nixapp.desktop"), "NixApp"))
        from sonata2.files.views import icon_with_badge, show_badge
        with mock.patch.object(apps, "app_dirs", return_value=[flatpak, nix]):
            fo = F.Folder(lambda uri: None, lambda uri, e: None)
            fo.load(F.APPS)
            self.assertTrue(wait(lambda: fo.store.get_n_items() == 2))
            for i in range(2):
                info = fo.store.get_item(i)
                self.assertTrue(info.get_is_symlink())              # the layout: links
                img = Gtk.Image()
                icon_with_badge(img)
                show_badge(img, info)
                self.assertFalse(img.badge.get_visible(), info.get_name())


class ShortcutsKeepTheArrowTest(_Layout):
    def test_create_shortcut_in_the_persons_folders(self):
        """What Create Shortcut makes in the home folder, Documents, the
        Desktop -- to a file, a folder, an app -- keeps the arrow."""
        report = self.file(os.path.join(self.home, "report.pdf"))
        app = self.entry(self.p("root/var/lib/flatpak/exports/share/applications/org.example.App.desktop"))
        for d in (self.home, os.path.join(self.home, "Documents"), os.path.join(self.home, "Desktop")):
            os.makedirs(d, exist_ok=True)
            made = ops.make_shortcuts([Gio.File.new_for_path(report), Gio.File.new_for_path(app),
                                       Gio.File.new_for_path(self.root)], Gio.File.new_for_path(d))
            self.assertEqual(len(made), 3)
            for f in made:
                self.assertTrue(self.arrow(f.get_path()), f.get_path())
        self.assertFalse(self.arrow(report))                        # the original: none

    def test_a_folder_linked_to_another_disk_or_home_through_a_link(self):
        """Documents linked to another disk, and Silverblue's /home (a link to
        /var/home): a shortcut in them is still in the person's places."""
        disk = self.p("data/Documents")
        os.makedirs(disk)
        self.link(os.path.join(self.home, "Documents"), disk)
        made, = ops.make_shortcuts([Gio.File.new_for_path(self.root)],
                                   Gio.File.new_for_path(os.path.join(self.home, "Documents")))
        self.assertTrue(self.arrow(made.get_path()))
        var_home = self.p("var/home/vini")
        os.makedirs(var_home)
        self.link(self.p("var/home/vini/x - Shortcut"), self.root)
        with mock.patch.object(GLib, "get_home_dir", return_value=var_home):
            self.assertTrue(self.arrow(self.p("var/home/vini/x - Shortcut")))

    def test_plugged_in_disks_are_the_persons(self):
        self.assertTrue(ops._persons_place("/run/media/vini/USB/Photos"))
        self.assertTrue(ops._persons_place("/media/vini/USB"))
        self.assertTrue(ops._persons_place("/mnt/data/Work"))
        self.assertFalse(ops._persons_place("/mnt/data/.cache"))
        self.assertFalse(ops._persons_place("/usr/share/applications"))

    def test_the_desktop_and_unknown_places_keep_the_arrow(self):
        """The Desktop (wherever it is) and a link of no known place (the
        Trash, an info without its file) keep the arrow as before."""
        link = self.link(self.p("elsewhere/a - Shortcut"), self.root)
        info = Gio.File.new_for_path(link).query_info(F.ATTRS, Gio.FileQueryInfoFlags.NONE, None)
        self.assertTrue(ops.is_shortcut(info))                       # no "sonata::file"
        info.set_attribute_object("sonata::file", Gio.File.new_for_path(link))
        self.assertFalse(ops.is_shortcut(info))
        self.assertTrue(ops.is_shortcut(info, mine=True))
        self.assertFalse(ops.is_shortcut(Gio.FileInfo(), mine=True))  # not a link: never


if __name__ == "__main__":
    unittest.main()
