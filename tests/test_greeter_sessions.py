"""Vini: plain Wayfire (no Sonata) showed in the login screen's session list
after every install. It's left out; the other desktops stay."""
import os
import tempfile
import unittest
from unittest import mock

from sonata2.shell import greeter as G


def entry(d, key, name, execl):
    with open(os.path.join(d, key + ".desktop"), "w") as f:
        f.write(f"[Desktop Entry]\nName={name}\nExec={execl}\nType=Application\n")


class SessionsTest(unittest.TestCase):
    def test_plain_wayfire_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            entry(d, "sonata", "Sonata", "/usr/local/bin/sonata-login")
            entry(d, "wayfire", "Wayfire", "wayfire")
            entry(d, "wayfire-uwsm", "Wayfire (UWSM)", "uwsm start wayfire")
            entry(d, "plasma", "Plasma (Wayland)", "startplasma-wayland")
            with mock.patch.object(G, "SESSION_DIRS", [d]):
                keys = [s.key for s in G.sessions()]
        self.assertEqual(keys, ["sonata", "plasma"])


if __name__ == "__main__":
    unittest.main()
