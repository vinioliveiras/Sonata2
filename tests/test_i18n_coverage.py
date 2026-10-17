"""Every text the user sees has its translation (Vini: "tests that check
every item has a translation"). tests.test_i18n checks the strings marked
for translation (_(), ngettext(), N_(), pgettext()); this finds the ones
that never were, and translations left the same as the English:

- a literal handed straight to a widget's text (label=, title=, tooltip=,
  set_label(), Gtk.Label("..."), a menu Item("...")...) instead of _("...")
  -- the Close button's tooltip, Assistant's "No Conversations", Calendar's
  "No Results" and Camera's "Open" were all left in English;
- a catalog entry identical to the English. Some are right (Wi-Fi, Dock,
  OK, Kernel, music genres, keys): those are listed below, reviewed; a new
  one must be translated or added here on purpose.

tests.test_i18n_screens opens every app in Portuguese and reads the screen.

    python3 -m unittest tests.test_i18n_coverage
"""
import ast
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "sonata2")

TEXT_KEYWORDS = {"label", "title", "tooltip_text", "placeholder_text", "subtitle", "description", "text",
                 "heading", "body", "message"}
TEXT_METHODS = {"set_label", "set_title", "set_tooltip_text", "set_placeholder_text", "set_subtitle", "set_text",
                "set_heading", "set_body", "set_markup", "set_tooltip_markup", "set_description", "set_message"}
TEXT_FIRST_ARG = {"Label", "Button", "CheckButton", "ToggleButton", "MenuButton", "Item", "Toast", "StatusPage",
                  "ActionRow", "SwitchRow", "ComboRow", "PreferencesGroup", "PreferencesPage", "ExpanderRow",
                  "EntryRow", "MessageDialog", "AlertDialog", "LinkButton"}

# never shown, or the same in every language
NOT_SHOWN = {
    # titles of the shell's undecorated surfaces (no title bar: never on screen)
    ("shell/spotlight.py", "Search"), ("shell/notifications.py", "Notifications"),
    ("shell/notifications.py", "Notification Center"), ("shell/capture.py", "Screenshot"),
    ("shell/spaces.py", "Desktops"), ("shell/switcher.py", "App Switcher"), ("shell/livedraw.py", "Drawing"),
    ("shell/livedraw.py", "Drawing Tools"), ("shell/blackout.py", "Blackout"), ("shell/topbar.py", "Menu Bar"),
    ("shell/dock.py", "Dock"), ("shell/wallpaper.py", "Wallpaper"), ("shell/preview.py", "Sonata 2 preview"),
    # names
    ("settings/app.py", "Sonata 2"), ("settings/app.py", "Bluetooth"),
    ("textedit/window.py", "Aa"),                           # (the fonts button: a sample of type)
}
NOT_SHOWN_FILES = {"ui/gallery.py"}                         # the design system's gallery (a developer tool)

