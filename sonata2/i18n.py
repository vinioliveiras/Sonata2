"""Sonata in the system's language (Vini): Portuguese (Brazil) and
Portuguese (Portugal) for now, English otherwise. The language is the
session's (LC_ALL, LC_MESSAGES, LANG: Settings > Language & Region sets
the system's), read once per process -- a new one shows at the next login.

    from ..i18n import _, ngettext
    _("Move to Trash")                          # "Mover para a Lixeira" / "Mover para o Lixo"
    _("Copy {name}").format(name=n)             # placeholders, never f-strings inside _()
    ngettext("{n} item", "{n} items", n).format(n=n)

Catalogs: sonata2/locale/<lang>/*.json, {"English": "translation"}; a
plural is {"singular": ["one", "other"]} keyed by the singular. Every area
of Sonata keeps its own file (they're merged when loaded). A string that
isn't translated shows in English.

N_("...") marks a string translated later, where it is shown (tables
built at import that also serve as keys)."""
import json
import os

LOCALE_DIR = os.path.join(os.path.dirname(__file__), "locale")
SUPPORTED = ("pt_BR", "pt_PT")
_state = {"lang": None, "catalog": None}


def detect(env=None) -> str:
    """The language Sonata shows: "pt_BR", "pt_PT" or "en"."""
    env = os.environ if env is None else env
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = (env.get(var) or "").strip()
        if not value or value in ("C", "POSIX"):
            continue
        first = value.split(":")[0]                   # LANGUAGE: "pt_BR:pt:en"
        code = first.split(".")[0].split("@")[0]
        lang, _, region = code.partition("_")
        if lang == "pt":
            return "pt_PT" if region.upper() == "PT" else "pt_BR"   # plain "pt": Brazil's, the most spoken
        return "en"
    return "en"


def language() -> str:
    if _state["lang"] is None:
        _state["lang"] = detect()
    return _state["lang"]


def _load(lang: str) -> dict:
    out = {}
    folder = os.path.join(LOCALE_DIR, lang)
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError:
        return out
    for n in names:
        try:
            with open(os.path.join(folder, n), encoding="utf-8") as f:
                part = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(part, dict):
            out.update({k: v for k, v in part.items() if isinstance(k, str) and v})
    return out


def catalog() -> dict:
    if _state["catalog"] is None:
        lang = language()
        _state["catalog"] = _load(lang) if lang in SUPPORTED else {}
    return _state["catalog"]


def use(lang: str) -> None:
    """Switch the language of this process (tests; previews)."""
    _state["lang"], _state["catalog"] = lang, None


def _(text: str) -> str:
    if not text:
        return text
    got = catalog().get(text)
    if isinstance(got, list):                        # a plural's singular asked alone
        got = got[0]
    return got if isinstance(got, str) and got else text


def ngettext(singular: str, plural: str, n: int) -> str:
    got = catalog().get(singular)
    if isinstance(got, list) and len(got) >= 2:
        one = n == 1 or (language() == "pt_BR" and n == 0)    # Brazil: "0 item"; Portugal: "0 itens"
        return got[0] if one else got[1]
    return singular if n == 1 else plural


def N_(text: str) -> str:
    return text
