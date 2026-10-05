"""Long window titles never run over the title bar buttons (pixdecor,
patched by install.sh). Run: python3 -m unittest tests.test_titlebar_ellipsize"""
import unittest


class TitleTest(unittest.TestCase):
    def test_patch_cuts_long_titles(self):
        """Vini: a long file name in Preview's title covered the window's
        buttons (pixdecor centred it without a limit)."""
        with open("wayfire-plugin/pixdecor-title.patch", encoding="utf-8") as f:
            patch = f.read()
        self.assertIn("pango_layout_set_ellipsize(layout, PANGO_ELLIPSIZE_MIDDLE)", patch)
        self.assertIn("buttons_width", patch)
        # Vini: a margin after the buttons, the title not touching the green one
        self.assertIn("buttons_width + border + 40", patch)
        added = sum(1 for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        self.assertIn(f"+159,{6 + added} @@", patch)                 # the hunk header matches its lines

    def test_installer_builds_it_and_session_uses_it(self):
        with open("install.sh", encoding="utf-8") as f:
            inst = f.read()
        self.assertIn("tools/build-pixdecor.sh", inst)
        with open("tools/build-pixdecor.sh", encoding="utf-8") as f:
            build = f.read()
        self.assertIn("pixdecor-title.patch", build)
        self.assertIn("PIXDECOR_COMMIT=", build)
        self.assertIn("tail -n", build)
        self.assertIn('grep -E -A3 "error:', build)                  # errors first, not the warnings after them
        self.assertIn("glm", build)                                  # pixdecor needs it (Vini's build failed)
        self.assertIn("command -v cmake", build)
        self.assertIn("vulkan-headers", build)                       # wlroots' headers need them                     # Arch's glm is found only through cmake                              # a failed build says why
        import subprocess
        self.assertEqual(subprocess.run(["bash", "-n", "tools/build-pixdecor.sh"]).returncode, 0)
        self.assertEqual(subprocess.run(["bash", "-n", "install.sh"]).returncode, 0)
        with open("tools/wayfire-config.sh", encoding="utf-8") as f:
            cfg = f.read()
        self.assertIn("plugin-manager/install/lib/wayfire", cfg.split("pix=1")[0])   # the user's build counts


if __name__ == "__main__":
    unittest.main()


class InstallWithoutAurTest(unittest.TestCase):
    """Issue #1: on CachyOS the AUR's pixdecor package pulled wayfire-git,
    which conflicts with the stable wayfire, and --noconfirm stopped the
    install. pixdecor is only built from source (tools/build-pixdecor.sh)."""

    def test_no_aur_helper(self):
        inst = open("install.sh", encoding="utf-8").read()
        for word in ("wayfire-plugin-pixdecor-git", "paru", "yay"):
            self.assertNotIn(word, inst)
        self.assertIn('bash "$SRC/tools/build-pixdecor.sh"', inst)
        doctor = open("sonata2/doctor.py", encoding="utf-8").read()
        self.assertNotIn("wayfire-plugin-pixdecor-git", doctor)
