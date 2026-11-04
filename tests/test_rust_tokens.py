"""The Rust port's design tokens (rust/crates/sonata-ui/tokens.json) are
generated from sonata2/ui/tokens.py, which stays the one place a value is
changed (docs/RUST_PORT.md). python3 -m unittest tests.test_rust_tokens"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RustTokensTest(unittest.TestCase):
    def test_rust_tokens_json_is_not_stale(self):
        """A token changed in tokens.py and not exported: the Rust components
        would show the old colour. Run tools/export_tokens.py."""
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "export_tokens.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
