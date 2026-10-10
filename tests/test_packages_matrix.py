"""Files' right-click Install / Run / Extract Here on many kinds of packages
(Vini: test it with several different packages). The commands for each
system; a real .deb installed where apt is; real AppImage runs; real
archives expanded."""
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from sonata2.files import packages as P  # noqa: E402


def settle(ms=3000, until=None):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)
        if until and until():
            return


class KindTest(unittest.TestCase):
    CASES = {
        "yay-12.4.2-1-x86_64.pkg.tar.zst": "arch", "old-1-1-any.pkg.tar.xz": "arch",
        "Discord 0.0.70.deb": "debian", "code_1.95_amd64.DEB": "debian",
        "google-chrome-stable.x86_64.rpm": "rpm", "com.spotify.Client.flatpakref": "flatpakref",
        "app.flatpak": "flatpak", "app_1.0.snap": "snap", "Obsidian-1.7.AppImage": "appimage",
        "tool.appimage": "appimage", "src.tar.gz": "archive", "src.tgz": "archive", "x.tar.xz": "archive",
        "x.tar.bz2": "archive", "x.tar.zst": "archive", "x.zip": "archive", "x.rar": "archive",
        "x.7z": "archive", "notes.txt": None, "pkg.tar.zst.sig": None,
    }

    def test_kinds(self):
        for name, k in self.CASES.items():
            self.assertEqual(P.kind("/d/" + name), k, name)

    def test_menu_entries(self):
        for name, k in self.CASES.items():
            labels = [i.label for i in P.menu_items("/d/" + name)]
            want = {None: [], "appimage": ["Run"], "archive": ["Extract Here"]}.get(k, ["Install"])
            self.assertEqual(labels, want, name)


class CommandTest(unittest.TestCase):
    def cmd(self, name, fam, tools=()):
        with mock.patch.object(P, "family", return_value=fam), \
                mock.patch.object(P.shutil, "which", side_effect=lambda t: f"/usr/bin/{t}" if t in tools else None):
            return P.install_command("/home/v/Downloads/" + name)

    def test_native_packages(self):
        self.assertEqual(self.cmd("yay-1.pkg.tar.zst", "arch"), "sudo pacman -U /home/v/Downloads/yay-1.pkg.tar.zst")
        self.assertEqual(self.cmd("a b.deb", "debian"), "sudo apt install '/home/v/Downloads/a b.deb'")
        self.assertEqual(self.cmd("c.rpm", "rpm", ("dnf",)), "sudo dnf install /home/v/Downloads/c.rpm")
        self.assertEqual(self.cmd("c.rpm", "rpm", ("zypper",)), "sudo zypper install /home/v/Downloads/c.rpm")

    def test_quotes_in_names(self):
        c = self.cmd("it's (1).pkg.tar.zst", "arch")
        self.assertEqual(c, "sudo pacman -U '/home/v/Downloads/it'\"'\"'s (1).pkg.tar.zst'")

    def test_foreign_packages_refused(self):
        self.assertIsNone(self.cmd("c.rpm", "arch"))
        self.assertIsNone(self.cmd("a.pkg.tar.zst", "debian"))
        self.assertIsNone(self.cmd("a.deb", "arch"))                 # no debtap
        self.assertIn("debtap -Q", self.cmd("a.deb", "arch", ("debtap",)))

    def test_flatpak_snap(self):
        self.assertEqual(self.cmd("x.flatpakref", "arch", ("flatpak",)),
                         "flatpak install --user /home/v/Downloads/x.flatpakref")
        self.assertEqual(self.cmd("x.flatpak", "arch", ("flatpak",)),
                         "flatpak install --user --bundle /home/v/Downloads/x.flatpak")
        self.assertIsNone(self.cmd("x.flatpak", "arch"))
        self.assertEqual(self.cmd("x.snap", "debian", ("snap",)), "sudo snap install --dangerous /home/v/Downloads/x.snap")


