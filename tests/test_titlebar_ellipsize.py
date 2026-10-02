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
        self.assertIn("tail -n", build)                              # a failed build says why
        import subprocess
        self.assertEqual(subprocess.run(["bash", "-n", "tools/build-pixdecor.sh"]).returncode, 0)
        self.assertEqual(subprocess.run(["bash", "-n", "install.sh"]).returncode, 0)
        with open("tools/wayfire-config.sh", encoding="utf-8") as f:
            cfg = f.read()
        self.assertIn("plugin-manager/install/lib/wayfire", cfg.split("pix=1")[0])   # the user's build counts


if __name__ == "__main__":
    unittest.main()
