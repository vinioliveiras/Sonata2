"""Settings' search reaches every setting, not only the sections (Vini):
any text a section shows -- a group's title, a row's title or subtitle, a
switch's label, a button -- finds it, and picking a result opens that
section scrolled to that setting, which lights up for a moment.

The index is read from the sections' source (the strings each _page_<part>
and the methods it calls put through _() / N_() / pgettext / ngettext),
not by building every section: building one starts things (a Bluetooth
search, password prompts, reads of the system) that searching mustn't.
Its texts are translated as they're searched (English, pt-BR, pt-PT), and
the English ones match too.

    hits = search.find("night shift")      # [Hit(sid, part, text)], best first
    search.reveal(page, hit.text)          # in the built section: scroll to it, light it up
"""
import ast
import os
import re
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import i18n  # noqa: E402
from ..i18n import _  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_HITS = 40
FLASH_MS = 1600
# a section's page modules (their whole text belongs to it)
MODULES = {"apps_page.py": "apps", "appicons_page.py": "appicons", "language_page.py": "language",
           "shortcuts_page.py": "shortcuts", "arrange.py": "displays"}
_PLACEHOLDER = re.compile(r"\{[^}]*\}|%\(?\w*\)?[sd]")


@dataclass
class Hit:
    sid: str        # the section to open
    part: str       # the part of it the text is in
    text: str       # as shown (translated)


def _strings(node) -> list:
    """(msgid, context) of every translatable string under `node`."""
    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        args = [a.value for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if name in ("_", "N_") and args:
            out.append((args[0], None))
        elif name == "ngettext" and args:
            out.append((args[0], None))
        elif name == "pgettext" and len(args) >= 2:
            out.append((args[1], args[0]))
    return out


def _calls(node) -> set:
    """Names of self.<method> used under `node` (called or passed as a callback)."""
    return {n.attr for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"}


def _index_source(app_src: str, modules: dict) -> dict:
    """{part: [(msgid, context)]}: every string each section can show."""
    tree = ast.parse(app_src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and
               any(isinstance(m, ast.FunctionDef) and m.name.startswith("_page_") for m in n.body))
    methods = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}
    index = {}
    for name, fn in methods.items():
        if not name.startswith("_page_"):
            continue
        part, seen, todo, found = name[len("_page_"):], set(), [name], []
        while todo:                                   # its own strings and its helpers' (not other sections')
            m = todo.pop()
            if m in seen or m not in methods:
                continue
            seen.add(m)
            found += _strings(methods[m])
            todo += [c for c in _calls(methods[m]) if not c.startswith("_page_")]
        index[part] = found
    for src, part in modules.items():
        index.setdefault(part, []).extend(_strings(ast.parse(src)))
    return index


_INDEX = None


def index() -> dict:
    """{part: [(msgid, context)]}, read once."""
    global _INDEX
    if _INDEX is None:
        with open(os.path.join(HERE, "app.py"), encoding="utf-8") as f:
            app_src = f.read()
        mods = {}
        for fname, part in MODULES.items():
            try:
                with open(os.path.join(HERE, fname), encoding="utf-8") as f:
                    mods[f.read()] = part
            except OSError:
                pass
        _INDEX = _index_source(app_src, mods)
    return _INDEX


def _shown(msgid: str, ctx) -> str:
    """As the section shows it (strings found in its source, already in the
    catalogs: looked up as pgettext would, not marked again here)."""
    if ctx:
        got = i18n.catalog().get(ctx + i18n.CONTEXT_SEP + msgid)
        if isinstance(got, str) and got:
            return got
    return _(msgid)


def _searchable(text: str) -> bool:
    """Real words a person reads (not a lone placeholder, symbol or letter)."""
    bare = _PLACEHOLDER.sub("", text).strip(" …:.,-–—()")
    return len(bare) >= 3 and any(c.isalpha() for c in bare)


def find(query: str, section_of=lambda p: p, limit: int = MAX_HITS) -> list:
    """Every text that has all of the query's words, best first: a text that
    starts with the query, then one with a word starting with it, then the
    rest; shorter texts first (a setting's name over a sentence about it)."""
    words = query.casefold().split()
    if not words:
        return []
    hits, seen = [], set()
    for part, strings in index().items():
        sid = section_of(part)
        for msgid, ctx in strings:
            text = _shown(msgid, ctx)
            if not _searchable(text):
                continue
            hay = f"{text} {msgid}".casefold()
            if not all(w in hay for w in words):
                continue
            key = (sid, text)
            if key in seen:
                continue
            seen.add(key)
            low, q = text.casefold(), " ".join(words)
            rank = 0 if low.startswith(q) else 1 if re.search(r"\b" + re.escape(words[0]), low) else 2
            hits.append((rank, len(text), Hit(sid, part, _PLACEHOLDER.sub("…", text).strip())))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits[:limit]]