# translations the same as the English, reviewed: loanwords, names, keys, units
SAME_AS_ENGLISH = {
    "pt_BR": {
        "Apps", "Arch Linux (.pkg.tar.zst)", "Backspace", "Backup", "Bluetooth", "Bluetooth PAN", "Bluetooth…", "CPU",
        "Dance", "Dock", "Downloads", "Emoji", "FPS", "Feedbacker", "Firewall", "Flash", "GPU", "GPU ({maker})",
        "Hardware (OpenGL)", "Hardware (Vulkan)", "Hip-Hop", "Internet", "Jazz", "Kernel", "Kernel {version}",
        "Loopback", "Lounge", "Mail", "Manual", "Megapixels", "Mouse", "Night Shift", "Normal", "OK", "Page Down",
        "Page Up", "Piano", "Pop", "Print Screen", "R&B", "Return", "Rock", "Social", "Start / Options", "Swap",
        "TIMER", "Terminal", "TextEdit", "Threads", "Timer", "Timer (3 s)", "Timers", "Trackpad", "View / Share",
        "Volume", "Wi-Fi", "Windows NT File System (NTFS)", "Zero KB", "Zero bytes", "Zoom", "min", "pixels",
        "{bytes} bytes", "{count} tokens", "{label}:", "{n} FPS", "{n} GB", "{n} bytes", "{n} fps",
        "{size} ({bytes} bytes)", "{words}, {characters}", "{w} × {h} pixels"},
    "pt_PT": {
        "Apps", "Arch Linux (.pkg.tar.zst)", "Bluetooth", "Bluetooth PAN", "Bluetooth…", "CPU", "Dock", "Emoji", "FPS",
        "Feedbacker", "Firewall", "Flash", "GPU", "GPU ({maker})", "Hardware (OpenGL)", "Hardware (Vulkan)",
        "Hip-Hop", "Internet", "Jazz", "Kernel", "Kernel {version}", "Loopback", "Lounge", "Mail", "Manual",
        "Night Shift", "Normal", "OK", "Page Down", "Page Up", "Piano", "Pop", "Print Screen", "R&B", "Return",
        "Rock", "Router", "Social", "Sockets", "Start / Options", "Swap", "Terminal", "TextEdit", "Threads",
        "Trackpad", "View / Share", "Volume", "Wi-Fi", "Windows NT File System (NTFS)", "Zero KB", "Zero bytes",
        "Zombie", "Zoom", "min", "{bytes} bytes", "{count} tokens", "{label}:", "{n} FPS", "{n} GB", "{n} bytes",
        "{n} fps", "{size} ({bytes} bytes)", "{words}, {characters}"},
}


def wordy(s) -> bool:
    """Words a person reads -- not an id, a CSS class, a path or an icon."""
    if not isinstance(s, str) or len(s) < 2:
        return False
    if " " not in s and re.fullmatch(r"[\w.\-/:%#@]+", s):
        return bool(re.fullmatch(r"[A-Z][a-z]+(?: [A-Z]?[a-z]+)*[.…!?]?", s))
    return bool(re.search(r"[A-Za-z]{2,}", s)) and " " in s and not s.startswith(("<", "{", "#", "/", "."))


def unmarked_texts() -> list:
    """[(file, line, text)] of literal texts handed to a widget unmarked."""
    found = []
    for folder, _dirs, files in os.walk(PKG):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, PKG)
            if rel in NOT_SHOWN_FILES:
                continue
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                values = [kw.value for kw in node.keywords if kw.arg in TEXT_KEYWORDS]
                if node.args and (fn in TEXT_METHODS or fn in TEXT_FIRST_ARG):
                    values.append(node.args[0])
                for v in values:
                    if isinstance(v, ast.Constant) and wordy(v.value) and (rel, v.value) not in NOT_SHOWN:
                        found.append((rel, v.lineno, v.value))
    return sorted(found)


class UnmarkedTextTest(unittest.TestCase):
    def test_no_text_left_unmarked(self):
        self.assertEqual(unmarked_texts(), [])

    def test_the_scan_finds_them(self):
        """The scan itself: the four texts that were left in English."""
        src = 'b = Gtk.Button(tooltip_text="Close")\nGtk.Label(label="No Results")\nx.set_title("Open File")\n'
        import tempfile
        from unittest import mock
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, "w.py"), "w") as f:
            f.write(src + 'Gtk.Label(label=_("Fine"))\nGtk.Box(css_classes=["cal-empty"])\n')
        with mock.patch(f"{__name__}.PKG", folder):
            self.assertEqual([t for _f, _l, t in unmarked_texts()], ["Close", "No Results", "Open File"])


class SameAsEnglishTest(unittest.TestCase):
    def test_only_the_reviewed_ones(self):
        from sonata2 import i18n
        for lang, ok in SAME_AS_ENGLISH.items():
            cat = i18n._load(lang)
            same = {k for k, v in cat.items() if isinstance(v, str) and v == k and any(c.isalpha() for c in k)}
            self.assertEqual(sorted(same - ok), [], f"{lang}: translate these (or add them to SAME_AS_ENGLISH)")


if __name__ == "__main__":
    unittest.main()
