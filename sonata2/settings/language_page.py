"""Settings > Language & Region (Vini): the system's language and its
formats -- as chosen on a first install -- apart from the keyboard, which
stays in Keyboard > Input Sources (Portuguese (Brazil) with a Portugal
keyboard). Sonata shows itself in the system's language (i18n.py)."""
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import config  # noqa: E402
from ..backend import locales, system  # noqa: E402
from ..i18n import N_, _  # noqa: E402

WEEK_STARTS = (("locale", N_("Region's default")), (6, N_("Sunday")), (0, N_("Monday")), (5, N_("Saturday")))


def _with(options, value, name=None):
    """`options` with `value` in it (a locale set elsewhere is still shown)."""
    if value and value not in [v for v, _n in options]:
        return options + [(value, name or value)]
    return options


class LanguagePage:
    def __init__(self, settings):
        self.settings = settings
        self.cur = {}

    def groups(self) -> list:
        from .app import combo_row, group
        lang_g = group(_("Language"), _("Sonata, your apps and the login screen use it from the next login."))
        fmt_g = group(_("Formats"), _("How dates, times, numbers and money are written."))
        kb_g = group()
        self.cur = locales.current()
        lang = locales.normalise(self.cur.get("LANG") or "en_US.UTF-8")
        region = locales.normalise(locales.formats_of(self.cur))
        self.state = {"lang": lang, "region": region}
        self.lang_row = combo_row(_("Language"), _with(locales.languages(), lang), lang,
                                  lambda v: self._set(lang=v))
        self.lang_row.set_enable_search(True)
        lang_g.add(self.lang_row)
        self.region_row = combo_row(_("Region"), _with(locales.regions(), region), region,
                                    lambda v: self._set(region=v))
        self.region_row.set_enable_search(True)
        fmt_g.add(self.region_row)
        self.sample_row = Adw.ActionRow(title=_("Example"), subtitle="…")
        self.sample_row.set_subtitle_selectable(True)
        fmt_g.add(self.sample_row)
        from ..calendar.window import DEFAULTS as CAL
        ws = config.load("calendar", CAL).get("week_start", "locale")
        fmt_g.add(combo_row(_("First day of the week"), [(v, _(n)) for v, n in WEEK_STARTS],
                            ws if ws in [v for v, _n in WEEK_STARTS] else "locale",
                            lambda v: self.settings._save("calendar", "week_start", v),
                            subtitle=_("Calendar")))
        kb = Adw.ActionRow(title=_("Keyboard"),
                           subtitle=_("Your keyboard layout is set apart: Keyboard > Input Sources."))
        go = Gtk.Button(label=_("Keyboard…"), valign=Gtk.Align.CENTER)
        go.connect("clicked", lambda *_a: self.settings.select("keyboard"))
        kb.add_suffix(go)
        kb_g.add(kb)
        self._sample()
        return [lang_g, fmt_g, kb_g]

    def _sample(self) -> None:
        region = self.state["region"]
        system.run_async(locales.sample, lambda text: self.sample_row.set_subtitle(
            text or _("Shown once the region's formats are installed (next login).")), region)

    def _set(self, lang=None, region=None) -> None:
        """A new language or region: the system's settings (root: Sonata's
        password prompt), then "log out now or later"."""
        before = dict(self.state)
        if lang:
            self.state["lang"] = lang
        if region:
            self.state["region"] = region
        cmd = locales.command(self.state["lang"], self.state["region"])
        if not cmd:
            return

        def run():
            try:
                return subprocess.run(["pkexec", "sh", "-c", cmd], capture_output=True, timeout=600).returncode == 0
            except (OSError, subprocess.SubprocessError):
                return False

        def done(ok):
            if not ok:
                from .app import show_quietly
                self.state.update(before)
                show_quietly(self.lang_row, before["lang"])
                show_quietly(self.region_row, before["region"])
                self.settings.toast(_("Couldn't change the language"))
                return
            self._sample()
            self.settings.ask_restart("session", _("The new language and formats"))
        system.run_async(run, done)
