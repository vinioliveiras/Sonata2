"""Vini: every Dock name label went blank sometimes -- install.sh deleted the
installed copy while the Dock ran, and its bundled font file with it
(Pango: "font_face status is: file not found"). The copy is now updated in
place: unchanged files keep their inode."""
import importlib.util
import os
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
spec = importlib.util.spec_from_file_location("sync_tree", os.path.join(ROOT, "tools", "sync-tree.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class SyncTreeTest(unittest.TestCase):
    def test_in_place(self):
        src, dst = tempfile.mkdtemp(), tempfile.mkdtemp()
        write(f"{dst}/sonata2/data/fonts/Inter.ttf", "font")
        write(f"{dst}/sonata2/dock.py", "old")
        write(f"{dst}/sonata2/gone.py", "x")
        os.makedirs(f"{dst}/sonata2/old_dir")
        write(f"{src}/sonata2/data/fonts/Inter.ttf", "font")
        write(f"{src}/sonata2/dock.py", "new")
        write(f"{src}/sonata2/new.py", "n")
        os.symlink("dock.py", f"{src}/sonata2/link.py")
        font_ino = os.stat(f"{dst}/sonata2/data/fonts/Inter.ttf").st_ino
        held = open(f"{dst}/sonata2/dock.py")                     # a running process holds the old one
        c = S.sync(src, dst)
        self.assertEqual(os.stat(f"{dst}/sonata2/data/fonts/Inter.ttf").st_ino, font_ino)   # the font survives
        with open(f"{dst}/sonata2/dock.py") as f:
            self.assertEqual(f.read(), "new")
        self.assertEqual(held.read(), "old")                     # still readable by its holder
        held.close()
        self.assertTrue(os.path.exists(f"{dst}/sonata2/new.py"))
        self.assertEqual(os.readlink(f"{dst}/sonata2/link.py"), "dock.py")
        self.assertFalse(os.path.exists(f"{dst}/sonata2/gone.py"))
        self.assertFalse(os.path.exists(f"{dst}/sonata2/old_dir"))
        self.assertEqual(c["kept"], 1)
        self.assertEqual(S.sync(src, dst)["replaced"], 0)          # a second run changes nothing

    def test_install_uses_it(self):
        with open(os.path.join(ROOT, "install.sh")) as f:
            text = f.read()
        self.assertIn('tools/sync-tree.py" "$tmp/sonata2" "$SHARE"', text)
        block = text[text.index("carry_data\n$SUDO mkdir"):text.index("cat > \"$tmp/sonata2-launcher\"")]
        self.assertNotIn('rm -rf "$SHARE"\n$SUDO mkdir', block)      # never deleted before the update


if __name__ == "__main__":
    unittest.main()
