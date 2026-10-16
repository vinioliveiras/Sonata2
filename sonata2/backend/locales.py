"""Language & Region (Settings): the system's language and its formats --
dates, times, numbers, money, measures, paper -- as on a first install,
apart from the keyboard (Vini: Portuguese (Brazil) with a Portugal
keyboard). Written with localectl (systemd-localed: every program, the
login screen too), the locale generated first when the system lacks it
(/etc/locale.gen + locale-gen: Arch, Debian, Ubuntu). It takes effect at
the next login, Sonata's own language with it (sonata2/i18n.py follows
the session's).

    current()                  # {"LANG": "pt_BR.UTF-8", "LC_TIME": ...}
    languages() / regions()    # [(locale, name in its own language)]
    command(lang, formats)     # the root shell script that sets them
    sample(locale)             # "Friday, 16 October 2026 · 17:05 · 1,234.56"
"""
import os
import re
import shutil
import subprocess

FORMAT_VARS = ("LC_TIME", "LC_NUMERIC", "LC_MONETARY", "LC_MEASUREMENT", "LC_PAPER")

# (locale, the language's own name): Sonata's translations first
LANGUAGES = [
    ("en_US.UTF-8", "English (United States)"), ("en_GB.UTF-8", "English (United Kingdom)"),
    ("pt_BR.UTF-8", "Português (Brasil)"), ("pt_PT.UTF-8", "Português (Portugal)"),
    ("es_ES.UTF-8", "Español (España)"), ("es_MX.UTF-8", "Español (México)"),
    ("fr_FR.UTF-8", "Français"), ("de_DE.UTF-8", "Deutsch"), ("it_IT.UTF-8", "Italiano"),
    ("nl_NL.UTF-8", "Nederlands"), ("pl_PL.UTF-8", "Polski"), ("ru_RU.UTF-8", "Русский"),
    ("uk_UA.UTF-8", "Українська"), ("tr_TR.UTF-8", "Türkçe"), ("ja_JP.UTF-8", "日本語"),
    ("zh_CN.UTF-8", "中文 (简体)"), ("ko_KR.UTF-8", "한국어"),
]
# formats by region (the same codes: a locale's formats are its region's)
REGIONS = [
    ("en_US.UTF-8", "United States"), ("en_GB.UTF-8", "United Kingdom"), ("en_IE.UTF-8", "Ireland"),
    ("pt_BR.UTF-8", "Brasil"), ("pt_PT.UTF-8", "Portugal"), ("es_ES.UTF-8", "España"),
    ("es_MX.UTF-8", "México"), ("es_AR.UTF-8", "Argentina"), ("fr_FR.UTF-8", "France"),
    ("de_DE.UTF-8", "Deutschland"), ("it_IT.UTF-8", "Italia"), ("nl_NL.UTF-8", "Nederland"),
    ("pl_PL.UTF-8", "Polska"), ("ja_JP.UTF-8", "日本"), ("zh_CN.UTF-8", "中国"),
]
_SAFE = re.compile(r"^[A-Za-z]{2,3}(_[A-Za-z]{2})?(\.UTF-8)?(@[a-z]+)?$")


def valid(loc: str) -> bool:
    return bool(loc) and bool(_SAFE.match(loc))


def _parse(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("System Locale:"):
            line = line.split(":", 1)[1].strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            k = k.strip()
            if k == "LANG" or k.startswith("LC_"):
                out[k] = v.strip().strip('"')
    return out


def current() -> dict:
    """The system's locale settings: its file (/etc/locale.conf; Debian's
    /etc/default/locale) -- read directly, the page opens without waiting
    for a program -- else localectl."""
    for path in ("/etc/locale.conf", "/etc/default/locale"):
        try:
            with open(path, encoding="utf-8") as f:
                got = _parse(f.read())
        except OSError:
            continue
        if got:
            return got
    if shutil.which("localectl"):
        try:
            out = subprocess.run(["localectl", "status"], capture_output=True, text=True, timeout=5).stdout
            got = _parse(out.split("VC Keymap:")[0])     # (not the keyboard's lines)
            if got:
                return got
        except (OSError, subprocess.SubprocessError):
            pass
    return {"LANG": os.environ.get("LANG", "en_US.UTF-8")}


def installed() -> set:
    """Locales the system can use now ("pt_BR.UTF-8"), normalised."""
    names = []
    for cmd in (["localectl", "list-locales"], ["locale", "-a"]):
        if shutil.which(cmd[0]):
            try:
                names = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.split()
            except (OSError, subprocess.SubprocessError):
                names = []
            if names:
                break
    return {normalise(n) for n in names}


def normalise(name: str) -> str:
    """"pt_BR.utf8" -> "pt_BR.UTF-8" (locale -a's spelling -> localectl's)."""
    base, _, enc = name.partition(".")
    enc, _, mod = enc.partition("@")
    out = base + (".UTF-8" if enc.lower().replace("-", "") == "utf8" else ("." + enc if enc else ""))
    return out + ("@" + mod if mod else "")


def languages():
    return list(LANGUAGES)


def regions():
    return list(REGIONS)


def formats_of(settings: dict) -> str:
    """The formats region in effect: LC_TIME's, else the language's."""
    return settings.get("LC_TIME") or settings.get("LANG") or "en_US.UTF-8"


def command(lang: str, formats: str, have=None) -> str:
    """The script (run as root) that sets the language and the formats:
    the missing locales generated first where the system generates them
    (/etc/locale.gen), then localectl. None for an unsafe name."""
    if not (valid(lang) and valid(formats)):
        return None
    have = installed() if have is None else have
    need = [x for x in dict.fromkeys((lang, formats)) if x not in have]
    steps = []
    if need and os.path.exists("/etc/locale.gen") and shutil.which("locale-gen"):
        for loc in need:
            base, _, _enc = loc.partition(".")
            # uncomment "pt_BR.UTF-8 UTF-8", or add it when the file doesn't list it
            steps.append(f"(grep -q '^{loc} UTF-8' /etc/locale.gen || "
                         f"sed -i 's/^# *{re.escape(loc)} UTF-8/{loc} UTF-8/' /etc/locale.gen; "
                         f"grep -q '^{loc} UTF-8' /etc/locale.gen || echo '{loc} UTF-8' >> /etc/locale.gen)")
        steps.append("locale-gen")
    vars_ = [f"LANG={lang}"] + [f"{v}={formats}" for v in FORMAT_VARS if formats != lang]
    steps.append("localectl set-locale " + " ".join(vars_))
    return " && ".join(steps)


def sample(loc: str) -> str:
    """How dates, times and numbers read in `loc` (empty when it isn't
    installed yet): the formats preview."""
    if not valid(loc):
        return ""
    env = dict(os.environ, LC_ALL=loc)
    try:
        date = subprocess.run(["date", "+%A, %x · %X"], env=env, capture_output=True, text=True,
                              timeout=3).stdout.strip()
        num = subprocess.run(["printf", "%'.2f", "1234567.89"], env=env, capture_output=True, text=True,
                             timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{date} · {num}" if date else ""
