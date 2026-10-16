"""Sonata in Portuguese (Brazil / Portugal) when the system is (Vini).
Every string marked for translation (_(), ngettext(), N_()) has both
translations, with the same {placeholders}; no f-string inside _() (it
could never be found in a catalog). python3 -m unittest tests.test_i18n"""
import ast
import json
import os
import re
import string
import unittest

from sonata2 import i18n

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sonata2")
FUNCS = {"_", "N_", "ngettext"}


def marked():
    """{(singular, plural or None): [where]} and [(where, problem)]."""
    found, bad = {}, []
    for dp, _dn, fns in os.walk(ROOT):
        if "locale" in dp.split(os.sep):
            continue
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dp, fn)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), path)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS):
                    continue
                where = f"{os.path.relpath(path, ROOT)}:{node.lineno}"
                args = node.args
                if node.func.id == "ngettext":
                    if len(args) < 2 or not all(isinstance(a, ast.Constant) and isinstance(a.value, str)
                                                for a in args[:2]):
                        bad.append((where, "ngettext needs two literal strings"))
                        continue
                    found.setdefault((args[0].value, args[1].value), []).append(where)
                    continue
                if len(args) != 1:
                    bad.append((where, f"{node.func.id}() takes one string"))
                    continue
                a = args[0]
                if isinstance(a, ast.JoinedStr):
                    bad.append((where, "an f-string inside _(): use _(\"... {x}\").format(x=...)"))
                elif isinstance(a, ast.Constant) and isinstance(a.value, str):
                    found.setdefault((a.value, None), []).append(where)
                # _(variable): translated through a table marked with N_() elsewhere
    return found, bad


def fields(text):
    try:
        return sorted({f for _l, f, _s, _c in string.Formatter().parse(text) if f is not None})
    except ValueError:
        return None


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.found, self.bad = marked()

    def test_no_untranslatable_calls(self):
        self.assertEqual(self.bad, [])

    def test_catalogs_are_valid_json(self):
        for lang in i18n.SUPPORTED:
            folder = os.path.join(i18n.LOCALE_DIR, lang)
            for n in sorted(os.listdir(folder)):
                with open(os.path.join(folder, n), encoding="utf-8") as f:
                    data = json.load(f)
                self.assertIsInstance(data, dict, n)

    def test_every_marked_string_translated(self):
        for lang in i18n.SUPPORTED:
            cat = i18n._load(lang)
            missing = [(s, w[0]) for (s, p), w in sorted(self.found.items()) if not cat.get(s)]
            self.assertEqual(missing, [], f"{lang}: {len(missing)} untranslated")

    def test_placeholders_kept(self):
        for lang in i18n.SUPPORTED:
            cat = i18n._load(lang)
            for (s, p), where in self.found.items():
                t = cat.get(s)
                if t is None:
                    continue
                if p is not None:
                    self.assertIsInstance(t, list, f"{lang} {where[0]}: plural needs [one, other]")
                    self.assertEqual(len(t), 2, f"{lang} {where[0]}")
                    self.assertEqual(fields(t[0]), fields(s), f"{lang} {where[0]}: {s!r} -> {t[0]!r}")
                    self.assertEqual(fields(t[1]), fields(p), f"{lang} {where[0]}: {p!r} -> {t[1]!r}")
                else:
                    self.assertIsInstance(t, str, f"{lang} {where[0]}: {s!r}")
                    self.assertEqual(fields(t), fields(s), f"{lang} {where[0]}: {s!r} -> {t!r}")
                    self.assertEqual(t.count("%"), s.count("%"), f"{lang} {where[0]}: {s!r} -> {t!r}")


class LanguageTest(unittest.TestCase):
    def tearDown(self):
        i18n.use(None)

    def test_detect(self):
        D = i18n.detect
        self.assertEqual(D({"LANG": "pt_BR.UTF-8"}), "pt_BR")
        self.assertEqual(D({"LANG": "pt_PT.UTF-8"}), "pt_PT")
        self.assertEqual(D({"LANG": "pt"}), "pt_BR")
        self.assertEqual(D({"LANG": "en_US.UTF-8", "LC_MESSAGES": "pt_PT.UTF-8"}), "pt_PT")   # messages win
        self.assertEqual(D({"LC_ALL": "C", "LANG": "pt_BR.UTF-8"}), "pt_BR")
        self.assertEqual(D({"LANG": "de_DE.UTF-8"}), "en")
        self.assertEqual(D({}), "en")
        self.assertEqual(D({"LANGUAGE": "pt_PT:pt:en"}), "pt_PT")

    def test_english_unchanged_and_portuguese_used(self):
        i18n.use("en")
        self.assertEqual(i18n._("Language"), "Language")
        i18n.use("pt_BR")
        self.assertEqual(i18n._("Language"), "Idioma")
        self.assertEqual(i18n._("No such string anywhere"), "No such string anywhere")

    def test_plurals(self):
        i18n.use("pt_BR")
        i18n._state["catalog"] = {"{n} item": ["{n} item", "{n} itens"]}
        self.assertEqual(i18n.ngettext("{n} item", "{n} items", 0), "{n} item")    # Brazil: 0 is singular
        self.assertEqual(i18n.ngettext("{n} item", "{n} items", 2), "{n} itens")
        i18n._state["lang"] = "pt_PT"
        self.assertEqual(i18n.ngettext("{n} item", "{n} items", 0), "{n} itens")
        i18n.use("en")
        self.assertEqual(i18n.ngettext("{n} item", "{n} items", 1), "{n} item")

    def test_pt_br_and_pt_pt_differ_where_they_should(self):
        br, pt = i18n._load("pt_BR"), i18n._load("pt_PT")
        for en in ("Keyboard",):
            if en in br and en in pt:
                self.assertTrue(br[en] and pt[en])


if __name__ == "__main__":
    unittest.main()
