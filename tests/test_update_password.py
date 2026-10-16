"""Vini: a system update asked for the password again and again (pkexec at
every AUR step). install.sh adds a polkit rule keeping the "yes" for a few
minutes -- pacman only, administrators at their own session -- and adds
the disks rule without asking. python3 -m unittest tests.test_update_password"""
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULE = os.path.join(ROOT, "config", "polkit", "60-sonata2-updates.rules")


class UpdateRuleTest(unittest.TestCase):
    def test_installed_by_install_sh(self):
        with open(os.path.join(ROOT, "install.sh")) as f:
            sh = f.read()
        self.assertIn('"$SRC/config/polkit/60-sonata2-updates.rules"', sh)
        self.assertIn("/etc/polkit-1/rules.d/60-sonata2-updates.rules", sh)       # and removed by --uninstall
        self.assertEqual(len(re.findall(r"60-sonata2-updates\.rules", sh)), 4)

    @unittest.skipUnless(shutil.which("node"), "node checks the rule's JavaScript")
    def test_rule_keeps_only_pacman_for_admins(self):
        js = """
        const fs = require("fs"); let rule;
        const polkit = {Result: {AUTH_ADMIN_KEEP: "keep"}, addRule: f => rule = f};
        eval(fs.readFileSync(process.argv[1], "utf8"));
        const a = (id, prog) => ({id, lookup: k => k == "program" ? prog : undefined});
        const s = (g, local = true, active = true) => ({local, active, isInGroup: x => x == g});
        const E = "org.freedesktop.policykit.exec";
        console.log(JSON.stringify([rule(a(E, "/usr/bin/pacman"), s("wheel")), rule(a(E, "/usr/bin/pacman"), s("sudo")),
            rule(a(E, "/usr/bin/bash"), s("wheel")), rule(a(E, "/usr/bin/pacman"), s("users")),
            rule(a(E, "/usr/bin/pacman"), s("wheel", false)), rule(a(E, "/usr/bin/pacman"), s("wheel", true, false))]));
        """
        out = subprocess.run(["node", "-e", js, RULE], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), '["keep","keep",null,null,null,null]')


class DiskRuleTest(unittest.TestCase):
    """Vini: the disks rule is added without asking; --no-mount-without-password removes it."""

    def _run(self, mountrule, exists=False):
        with open(os.path.join(ROOT, "install.sh")) as f:
            sh = f.read()
        block = sh[sh.index("MOUNT_RULES="):]
        block = block[:block.index("\nfi\n") + 4]
        block = block.replace('[ -f "$MOUNT_RULES" ]', "[ %s ]" % ("1" if exists else ""))   # (no /etc here)
        script = ('sudo() { echo "SUDO $*"; cat >/dev/null; }\nask_no() { echo ASKED; return 1; }\n'
                  f"YES=0 MOUNTRULE={mountrule}\n" + block)
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, input="", timeout=20).stdout

    def test_added_without_asking(self):
        out = self._run(1)
        self.assertNotIn("ASKED", out)
        self.assertIn("Disks: mounted without a password", out)

    def test_kept_when_there(self):
        out = self._run(1, exists=True)
        self.assertNotIn("SUDO", out)
        self.assertNotIn("ASKED", out)

    def test_flag_removes_it(self):
        self.assertIn("SUDO rm -f /etc/polkit-1/rules.d/50-sonata2-mount.rules", self._run(0))

    def test_default_is_on(self):
        with open(os.path.join(ROOT, "install.sh")) as f:
            self.assertIn(" MOUNTRULE=1 ", f.read())

if __name__ == "__main__":
    unittest.main()
