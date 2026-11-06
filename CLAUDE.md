# Notes for Claude

Sonata 2 is a macOS-like Linux desktop: Rust (gtk4-rs, GTK 4 /
libadwaita) on Wayfire. The code is the cargo workspace in `rust/` (one
multi-call binary, `sonata2-rs`, installed as `sonata2`); the Python
Sonata it was ported from is gone. Start with docs/GUIDE.md (layout,
development commands) and docs/DESIGN.md (design rules).

## Text the user sees: always translated
- **Translations.** Sonata is in English, Portuguese (Brazil) and
  Portuguese (Portugal). Any new or changed string the user sees goes
  through `tr!()`, `trn!()`, `trp!()` or `n_!()` / `nc_!()`, and must be
  added to **both** `locale/pt_BR/` and `locale/pt_PT/`. Read
  **docs/TRANSLATING.md** first: it covers the rules, the routine, the
  pt-BR / pt-PT glossary, and the places where longer text breaks the
  layout.
- **Check before committing** (from `rust/`, isolated as below):
  - `python3 ../tools/i18n_missing.py apps/<area>/` (one area's gaps)
  - `cargo test -p sonata-i18n` (every marked string, both languages)
  - `cargo test -p sonata2-rs desktop` (the apps' names and comments)

## Tests
- **Never on Vini's session.** Tests once rewrote his Dock, Launchpad and
  web apps (2026-11-04). Run them from `rust/` with a temporary HOME and
  XDG folders and the session's variables unset, never against his real
  ones:
  `env -u SONATA2_RS_SIDE_BY_SIDE -u SONATA2_RS_SESSION -u SONATA2_LAUNCHER -u WAYFIRE_SOCKET -u DBUS_SESSION_BUS_ADDRESS -u WAYLAND_DISPLAY -u DISPLAY -u GREETD_SOCK CARGO_HOME=$HOME/.cargo HOME=$(mktemp -d) XDG_DATA_HOME=$(mktemp -d) XDG_CONFIG_HOME=$(mktemp -d) XDG_RUNTIME_DIR=$(mktemp -d) cargo test -q -j 4 --workspace --no-fail-fast`
  (one crate: `-p <crate>`; long runs in the background). The GTK tests
  skip without a display. Don't run tests on his machine unless he asks,
  and never install.sh, `sonata2 restart` or anything that touches his
  ~/.config, ~/.local or his running session.
- **A test for every bug, always.** Every bug that gets fixed -- reported
  by Vini or found while working -- gets a regression test in the same
  change, named after the bug (`test_<what_went_wrong>`), with a `///`
  doc comment saying what the user saw. Check it fails on the code before
  the fix and passes after. No bug fix is done without its test.
- **Build checks:** `cargo check -q --workspace --tests` must pass.
- **Plugin.** The Wayfire plugin (`wayfire-plugin/`) is C++. It builds with
  meson against Wayfire's headers; compile-check any change to it.
- **Shell scripts** (install.sh, tools/*.sh): `bash -n`, and shellcheck
  when it's installed.

## Changes
- **Design.** Colours, radii, sizes are tokens in
  `rust/crates/sonata-ui/tokens.json`; CSS templates are
  `rust/crates/sonata-ui/css.json` (docs/DESIGN.md).
- **Settings and data never lost.** `~/.config/sonata2` and
  `~/.local/share/sonata2-data` are the user's: an install or update
  never replaces them (install.sh's upgrade: `sonata2 migrate`).
- **Changelog.** Add a CHANGELOG.md line (under "Unreleased") for anything
  a user would notice.
- **Comments.** Comments explain why, in the project's voice. Keep the
  surrounding style.
