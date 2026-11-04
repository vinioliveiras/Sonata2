"""Vini: an AppImage could only be run from Files, not installed. Install (its
right-click) copies it into ~/Applications with a launcher of its own --
name, icon and categories from inside it -- so it's in the Apps Menu; and
uninstalling it moves the AppImage to the Trash with its launcher.

    xvfb-run -a python3 -m unittest tests.test_appimages
"""
import os
import stat
import tempfile
import unittest
from unittest import mock

from sonata2 import appimages as A

# a stand-in AppImage: its runtime's --appimage-extract <pattern> writes
# squashfs-root/<files> where it's run
FAKE = """#!/bin/sh
[ "$1" = "--appimage-extract" ] || exit 1
mkdir -p squashfs-root
case "$2" in
  "*.desktop") printf '[Desktop Entry]\\nType=Application\\nName=Cool App\\nIcon=cool\\nCategories=Graphics;\\nStartupWMClass=CoolApp\\nExec=AppRun %%U\\n' > squashfs-root/cool.desktop ;;
  ".DirIcon") ln -sf cool.png squashfs-root/.DirIcon ;;
  "cool.png") printf '\\211PNG fake' > squashfs-root/cool.png ;;
esac
"""


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        p = [mock.patch.object(A, "apps_dir", return_value=os.path.join(self.home, "Applications")),
             mock.patch.object(A, "launchers_dir", return_value=os.path.join(self.home, "applications")),
             mock.patch.object(A, "icons_dir", return_value=os.path.join(self.home, "icons"))]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.src = os.path.join(self.home, "Downloads", "Cool App-1.2.3-x86_64.AppImage")
        os.makedirs(os.path.dirname(self.src))
        with open(self.src, "w") as f:
            f.write(FAKE)
        os.chmod(self.src, 0o644)                                  # downloaded: not executable yet

    def test_appimage_could_not_be_installed(self):
        res = A.install(self.src)
        self.assertEqual(os.path.dirname(res["appimage"]), os.path.join(self.home, "Applications"))
        self.assertTrue(os.stat(res["appimage"]).st_mode & stat.S_IXUSR)
        self.assertTrue(os.path.isfile(self.src))                   # the download stays where it was
        self.assertEqual(res["name"], "Cool App")
        self.assertEqual(os.path.basename(res["desktop"]), "appimage-cool-app.desktop")
        text = open(res["desktop"]).read()
        self.assertIn("Name=Cool App\n", text)
        self.assertIn("Categories=Graphics;\n", text)
        self.assertIn("StartupWMClass=CoolApp\n", text)
        self.assertIn(f'Exec="{res["appimage"]}" %U\n', text)    # (a space in its name: quoted)
        icon = next(line[5:] for line in text.splitlines() if line.startswith("Icon="))
        self.assertTrue(os.path.isfile(icon) and icon.endswith(".png"), icon)
        self.assertEqual(A.file_of(res["desktop"]), res["appimage"])   # uninstall finds the file

    def test_installed_twice_keeps_both(self):
        a, b = A.install(self.src), A.install(self.src)
        self.assertNotEqual(a["appimage"], b["appimage"])
        self.assertNotEqual(a["desktop"], b["desktop"])

    def test_no_launcher_inside(self):
        with open(self.src, "w") as f:
            f.write("#!/bin/sh\nmkdir -p squashfs-root\n")
        res = A.install(self.src)
        self.assertEqual(res["name"], "Cool App")                   # from the file's name, version cut
        text = open(res["desktop"]).read()
        self.assertIn("Icon=application-x-executable\n", text)
        self.assertIn("Categories=Utility;\n", text)


class MenuTest(unittest.TestCase):
    def test_right_click_has_install(self):
        from sonata2 import ui
        from sonata2.files import packages as P
        with mock.patch.object(ui.menu, "Item", side_effect=lambda label, cb=None, **_k: (label, cb)):
            items = P.menu_items("/x/App.AppImage")
        self.assertEqual([i[0] for i in items], ["Run", "Install"])


if __name__ == "__main__":
    unittest.main()
