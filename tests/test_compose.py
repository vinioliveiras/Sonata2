"""Vini: ç couldn't be typed on U.S. International. python3 -m unittest tests.test_compose"""
import os
import tempfile
import unittest

from sonata2 import compose


class ComposeTest(unittest.TestCase):
    def setUp(self):
        self.f = os.path.join(tempfile.mkdtemp(), ".XCompose")

    def read(self):
        with open(self.f, encoding="utf-8") as f:
            return f.read()

    def test_new_file_keeps_the_locale_table(self):
        self.assertTrue(compose.ensure(self.f))
        text = self.read()
        self.assertTrue(text.splitlines()[1] == 'include "%L"')          # every other combination stays
        for r in compose.RULES:
            self.assertIn(r, text)
        compose.ensure(self.f)
        self.assertEqual(self.read(), text)                              # once: nothing added again

    def test_users_own_file_keeps_its_lines(self):
        with open(self.f, "w", encoding="utf-8") as f:
            f.write('include "%L"\n<Multi_key> <s> <s> : "ß"\n')
        compose.ensure(self.f)
        text = self.read()
        self.assertTrue(text.startswith('include "%L"\n<Multi_key> <s> <s> : "ß"\n'))
        self.assertEqual(text.count('include "%L"'), 1)
        self.assertIn(compose.RULES[0], text)

    def test_a_file_without_include_gets_it(self):
        with open(self.f, "w", encoding="utf-8") as f:
            f.write('<Multi_key> <s> <s> : "ß"')
        compose.ensure(self.f)
        self.assertTrue(self.read().startswith('include "%L"\n'))

    def test_users_own_cedilla_rule_left_alone(self):
        with open(self.f, "w", encoding="utf-8") as f:
            f.write('include "%L"\n<dead_acute> <c> : "ç"\n<dead_acute> <C> : "Ç"\n')
        before = self.read()
        compose.ensure(self.f)
        self.assertEqual(self.read(), before)


if __name__ == "__main__":
    unittest.main()
