"""Vini: CachyOS' update (tray icon, "Run" in its notification) did nothing --
apps with Terminal=true look for xdg-terminal-exec, then terminals Sonata's
isn't one of. install.sh puts an xdg-terminal-exec that opens Sonata's."""
import os
import pathlib
import stat
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TerminalExecTest(unittest.TestCase):
    def shim(self):
        s = (ROOT / "install.sh").read_text()
        start = s.index("cat > \"$tmp/xdg-terminal-exec\" <<'EOF'\n") + len("cat > \"$tmp/xdg-terminal-exec\" <<'EOF'\n")
        body = s[start:s.index("EOF\n    sed -i \"s|@BIN@|$BIN|g\"")]
        d = tempfile.mkdtemp()
        fake = os.path.join(d, "sonata2")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        path = os.path.join(d, "xdg-terminal-exec")
        with open(path, "w") as f:
            f.write(body.replace("@BIN@", d))
        for p in (fake, path):
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        return path

    def test_runs_the_command_in_sonatas_terminal(self):
        out = subprocess.run([self.shim(), "-e", "echo", "it's", "ok"], capture_output=True, text=True).stdout
        args = out.splitlines()
        self.assertEqual(args[:2], ["terminal", "--exec"])
        ran = subprocess.run(["sh", "-c", args[2]], capture_output=True, text=True).stdout
        self.assertEqual(ran.strip(), "it's ok")                      # quoting survives

    def test_no_command_opens_a_terminal(self):
        out = subprocess.run([self.shim()], capture_output=True, text=True).stdout.split()
        self.assertEqual(out, ["terminal", "--new-window"])

    def test_only_when_the_system_has_none_and_removed_on_uninstall(self):
        s = (ROOT / "install.sh").read_text()
        self.assertIn('if ! command -v xdg-terminal-exec >/dev/null || [ "$(command -v xdg-terminal-exec)" = '
                      '"$BIN/xdg-terminal-exec" ]; then', s)
        self.assertIn('grep -qs "Sonata\'s Terminal" "$BIN/xdg-terminal-exec" && $SUDO rm -f "$BIN/xdg-terminal-exec"', s)


if __name__ == "__main__":
    unittest.main()
