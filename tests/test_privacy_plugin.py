"""Vini (security review): any app could capture the screen, list and
control the windows, read the clipboard, type keys -- Wayfire offers those
protocols to every client. sonata-privacy hides them from everything but
Sonata, the portal and the tools Sonata runs."""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = os.path.join(ROOT, "wayfire-plugin", "src", "sonata-privacy.cpp")


def read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def cpp_set(name):
    body = re.search(name + r" = \{(.*?)\};", read("wayfire-plugin", "src", "sonata-privacy.cpp"), re.S).group(1)
    return set(re.findall(r'"([^"]+)"', body))


class PrivacyPluginTest(unittest.TestCase):
    def test_privileged_globals(self):
        p = cpp_set("PRIVILEGED")
        for iface in ("zwlr_screencopy_manager_v1", "ext_image_copy_capture_manager_v1", "zwlr_export_dmabuf_manager_v1",
                      "zwlr_foreign_toplevel_manager_v1", "zwlr_data_control_manager_v1",
                      "zwp_virtual_keyboard_manager_v1", "zwlr_virtual_pointer_manager_v1",
                      "ext_session_lock_manager_v1", "zwlr_layer_shell_v1"):
            self.assertIn(iface, p)

    def test_every_tool_sonata_runs_is_a_helper(self):
        """A tool Sonata starts that needs one of them, left out, would just stop working."""
        helpers = cpp_set("HELPERS")
        tools = ("grim", "slurp", "wf-recorder", "wl-copy", "wl-paste", "wtype", "wlsunset", "wlr-randr",
                 "swayidle", "wayvnc", "wlopm", "ydotool", "cliphist", "gammastep", "kanshi", "wl-mirror")
        used = set()
        for base, _dirs, files in os.walk(os.path.join(ROOT, "sonata2")):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(base, f), encoding="utf-8") as fh:
                        text = fh.read()
                    used |= {t for t in tools if re.search(r'["\']' + re.escape(t) + r'["\' ]', text)}
        self.assertTrue(used)
        self.assertEqual(used - helpers, set())

    def test_portal_and_sandboxes(self):
        src = read("wayfire-plugin", "src", "sonata-privacy.cpp")
        self.assertIn("xdg-desktop-portal-wlr", cpp_set("SERVICES"))
        self.assertIn("same_mount_namespace", src)                    # Flatpak never trusted
        self.assertIn('args[i + 1] == "sonata2"', src)

    def test_built_and_loaded(self):
        self.assertIn("'sonata-privacy'", read("wayfire-plugin", "meson.build"))
        self.assertIn("metadata/sonata-privacy.xml", read("wayfire-plugin", "meson.build"))
        self.assertIn("sonata-privacy", read("tools", "wayfire-config.sh"))
        xml = read("wayfire-plugin", "metadata", "sonata-privacy.xml")
        self.assertIn('name="trusted"', xml)
        self.assertIn('name="enabled"', xml)
        # first release: learning mode (only logs what it would hide; nothing breaks)
        self.assertRegex(xml, r'name="enforce" type="bool">\s*<_short>[^<]*</_short>\s*<default>false</default>')
        self.assertIn("would be hidden from", read("wayfire-plugin", "src", "sonata-privacy.cpp"))


if __name__ == "__main__":
    unittest.main()
