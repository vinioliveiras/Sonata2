# Notes for Claude

Sonata 2 is a macOS-like Linux desktop: Python, GTK 4 / libadwaita and
Wayfire. Start with README.md (layout, development commands) and
docs/DESIGN.md (design rules).

## Text the user sees: always translated
- **Translations.** Sonata is in English, Portuguese (Brazil) and
  Portuguese (Portugal). Any new or changed string the user sees goes
  through `_()`, `ngettext()`, `N_()` or `pgettext()`, and must be added to
  **both** `sonata2/locale/pt_BR/` and `sonata2/locale/pt_PT/`. Read
  **docs/TRANSLATING.md** first: it covers the rules, the routine, the
  pt-BR / pt-PT glossary, and the places where longer text breaks the
  layout.
- **Check before committing:**
  - `python3 tools/i18n_missing.py sonata2/<area>/`
  - `python3 -m unittest tests.test_i18n tests.test_app_names_translated`

## Tests
- **Use the right interpreter.** Run tests with the interpreter that has
  PyGObject. UI tests need a display:
  `xvfb-run -a python3 -m unittest tests.<module>` (one module at a time).
- **A test for every bug, always.** Every bug that gets fixed -- reported
  by Vini or found while working -- gets a regression test in the same
  change, named after the bug (`test_<what_went_wrong>`), with a docstring
  saying what the user saw. Check it fails on the code before the fix and
  passes after. No bug fix is done without its test.
- **Every language.** After touching Settings, an app or any text, run
  `dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_smoke_languages`
  (about 90 s). It opens every Sonata app in English, pt-BR and pt-PT and
  walks and rebuilds every Settings section in each.
- **Plugin.** The Wayfire plugin (`wayfire-plugin/`) is C++. It builds with
  meson against Wayfire's headers; compile-check any change to it.

## Changes
- **Changelog.** Add a CHANGELOG.md line (under "Unreleased") for anything
  a user would notice.
- **Comments.** Comments explain why, in the project's voice. Keep the
  surrounding style.
