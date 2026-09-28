"""XDG autostart rules (no display): python3 -m unittest tests.test_autostart"""
import os
import tempfile
import unittest

HOME = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = os.path.join(HOME, "cfg")
os.environ["XDG_CONFIG_DIRS"] = os.path.join(HOME, "sys")

from sonata2 import autostart  # noqa: E402


def entry(d, name, extra=""):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w") as f:
        f.write(f"[Desktop Entry]\nType=Application\nName={name}\nExec=true\n{extra}")


class AutostartTest(unittest.TestCase):
    def test_rules(self):
        user = os.path.join(HOME, "cfg", "autostart")
        system = os.path.join(HOME, "sys", "autostart")
        entry(user, "a.desktop")
        entry(user, "hidden.desktop", "Hidden=true\n")
        entry(user, "gnome.desktop", "OnlyShowIn=GNOME;\n")
        entry(user, "sonata.desktop", "OnlyShowIn=Sonata;\n")
        entry(user, "notsonata.desktop", "NotShowIn=Sonata;\n")
        entry(user, "disabled.desktop", "X-GNOME-Autostart-enabled=false\n")
        entry(user, "tryexec.desktop", "TryExec=definitely-not-installed-xyz\n")
        entry(system, "hidden.desktop")        # user's Hidden=true wins over it
        entry(system, "sys.desktop")
        names = sorted(n for n, _ in autostart.entries())
        self.assertEqual(names, ["a.desktop", "sonata.desktop", "sys.desktop"])


if __name__ == "__main__":
    unittest.main()
