# Brief for building a Sonata app

Sonata 2 is a macOS-Ventura-style Linux desktop shell: Python 3, GTK 4,
libadwaita, on Wayfire. Repo: /home/claude/sonata2 (package `sonata2`).
Read docs/DESIGN.md, sonata2/calculator/window.py, sonata2/textedit/window.py
and sonata2/preview/window.py first: they are the patterns to follow.

## Rules
- Code, comments, docs in English. Module docstring explains the app like
  the existing ones (what macOS app it mirrors, keys, behaviour).
- Every visual value comes from tokens (sonata2/ui/tokens.py); CSS is
  registered with `ui.register("""...%(token)s...""", key="myapp")`. Never
  hard-code colours/radii/fonts. Light and Dark must both work (automatic
  when you use tokens). No GNOME/KDE dependencies.
- Window: `class XWindow(Gtk.ApplicationWindow)`; call `ui.window.standard(self)`.
  The title bar is drawn by the compositor (glass): do NOT add your own
  title bar or traffic lights. Tools go on `ui.window.glass_toolbar(self,
  start=[(icon, tooltip, callback)], end=[...])` returned widget, placed
  first in the window's vertical box. Sidebars use the `sidebar_material`
  token (translucent) like Files.
- Menus: `ui.menu.popup(widget, [[ui.menu.Item("Label", callback,
  enabled=bool)], ...], at=(x, y), glass=True, passthrough=True)` for
  right-click. Alerts: `ui.dialog.alert(heading, body, [(id, label,
  "default"|"destructive"|"")], on_response, parent=self)`.
  Popover panels: `ui.panel.popup(anchor_widget, child)`.
  Sliders: `ui.controls.slider(value, on_change, lower=, upper=)`.
  Switches: `ui.controls.switch(active, on_change)`.
- Keyboard: Ctrl or Super act as ⌘ (see terminal/window.py `_key`).
- Settings/data: `sonata2.config.load(name, DEFAULTS)` / `config.save`/
  `config.update` (~/.config/sonata2/<name>.json). App data files go in
  `GLib.get_user_data_dir()/sonata2/<app>/`.
- Blocking work (subprocess, file scans, D-Bus sync calls) off the main
  loop: `sonata2.backend.system.run_async(func, callback, *args)`.
- Optional libraries (GStreamer, mutagen, UDisks typelibs...) must be
  imported lazily and degrade gracefully with a clear message in the UI
  when missing; nothing may crash at import.
- Module layout: `sonata2/<app>/__init__.py` (empty) and
  `sonata2/<app>/window.py` exposing:
  - `APP_ID = "io.github.vinioliveiras.sonata2.<app>"`
  - `open_windows(app, paths)` (or `open_paths`): open/raise windows; with
    no paths and a window already open, present it.
  - `<app>_desktop_file(command)` writing the .desktop via
    `from ..apps import write_desktop_file` (Name, Comment, Icon (a
    freedesktop icon name), Categories, MimeType if it opens files,
    `Exec={command} <app> %F`, `Actions=new-window;` +
    `[Desktop Action new-window]` with `Exec={command} <app> --new-window`
    when multiple windows make sense).
- Do NOT edit shared files (sonata2/__main__.py, sonata2/ui/*, apps.py,
  install.sh, config/*). If you need a shared change, describe it in your
  final report instead; the lead wires the app into __main__.py.
- Tests: `tests/test_<app>.py` (unittest, like tests/test_calculator.py),
  run with:
  `cd /home/claude/sonata2 && timeout 115 env PYTHONPATH=. xvfb-run -a /opt/gtk422/bin/python -m unittest tests/test_<app>.py`
  (GStreamer/UDisks/Vte typelibs are NOT available in this test
  environment: test the logic and the degraded UI.) Take a screenshot to
  check the look: run a small script under
  `xvfb-run -a -s "-screen 0 1200x800x24"` that presents the window with
  sample data, then `import -window root out.png`, and look at it (Read
  the png). Check Light and Dark (`ui.force_appearance("dark")`).
- Bug fixes: add a regression test to `tests/test_regressions.py` (one per
  reported bug, named after it) -- fixed bugs must not come back.
- Performance: lazy views (Gtk.ListView/ColumnView/GridView for long
  lists), no polling faster than needed, pause timers when the window is
  hidden/minimized.
- Final report (your last message): files created, how to launch
  (component name), the exact __main__.py wiring needed (import + call),
  desktop-file function name, anything you could not finish.