class InstallDialogsTest(unittest.TestCase):
    def run_install(self, name, fam, tools=(), helper=None):
        alerts, terms = [], []
        with mock.patch.object(P, "family", return_value=fam), \
                mock.patch.object(P.shutil, "which", side_effect=lambda t: f"/usr/bin/{t}" if t in tools else None), \
                mock.patch.object(P, "aur_helper", return_value=helper), \
                mock.patch.object(P.ui.dialog, "alert", side_effect=lambda *a, **k: alerts.append((a, k))), \
                mock.patch.object(P.system, "run_in_terminal", side_effect=lambda c: terms.append(c) or True):
            P.install("/d/" + name)
        return alerts, terms

    def test_runs_in_a_terminal(self):
        alerts, terms = self.run_install("yay.pkg.tar.zst", "arch")
        self.assertEqual(terms, ["sudo pacman -U /d/yay.pkg.tar.zst"])
        self.assertEqual(alerts, [])

    def test_deb_on_arch_offers_debtap(self):
        alerts, terms = self.run_install("discord.deb", "arch", helper="paru")
        self.assertIn("Debian and Ubuntu", alerts[0][0][0])
        self.assertEqual([r[0] for r in alerts[0][0][2]], ["cancel", "debtap"])
        with mock.patch.object(P.system, "run_in_terminal", side_effect=lambda c: terms.append(c) or True):
            alerts[0][1]["on_response"]("debtap")
        self.assertIn("paru -S debtap", terms[0])
        self.assertIn("debtap -Q /d/discord.deb", terms[0])

    def test_explains_what_it_cannot(self):
        for name, fam, words in (("c.rpm", "arch", "Fedora"), ("a.deb", "arch", "Debian"),
                                 ("x.flatpak", "arch", "Flatpak isn't installed"),
                                 ("x.snap", "arch", "Snap isn't installed")):
            alerts, terms = self.run_install(name, fam)
            self.assertEqual(terms, [], name)
            self.assertIn(words, alerts[0][0][1], name)

    def test_no_terminal(self):
        alerts = []
        with mock.patch.object(P, "family", return_value="arch"), \
                mock.patch.object(P.ui.dialog, "alert", side_effect=lambda *a, **k: alerts.append(a)), \
                mock.patch.object(P.system, "run_in_terminal", return_value=False):
            P.install("/d/a.pkg.tar.zst")
        self.assertIn("No terminal", alerts[0][0])


    def test_desktop_menu_parents_its_alerts(self):
        import inspect
        from sonata2.shell import desktop
        self.assertIn("packages.menu_items(file_of(item.info).get_path(), self.get_root())",
                      inspect.getsource(desktop.Desktop.item_menu))


@unittest.skipUnless(P.family() == "debian" and os.geteuid() == 0 and shutil.which("dpkg-deb"), "needs apt as root")
class RealDebTest(unittest.TestCase):
    def test_install_a_real_deb(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "pkg")
            os.makedirs(os.path.join(root, "DEBIAN"))
            with open(os.path.join(root, "DEBIAN", "control"), "w") as f:
                f.write("Package: sonata-matrix-test\nVersion: 1.0\nArchitecture: all\n"
                        "Maintainer: t <t@t>\nDescription: test\n")
            deb = os.path.join(d, "sonata matrix (test)_1.0_all.deb")
            subprocess.run(["dpkg-deb", "--build", root, deb], check=True, capture_output=True)
            cmd = P.install_command(deb)
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=dict(os.environ,
                               DEBIAN_FRONTEND="noninteractive"))
            try:
                self.assertEqual(r.returncode, 0, r.stderr)
                st = subprocess.run(["dpkg", "-s", "sonata-matrix-test"], capture_output=True, text=True).stdout
                self.assertIn("install ok installed", st)
            finally:
                subprocess.run(["dpkg", "-r", "sonata-matrix-test"], capture_output=True)


