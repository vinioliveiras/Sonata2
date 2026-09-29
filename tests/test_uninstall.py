"""Uninstall: who owns an app's entry, and the commands (python3 -m unittest tests.test_uninstall)."""
import os
import tempfile
import unittest

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()

from sonata2 import apps  # noqa: E402
from sonata2.backend import uninstall as U  # noqa: E402


def entry(folder, name):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".desktop")
    with open(path, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=T\nExec=true\n")
    return apps.DesktopAppInfo.new_from_filename(path)


class UninstallTest(unittest.TestCase):
    def test_kinds(self):
        data = os.environ["XDG_DATA_HOME"]
        o = U.owner(entry(os.path.join(data, "applications"), "myshortcut"))
        self.assertEqual((o.kind, o.steps()), ("shortcut", []))
        o = U.owner(entry(os.path.join(data, "flatpak", "exports", "share", "applications"), "org.x.App"))
        self.assertEqual((o.kind, o.name, o.manager), ("flatpak", "org.x.App", "flatpak-user"))
        self.assertIn("--user", o.steps()[0])
        o = U.Owner("package", "foo", "pacman")
        self.assertEqual(o.steps()[0][:3], ["pkexec", "pacman", "-Rns"])


if __name__ == "__main__":
    unittest.main()
