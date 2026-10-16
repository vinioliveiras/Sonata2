"""Install test (Docker): on Fedora 44 (Wayfire 0.10), Debian 13 and Ubuntu
25.04 (0.9) Sonata installed without its plugins -- no title bars, round
corners, zoom... Now install.sh builds Wayfire 0.12 with its wlroots into
its own folder there (tools/build-wayfire-stack.sh), the session starts it,
and the plugins are built against it."""
import os
import pathlib
import re
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "install.sh").read_text()
SESSION = (ROOT / "tools" / "sonata-session").read_text()
STACK = (ROOT / "tools" / "build-wayfire-stack.sh").read_text()


class StackTest(unittest.TestCase):
    def test_script_pins_what_arch_runs(self):
        self.assertIn("WLROOTS_TAG=0.20.2", STACK)
        self.assertIn("WAYFIRE_COMMIT=d181484c", STACK)
        self.assertIn('echo "$STAMP" > "$PREFIX/standalone"', STACK)
        self.assertIn("--wrap-mode=default", STACK)                    # Debian's meson won't fetch otherwise
        self.assertIn("--force-fallback-for=pixman-1", STACK)          # Debian/Ubuntu's pixman 0.44
        self.assertIn('for p in "$SRC"/wayfire-plugin/wayfire-*.patch; do', STACK)   # Sonata's own fixes too
        self.assertIn("patches-$PATCHES", STACK)                       # a changed patch builds it again
        for name in ("buffer-failures", "cursor-focus", "configure-ack"):
            self.assertTrue((ROOT / "wayfire-plugin" / f"wayfire-{name}.patch").is_file(), name)

    def test_electron_resize_not_undone(self):
        """Vini: VS Code / Claude sometimes didn't resize on release -- a
        frame at the old size, from before the app took the configure (it
        took longer than the transaction's timeout), was taken as the app
        resizing itself, and its old size was sent back to it."""
        patch = (ROOT / "wayfire-plugin" / "wayfire-configure-ack.patch").read_text()
        self.assertIn("src/view/xdg-shell/xdg-toplevel.cpp", patch)
        self.assertIn("+    if (this->toplevel->base->current.configure_serial < this->target_configure)", patch)
        single = (ROOT / "tools" / "build-wayfire.sh").read_text()
        self.assertIn('for p in "$SRC"/wayfire-plugin/wayfire-*.patch; do', single)
        self.assertIn('"$PREFIX/sonata-patches"', single)
        installer = (ROOT / "install.sh").read_text()
        self.assertIn('"$own_wf/sonata-patches"', installer)                # rebuilt when the patches change
        r = subprocess.run(["bash", "-n", str(ROOT / "tools" / "build-wayfire-stack.sh")])
        self.assertEqual(r.returncode, 0)

    def test_installer_builds_it_for_older_wayfire(self):
        self.assertIn('bash "$SRC/tools/build-wayfire-stack.sh"', INSTALL)
        self.assertIn("$1 * 1000 + $2 < 11", INSTALL)
        for family in ("*debian*|*ubuntu*) BUILD_DEPS=", "*fedora*|*rhel*) BUILD_DEPS="):
            self.assertIn(family, INSTALL)
        # the plugins are built against it
        self.assertIn('export PKG_CONFIG_PATH="$own_wf/lib/pkgconfig', INSTALL)
        self.assertLess(INSTALL.index('export PKG_CONFIG_PATH="$own_wf/lib/pkgconfig'),
                        INSTALL.index("Building Sonata's Wayfire plugin"))

    def test_a_broken_optional_package_doesnt_block_apt(self):
        """Ubuntu 25.04 test install: one optional package's failed setup left
        dpkg interrupted, so greetd and the Wayfire build's tools never installed."""
        loop = INSTALL.split('for p in $OPT; do', 1)[1].split("done", 1)[0]
        self.assertIn("dpkg --configure -a", loop)

    def test_version_rule(self):
        """The same awk rule as install.sh on real version strings."""
        def older(version):
            rule = re.search(r"awk '(\{print[^']*\})'", INSTALL).group(1)     # install.sh's own
            script = ('v="$(echo "$1" | sed -n \'s/^\\([0-9]*\\)\\.\\([0-9]*\\).*/\\1 \\2/p\')"; '
                      'echo "$v" | awk \'' + rule + '\'')
            return subprocess.run(["bash", "-c", script, "x", version], capture_output=True,
                                  text=True).stdout.strip() == "1"
        self.assertTrue(older("0.9.0-unknown (Dec 24 2024) branch unknown"))
        self.assertTrue(older("0.10.1-unknown (Jan 16 2026) branch unknown wlroots-0.19.2"))
        self.assertFalse(older("0.12.0-d181484c (Sep 29 2026) branch master wlroots-0.20.2"))
        self.assertFalse(older("1.0.0"))
        # Arch's: plugins build there (the test install wrongly offered a rebuild / warned)
        self.assertFalse(older("0.11.0-d3990237 (Jul 27 2026) branch makepkg wlroots-0.20.2"))

    def test_session_starts_it(self):
        self.assertIn('[ -s "$own/standalone" ]', SESSION)
        standalone = SESSION[SESSION.index('[ -s "$own/standalone" ]'):SESSION.index("elif [ -x")]
        self.assertIn('WAYFIRE="$own/bin/wayfire"', standalone)
        self.assertNotIn("ln -s", standalone)                          # never the system's (older) plugins

    def test_doctor_reports_it(self):
        from sonata2 import doctor
        home = tempfile.mkdtemp()
        own = os.path.join(home, "own")
        os.makedirs(os.path.join(own, "bin"))
        os.makedirs(os.path.join(own, "lib", "wayfire"))
        exe = os.path.join(own, "bin", "wayfire")
        with open(exe, "w") as f:
            f.write("#!/bin/sh\necho '0.12.0-d181484c (Oct  6 2026) branch HEAD wlroots-0.20.2'\n")
        os.chmod(exe, 0o755)
        open(os.path.join(own, "lib", "wayfire", "libpixdecor.so"), "w").close()
        with open(os.path.join(own, "standalone"), "w") as f:
            f.write("wlroots-0.20.2 wayfire-d181484c")
        with mock.patch.object(doctor, "OWN_WAYFIRE", own):
            self.assertTrue(doctor._standalone())
            self.assertIn(os.path.join(own, "lib", "wayfire"), doctor._plugin_dirs())
            self.assertNotIn("/usr/lib/wayfire", doctor._plugin_dirs())


if __name__ == "__main__":
    unittest.main()