class AppImageTest(unittest.TestCase):
    def test_runs_after_asking_once(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": d}):
            from sonata2 import config
            with mock.patch.object(config, "CONFIG_DIR", os.path.join(d, "sonata2")):
                app = os.path.join(d, "My App-1.0.AppImage")
                mark = os.path.join(d, "ran")
                with open(app, "w") as f:
                    f.write(f"#!/bin/sh\ntouch '{mark}'\n")              # not executable yet
                asked = []
                with mock.patch.object(P.ui.dialog, "alert", side_effect=lambda *a, **k: asked.append(a)):
                    P.run_appimage(app)
                    self.assertEqual(len(asked), 1)
                    asked[0][3]("open")                                     # "Open"
                    settle(3000, until=lambda: os.path.exists(mark))
                    self.assertTrue(os.path.exists(mark))
                    os.remove(mark)
                    P.run_appimage(app)                                     # trusted: no question
                    settle(3000, until=lambda: os.path.exists(mark))
                self.assertEqual(len(asked), 1)
                self.assertTrue(os.path.exists(mark))


class ExtractTest(unittest.TestCase):
    def make(self, d, kind, password=None):
        src = os.path.join(d, "src")
        os.makedirs(os.path.join(src, "inner"), exist_ok=True)
        with open(os.path.join(src, "inner", "a.txt"), "w") as f:
            f.write("hello")
        out = os.path.join(d, "My Files." + kind)
        if kind == "zip" and not password:
            with zipfile.ZipFile(out, "w") as z:
                z.write(os.path.join(src, "inner", "a.txt"), "inner/a.txt")
        elif kind in ("zip", "7z"):
            if not shutil.which("7z"):
                self.skipTest("7z")
            args = ["7z", "a", "-bso0", "-bsp0"] + (["-tzip"] if kind == "zip" else []) + \
                   ([f"-p{password}"] if password else []) + [out, "inner"]
            subprocess.run(args, cwd=src, check=True)
        else:
            flag = {"tar.gz": "-z", "tar.xz": "-J", "tar.bz2": "-j", "tar.zst": "--zstd", "tar": None}[kind]
            if kind == "tar.zst" and not shutil.which("zstd"):
                self.skipTest("zstd")
            subprocess.run(["tar"] + ([flag] if flag else []) + ["-cf", out, "-C", src, "inner"], check=True)
        return out

    def extract(self, path, password=None):
        done, errs = [], []
        with mock.patch.object(P.ui.dialog, "alert", side_effect=lambda *a, **k: errs.append(a)), \
                mock.patch.object(P, "ask_password", side_effect=lambda *a: errs.append(("password",) + a)):
            P.extract(path, done=done.append, password=password)
            settle(5000, until=lambda: done or errs)
        return done, errs

    def test_every_archive(self):
        for kind in ("zip", "tar.gz", "tar.xz", "tar.bz2", "tar.zst", "tar", "7z"):
            with tempfile.TemporaryDirectory() as d:
                done, errs = self.extract(self.make(d, kind))
                self.assertEqual(errs, [], kind)
                self.assertEqual(os.path.basename(done[0]), "My Files", kind)
                with open(os.path.join(done[0], "inner", "a.txt")) as f:
                    self.assertEqual(f.read(), "hello", kind)

    def test_second_copy_beside(self):
        with tempfile.TemporaryDirectory() as d:
            arc = self.make(d, "zip")
            first, _ = self.extract(arc)
            second, _ = self.extract(arc)
            self.assertEqual(os.path.basename(second[0]), "My Files 2")

    def test_password_archives(self):
        for kind in ("zip", "7z"):
            with tempfile.TemporaryDirectory() as d:
                arc = self.make(d, kind, password="s3cret")
                done, errs = self.extract(arc)
                self.assertEqual(errs[0][0], "password", kind)              # asked for it
                self.assertFalse(os.path.exists(os.path.join(d, "My Files")), kind)   # nothing half-made
                done, errs = self.extract(arc, password="wrong")
                self.assertEqual(errs[0][0], "password", kind)
                self.assertTrue(errs[0][3], kind)                            # "wrong"
                done, errs = self.extract(arc, password="s3cret")
                self.assertEqual(errs, [], kind)
                with open(os.path.join(done[0], "inner", "a.txt")) as f:
                    self.assertEqual(f.read(), "hello")

    def test_broken_archive_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "broken.zip")
            with open(bad, "wb") as f:
                f.write(b"not a zip")
            done, errs = self.extract(bad)
            self.assertEqual(done, [])
            self.assertIn("Unable to expand", errs[0][0])
            self.assertEqual(os.listdir(d), ["broken.zip"])                # no empty folder left


if __name__ == "__main__":
    unittest.main()