# -- in the built section ----------------------------------------------------------------------
def _texts(w) -> list:
    """What `w` shows as text."""
    out = []
    if isinstance(w, Adw.PreferencesGroup):
        out += [w.get_title() or "", w.get_description() or ""]
    if isinstance(w, Adw.PreferencesRow):
        out.append(w.get_title() or "")
    if isinstance(w, Adw.ActionRow):
        out.append(w.get_subtitle() or "")
    if isinstance(w, Adw.ExpanderRow):
        out.append(w.get_subtitle() or "")
    if isinstance(w, Gtk.Label):
        out.append(w.get_text() or "")
    if isinstance(w, (Gtk.Button, Gtk.CheckButton)) and not isinstance(w, Gtk.MenuButton):
        out.append(w.get_label() or "")
    return out


def _same(a: str, b: str) -> bool:
    a = _PLACEHOLDER.sub("…", a).strip().casefold()
    b = _PLACEHOLDER.sub("…", b).strip().casefold()
    if not a or not b:
        return False
    if "…" in b:                                       # a text with a value in it: its fixed parts
        return all(p.strip() in a for p in b.split("…") if p.strip())
    return a == b


def locate(root: Gtk.Widget, text: str):
    """The widget in `root` showing `text` (the first one, in reading order)."""
    stack = [root]
    while stack:
        w = stack.pop(0)
        if any(_same(t, text) for t in _texts(w)):
            return w
        c, kids = w.get_first_child(), []
        while c is not None:
            kids.append(c)
            c = c.get_next_sibling()
        stack[0:0] = kids                              # depth first, in order
    return None


def target_of(w: Gtk.Widget) -> Gtk.Widget:
    """What lights up for a found text: its row, or its group."""
    for cls in (Adw.PreferencesRow, Gtk.ListBoxRow, Adw.PreferencesGroup):
        a = w if isinstance(w, cls) else w.get_ancestor(cls)
        if a is not None:
            return a
    return w


def reveal(page: Gtk.Widget, text: str, flash_ms: int = FLASH_MS):
    """Scroll `page` (a built section) to the setting showing `text` and
    light it up for a moment. The widget lit up, or None (not on this page:
    the section opened is all there is)."""
    found = locate(page, text)
    if found is None:
        return None
    target = target_of(found)
    scroller = target.get_ancestor(Gtk.ScrolledWindow)
    if scroller is not None and scroller.get_child() is not None:
        ok, pt = target.compute_point(scroller.get_child(), _point(0, 0))
        if ok:
            adj = scroller.get_vadjustment()
            want = pt.y - max(0.0, (adj.get_page_size() - target.get_height()) / 3)
            adj.set_value(max(adj.get_lower(), min(want, adj.get_upper() - adj.get_page_size())))
    target.add_css_class("st-found")
    GLib.timeout_add(flash_ms, lambda: (target.remove_css_class("st-found"), False)[1])
    return target


def _point(x, y):
    from gi.repository import Graphene
    p = Graphene.Point()
    p.x, p.y = x, y
    return p
