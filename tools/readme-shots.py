#!/usr/bin/env python3
"""README screenshots: renders Sonata's apps with invented demo data and
saves framed WebP pictures (wallpaper, macOS-like title bar, rounded corners, drop
shadow) to docs/screenshots/.

Usage (from the repository root; needs Xvfb, GTK 4, libadwaita, Pillow):

    xvfb-run -a -s "-screen 0 1920x1200x24" python3 tools/readme-shots.py
    xvfb-run -a -s "-screen 0 1920x1200x24" python3 tools/readme-shots.py files notes-dark
    python3 tools/readme-shots.py --list

Privacy: nothing of the machine it runs on may show. Every capture runs in
its own process with a throwaway HOME (a generated demo home: sample
files, pictures, notes, songs), XDG_* dirs inside it, USER/LOGNAME "demo",
a fake host name, and fake back ends (Task Manager's /proc sampler,
Disk Manager's UDisks client). Before saving, every visible label is
checked for leaks (user names, paths, IP/MAC addresses); a hit fails the
shot. Still look at the pictures before committing them.

Windows are captured by rendering their own snapshot (no screen grab), so
translucent parts (sidebars, toolbars) show the wallpaper like in the
session. The title bar the compositor draws for Sonata's apps is faked
here (tokens: titlebar_bg/titlebar_text, the traffic-light artwork in
sonata2/data/decor); Files and Settings draw their own.

Full-screen shots (desktop, desktop-dark, files-full, settings-full,
launchpad, controlcenter; 1920x1080) put the real menu bar, Dock, desktop
icons, Launchpad and Control Center on the wallpaper, with demo apps
(desktop entries in the shot's own data folder), fake open windows (Dock
dots, the menu bar's app) and fake system state (Wi-Fi, battery, sound,
Now Playing). Their glass shows the picture behind blurred and saturated
like the compositor's blur. `hero` (four windows overlapping) is made only
when named."""
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs", "screenshots")
sys.path.insert(0, ROOT)

TITLEBAR_H = 28              # the compositor's title bar (config/wayfire.ini)
MARGIN = 48                  # wallpaper around a framed window
RADIUS = 10                  # r_window token
MAX_BYTES = 350 * 1024
# Sonata's default wallpaper (wallpapers.py "Mountains"): day in Light, night in Dark


def wall(size, dark: bool = False):
    """The default wallpaper cover-fit to `size` (RGBA), as the desktop shows it."""
    from PIL import Image, ImageOps
    from sonata2 import wallpapers
    path = wallpapers.DEFAULT.dark if dark else wallpapers.DEFAULT.light
    return ImageOps.fit(Image.open(path).convert("RGB"), size, Image.LANCZOS, centering=(0.5, 0.45)).convert("RGBA")


# name -> (appearance, window size); the capture function is shot_<base name>
SHOTS = {
    "files": ("light", (1060, 580)),
    "files-dark": ("dark", (1060, 580)),
    "settings": ("light", (980, 660)),
    "notes": ("light", (1080, 660)),
    "notes-dark": ("dark", (1080, 660)),
    "reminders": ("light", (1080, 660)),
    "calendar": ("light", (1120, 720)),
    "taskmanager": ("light", (1060, 680)),
    "taskmanager-dark": ("dark", (1060, 680)),
    "taskmanager-performance": ("light", (1060, 680)),
    "diskmanager": ("light", (1000, 640)),
    "textedit": ("light", (900, 600)),
    "preview": ("light", (1000, 602)),
    "calculator": ("light", None),
    "terminal": ("light", (820, 480)),
}

# nothing like these may be on screen (checked on every visible label)
BANNED = [re.compile(p, re.I) for p in (
    r"claude", r"anthropic", r"vinicius", r"scratchpad", r"\broot\b", r"/tmp/", r"/home/(?!demo\b)",
    r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", r"\b[0-9a-f]{2}(:[0-9a-f]{2}){5}\b")]


# == demo data (generated, nothing real) ===========================================================
def _lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _stops_at(stops, t):
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            return _lerp(c0, c1, (t - t0) / (t1 - t0) if t1 > t0 else 0)
    return stops[-1][1]


def gradient(size, angle, stops, step=4):
    """A CSS-like linear-gradient(angle, stops) as a Pillow RGB image:
    computed on a grid `step` times smaller, then smoothly scaled up."""
    from PIL import Image
    w, h = size
    sw, sh = max(2, w // step), max(2, h // step)
    a = math.radians(angle)
    dx, dy = math.sin(a), -math.cos(a)              # CSS: 0deg points up, 90deg right
    length = abs(sw * dx) + abs(sh * dy)
    data = [_stops_at(stops, ((x + .5 - sw / 2) * dx + (y + .5 - sh / 2) * dy) / length + .5)
            for y in range(sh) for x in range(sw)]
    im = Image.new("RGB", (sw, sh))
    im.putdata(data)
    return im.resize((w, h), Image.BICUBIC)


def landscape(path, size, seed, sky, sun, hills):
    """A made-up landscape photo: sky gradient, sun, layered hills."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = size
    rnd = random.Random(seed)
    im = gradient(size, 180, ((0, sky[0]), (0.62, sky[1]), (1, sky[1])), step=8).convert("RGBA")
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    sx, sy, r = w * rnd.uniform(.25, .75), h * rnd.uniform(.28, .45), h * .07
    d.ellipse([sx - r * 3, sy - r * 3, sx + r * 3, sy + r * 3], fill=sun + (70,))
    glow = glow.filter(ImageFilter.GaussianBlur(r))
    im.alpha_composite(glow)
    d = ImageDraw.Draw(im)
    d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=sun + (255,))
    for i, col in enumerate(hills):
        base = h * (.55 + .13 * i)
        amp = h * (.09 - .015 * i)
        f1, f2, p = rnd.uniform(1.2, 2.4), rnd.uniform(3, 6), rnd.uniform(0, 6)
        pts = [(x, base - amp * (math.sin(x / w * math.pi * f1 + p) * .7 + math.sin(x / w * math.pi * f2) * .3))
               for x in range(0, w + 8, 8)]
        d.polygon(pts + [(w, h), (0, h)], fill=col + (255,))
    im.convert("RGB").save(path, quality=88)


def cover(path, seed, c1, c2, style):
    """Album art: a diagonal gradient and a simple motif."""
    from PIL import Image, ImageDraw, ImageFilter
    s = 480
    rnd = random.Random(seed)
    im = gradient((s, s), 135, ((0, c1), (1, c2)), step=6).convert("RGBA")
    over = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    if style == "sun":
        d.ellipse([s * .25, s * .25, s * .75, s * .75], fill=(255, 255, 255, 90))
        for i in range(6):
            y = s * .55 + i * 18
            d.rectangle([0, y, s, y + 7], fill=c2 + (255,))
    elif style == "rings":
        for i in range(7):
            r = 30 + i * 32
            d.ellipse([s / 2 - r, s / 2 - r, s / 2 + r, s / 2 + r], outline=(255, 255, 255, 150 - i * 18), width=5)
    elif style == "bokeh":
        for _ in range(26):
            x, y, r = rnd.uniform(0, s), rnd.uniform(0, s), rnd.uniform(12, 60)
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rnd.randint(25, 80)))
        over = over.filter(ImageFilter.GaussianBlur(3))
    elif style == "stripes":
        for i in range(-s, s, 44):
            d.polygon([(i, 0), (i + 22, 0), (i + 22 + s, s), (i + s, s)], fill=(255, 255, 255, 45))
    elif style == "peaks":
        d.polygon([(0, s), (s * .35, s * .38), (s * .55, s * .62), (s * .75, s * .3), (s, s)], fill=(20, 20, 40, 120))
        d.ellipse([s * .62, s * .12, s * .8, s * .3], fill=(255, 250, 230, 200))
    elif style == "grid":
        for i in range(0, s, 40):
            d.line([(i, 0), (i, s)], fill=(255, 255, 255, 40), width=2)
            d.line([(0, i), (s, i)], fill=(255, 255, 255, 40), width=2)
        d.rectangle([s * .3, s * .3, s * .7, s * .7], outline=(255, 255, 255, 200), width=8)
    im.alpha_composite(over)
    im.convert("RGB").save(path, quality=90)


ALBUMS = [  # (album, artist, year, genre, colours, motif, songs)
    ("Blue Hour", "Oslo Drive", 2024, "Electronic", ((28, 60, 140), (120, 180, 255)), "rings",
     ["Night Bus", "Low Tide", "Glass Hands", "Neon Rain", "Afterglow"]),
    ("Circuit Garden", "Kilo Echo", 2023, "Electronic", ((20, 120, 90), (170, 240, 120)), "grid",
     ["Seedlings", "Wireframe", "Photosynthesis", "Loop Garden"]),
    ("Golden Thread", "Ada Moreau", 2022, "Jazz", ((120, 60, 20), (250, 200, 90)), "stripes",
     ["Brass & Linen", "Slow Waltz", "The Long Way", "Café at Nine"]),
    ("Northern Lights", "The Tidewater", 2025, "Indie", ((10, 30, 60), (60, 220, 170)), "peaks",
     ["Aurora", "Cold Harbour", "Fjord", "Driftwood", "Polar Night"]),
    ("Paper Lanterns", "Aria Vale", 2021, "Pop", ((230, 90, 110), (255, 200, 140)), "bokeh",
     ["Lanterns", "Summer Letters", "Kite", "Rooftops"]),
    ("Summer Static", "Juniper Fields", 2024, "Rock", ((240, 120, 40), (250, 220, 80)), "sun",
     ["Heatwave", "Static", "Highway 9", "Sunburn", "Fade Out"]),
    ("Velvet Hours", "Mira Sol", 2020, "R&B", ((70, 20, 90), (200, 80, 160)), "rings",
     ["Velvet", "Midnight Call", "Honey", "Slow Motion"]),
    ("City of Glass", "Lowlight", 2023, "Alternative", ((40, 40, 50), (140, 150, 170)), "grid",
     ["Skyline", "Reflections", "Elevator", "Glass City"]),
    ("Wildflower Season", "Maren Holt", 2025, "Folk", ((90, 140, 60), (240, 210, 150)), "bokeh",
     ["Meadow", "Pollen", "Old Road", "Evening Song"]),
    ("Deep Field", "Parallax", 2022, "Ambient", ((5, 10, 30), (80, 60, 160)), "peaks",
     ["Hubble", "Redshift", "Dark Matter", "Deep Field"]),
]

PICTURES = [  # (file, sky top, sky bottom, sun, hills far..near)
    ("Lake at dusk.jpg", (40, 50, 120), (250, 150, 110), (255, 230, 170), [(90, 70, 120), (60, 45, 90), (30, 25, 55)]),
    ("Alpine morning.jpg", (60, 140, 220), (200, 230, 250), (255, 250, 220), [(150, 180, 210), (80, 130, 110), (40, 90, 60)]),
    ("Desert road.jpg", (230, 120, 60), (255, 210, 130), (255, 245, 200), [(200, 110, 70), (160, 80, 50), (110, 50, 35)]),
    ("Forest valley.jpg", (120, 190, 230), (230, 240, 220), (255, 255, 230), [(120, 170, 140), (60, 120, 80), (30, 80, 50)]),
    ("Night coast.jpg", (10, 15, 45), (60, 60, 120), (240, 240, 255), [(40, 50, 90), (25, 30, 60), (10, 12, 30)]),
]


def make_home(base: str) -> str:
    """The demo home: <base>/home/demo with sample files. Returns its path."""
    home = os.path.join(base, "home", "demo")
    dirs = ["Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos", "Projects/sonata",
            "Pictures/Vacation 2026", ".config", ".local/share", ".cache"]
    for d in dirs:
        os.makedirs(os.path.join(home, d), exist_ok=True)
    with open(os.path.join(home, ".config", "user-dirs.dirs"), "w") as f:
        for key, d in (("DESKTOP", "Desktop"), ("DOCUMENTS", "Documents"), ("DOWNLOAD", "Downloads"),
                       ("MUSIC", "Music"), ("PICTURES", "Pictures"), ("VIDEOS", "Videos")):
            f.write(f'XDG_{key}_DIR="$HOME/{d}"\n')
    files = {
        "Notes.txt": "Ideas for the weekend:\n- farmers market\n- bike ride by the lake\n",
        "script.py": "#!/usr/bin/env python3\nprint('hello, sonata')\n",
        "index.html": "<!doctype html><title>Demo</title><h1>Hello</h1>\n",
        "Budget 2026.csv": "Month,Income,Expenses\nJan,100,80\n",
        "Documents/Report final v2.pdf": "%PDF-1.4\n%demo\n",
        "Documents/Trip plan.md": "# Trip plan\n\n- Day 1: old town\n- Day 2: the coast\n",
        "Projects/sonata/readme.md": "# Sonata\n",
        "Projects/plan.txt": "1. design\n2. build\n3. test\n",
        "Downloads/archive.tar.gz": "",
        "Desktop/Trip plan.md": "# Trip plan\n\n- Day 1: old town\n",
        "Desktop/Invoice 0142.pdf": "%PDF-1.4\n%demo\n",
    }
    os.makedirs(os.path.join(home, "Desktop", "Design"), exist_ok=True)
    # do-nothing programs for the demo apps' Exec= (GIO hides entries whose program is missing)
    bin_dir = os.path.join(home, ".local", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    for exe in ["sonata2"] + [did.split(".")[-1].lower() for did, *_r in DEMO_APPS]:
        with open(os.path.join(bin_dir, exe), "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(bin_dir, exe), 0o755)
    for rel, text in files.items():
        with open(os.path.join(home, rel), "w") as f:
            f.write(text)
    for i, (name, top, bottom, sun, hills) in enumerate(PICTURES):
        landscape(os.path.join(home, "Pictures", name), (1600, 1067), i + 3, (top, bottom), sun, hills)
    shutil.copy(os.path.join(home, "Pictures", PICTURES[2][0]), os.path.join(home, "Desktop", "Screenshot 2026-09-12.jpg"))
    shutil.copy(os.path.join(home, "Pictures", PICTURES[0][0]), os.path.join(home, "Downloads", "Lake wallpaper.jpg"))
    for i, (name, *_rest) in enumerate(PICTURES[:3]):
        shutil.copy(os.path.join(home, "Pictures", name), os.path.join(home, f"photo-{i + 1:03d}.jpg"))
    return home


# == capture process ================================================================================
def settle(ms: int) -> None:
    from gi.repository import GLib
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def patch_identity() -> None:
    """User, real and host names as the demo account's, whatever the machine."""
    import pwd
    from gi.repository import GLib
    GLib.get_user_name = lambda: "demo"
    GLib.get_real_name = lambda: "Alex Demo"
    GLib.get_host_name = lambda: "sonata-demo"
    real = pwd.getpwuid

    def getpwuid(uid):
        p = real(uid)
        return pwd.struct_passwd(("demo", "x", uid, p.pw_gid, "Alex Demo", os.environ["HOME"], "/bin/zsh"))
    pwd.getpwuid = getpwuid


def visible_texts(widget, out=None) -> list:
    """Every text drawn in a widget tree (mapped widgets; tooltips are not drawn)."""
    from gi.repository import Gtk
    out = [] if out is None else out
    if not widget.get_mapped():
        return out
    for cls, get in ((Gtk.Label, "get_label"), (Gtk.Editable, "get_text"), (Gtk.Button, "get_label")):
        if isinstance(widget, cls):
            t = getattr(widget, get)()
            if t:
                out.append(t)
    if isinstance(widget, Gtk.TextView):
        b = widget.get_buffer()
        out.append(b.get_text(b.get_start_iter(), b.get_end_iter(), True))
    child = widget.get_first_child()
    while child is not None:
        visible_texts(child, out)
        child = child.get_next_sibling()
    return out


def leaks(texts) -> list:
    return sorted({f"{rx.pattern} in {t[:80]!r}" for t in texts for rx in BANNED if rx.search(t)})


def capture(win, path: str) -> None:
    """The window's own rendering (RGBA, translucency kept) as a PNG."""
    from gi.repository import Graphene, Gtk
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    tex = win.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
    tex.save_to_png(path)


def _child_setup(name: str, mode: str):
    """In a child process: fresh app data, the demo identity, Sonata's look
    in `mode`; returns a registered Adw.Application."""
    os.environ["GDK_BACKEND"] = "x11"
    # app data and settings start empty for every shot (the demo files stay)
    home = os.environ["HOME"]
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        fresh = tempfile.mkdtemp(prefix=var.split("_")[1].lower() + "-", dir=os.path.join(home, ".cache"))
        if var == "XDG_CONFIG_HOME":
            shutil.copy(os.path.join(home, ".config", "user-dirs.dirs"), fresh)
        else:
            os.makedirs(os.path.join(fresh, "applications"))  # there before GIO looks (demo apps)
        os.environ[var] = fresh
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    patch_identity()
    from sonata2 import ui
    Adw.init()
    if mode == "dark":
        ui.force_appearance("dark")
    ui.setup()
    try:
        from sonata2 import icons
        icons.setup()
    except Exception:                                   # icons fall back to the system theme
        pass
    Gtk.Settings.get_default().set_property("gtk-font-name", "Inter 10")     # what the session sets
    app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.readmeshots." + name.replace("-", "_"))
    app.register(None)
    return app


def _check(name, widgets) -> None:
    """Fail the shot when something private is on screen."""
    texts = []
    for w in widgets:
        visible_texts(w, texts)
        if hasattr(w, "get_title"):
            texts.append(w.get_title() or "")
    hits = leaks(texts)
    if hits:
        print(f"{name}: possible private data on screen:\n  " + "\n  ".join(hits), file=sys.stderr)
        sys.exit(3)


def run_capture(name: str, raw_path: str) -> None:
    """In the child process: build one window, check it, save it."""
    mode, size = SHOTS[name]
    app = _child_setup(name, mode)
    base = name.split("-")[0]
    win, own_titlebar = globals()["shot_" + base](app, name, size)
    settle(300)
    _check(name, [win])
    capture(win, raw_path)
    with open(raw_path + ".json", "w") as f:
        json.dump({"title": win.get_title() or "", "own_titlebar": own_titlebar, "mode": mode}, f)
    os._exit(0)                                          # don't wait for app shutdown handlers


def _present(win, size) -> None:
    if size:
        win.set_default_size(*size)
    win.present()


# -- one function per app: build the window, return (window, draws its own title bar) --------------
def shot_files(app, name, size):
    from gi.repository import Gio, Gtk
    from sonata2.files.window import FilesWindow
    home = os.environ["HOME"]
    win = FilesWindow(app, Gio.File.new_for_path(home).get_uri())
    win.new_tab(Gio.File.new_for_path(os.path.join(home, "Pictures")).get_uri(), select=False)
    _present(win, size)
    settle(2200)                                          # thumbnails

    def fake_free(w):                                     # the machine's free space -> a demo figure
        if isinstance(w, Gtk.Label) and w.has_css_class("fs-free"):
            w.set_label("412.6 GB free")
        c = w.get_first_child()
        while c is not None:
            fake_free(c)
            c = c.get_next_sibling()
    fake_free(win)
    return win, True


def shot_settings(app, name, size):
    from sonata2.settings.app import Settings
    win = Settings(app, "appearance")
    _present(win, size)
    settle(1500)
    return win, True


def _notes_store():
    import datetime
    import time
    from sonata2.notes import store as S
    st = S.Store()
    now = time.time()
    recipes, work = st.add_folder("Recipes"), st.add_folder("Work")
    trip = st.new_note(body="# Trip to Lisbon\nFlights on **Friday 8:40**, back on Monday.\n## Packing\n"
                            "- [x] Passport\n- [x] Chargers\n- [ ] Sunglasses\n- [ ] Rain jacket\n"
                            "## Places\n1. Alfama viewpoints\n2. Tram 28\n3. *Pastéis* in Belém\n"
                            "4. LX Factory on Sunday", now=now - 90)
    st.new_note(body="# Grocery list\n- [ ] Oat milk\n- [ ] Coffee beans\n- [x] Eggs\n- [ ] Basil", now=now - 3600)
    st.new_note(work["id"], body="# Weekly sync\nRelease notes, dock polish, calendar invites.", now=now - 86400)
    st.new_note(work["id"], body="# Ideas\nA dock that bounces when an app wants attention.", now=now - 9 * 86400)
    st.new_note(recipes["id"], body="# Pão de queijo\nCassava flour, cheese, eggs, milk, oil.", now=now - 4 * 86400)
    st.new_note(recipes["id"], body="# Lemon pasta\nZest, butter, parmesan, black pepper.", now=now - 20 * 86400)
    pinned = st.new_note(body="# Book club\nNext: *The Left Hand of Darkness*.", now=now - 30 * 86400)
    st.set_pinned(pinned, True)
    today = datetime.date.today()
    home_list = st.add_list("Home")
    st.new_reminder(title="Call the dentist", due=S.make_due(today, 15, 30))
    r = st.new_reminder(title="Pay the electricity bill", due=S.make_due(today - datetime.timedelta(days=1)))
    st.update_reminder(r, flagged=True, priority=2, notes="Due yesterday")
    st.new_reminder(title="Send the project update", due=S.make_due(today, 9, 0))
    st.new_reminder(title="Water the plants", due=S.make_due(today))
    st.new_reminder(home_list["id"], title="Change the water filter", due=S.make_due(today, 18, 0))
    st.new_reminder(title="Book museum tickets", due=S.make_due(today + datetime.timedelta(days=2), 10, 0))
    done = st.new_reminder(title="Renew library card")
    st.update_reminder(done, completed=True)
    return st, trip


def shot_notes(app, name, size):
    from sonata2.notes.window import NotesWindow
    _st, trip = _notes_store()
    win = NotesWindow(app)
    _present(win, size)
    win.reload_notes(select_id=trip["id"])
    settle(900)
    return win, False


def shot_reminders(app, name, size):
    from sonata2.notes.window import NotesWindow
    _notes_store()
    win = NotesWindow(app)
    _present(win, size)
    win.select("r:today")
    settle(900)
    return win, False


def shot_calendar(app, name, size):
    import datetime as dt
    from sonata2.calendar import ics
    from sonata2.calendar.window import CalendarWindow
    win = CalendarWindow(app)
    st = win.store
    first = dt.date.today().replace(day=1)

    def ev(title, day, h, m, minutes, cal, **kw):
        s = dt.datetime.combine(first + dt.timedelta(days=day - 1), dt.time(h, m))
        e = ics.Event(uid=ics.new_uid(), summary=title, start=s, end=s + dt.timedelta(minutes=minutes),
                      calendar=cal, **kw)
        st.events[e.uid] = e

    def allday(title, day, ndays, cal):
        s = dt.datetime.combine(first + dt.timedelta(days=day - 1), dt.time(0))
        e = ics.Event(uid=ics.new_uid(), summary=title, start=s, end=s + dt.timedelta(days=ndays), all_day=True,
                      calendar=cal)
        st.events[e.uid] = e
    ev("Team Standup", 1, 9, 0, 15, "work", freq="WEEKLY", count=5)
    ev("Design Review", 3, 10, 0, 90, "work", location="Room 4")
    ev("Lunch with Sam", 4, 12, 30, 60, "home", location="Café Central")
    ev("Yoga", 2, 18, 0, 60, "home", freq="WEEKLY", count=5)
    ev("Sprint Planning", 8, 14, 0, 120, "work")
    ev("Dentist", 10, 11, 0, 45, "home")
    ev("Release 2.1", 15, 16, 0, 30, "work")
    ev("Concert", 17, 20, 0, 150, "home", location="City Hall")
    ev("1:1 with Robin", 22, 10, 30, 30, "work")
    ev("Book club", 24, 19, 0, 90, "home")
    ev("Quarterly review", 27, 9, 30, 60, "work")
    allday("Conference", 12, 3, "work")
    allday("Weekend trip", 19, 2, "home")
    win.data_changed()
    _present(win, size)
    win.set_view("month")
    settle(900)
    return win, False


# -- Task Manager: an invented desktop session instead of this machine's /proc -----------------------
MY_UID = 1000
# (pid, ppid, comm, exe, cmdline, owner, threads, MB, cpu % of one core)
PROCS = [
    (1, 0, "systemd", "systemd", "/sbin/init", "sys", 1, 14, 0.0),
    (2, 0, "kthreadd", "", "[kthreadd]", "sys", 1, 0, 0.0),
    (380, 1, "systemd-journal", "systemd-journald", "/usr/lib/systemd/systemd-journald", "sys", 1, 22, 0.1),
    (402, 1, "systemd-udevd", "systemd-udevd", "/usr/lib/systemd/systemd-udevd", "sys", 1, 9, 0.0),
    (530, 1, "NetworkManager", "NetworkManager", "/usr/bin/NetworkManager --no-daemon", "sys", 4, 19, 0.2),
    (541, 1, "bluetoothd", "bluetoothd", "/usr/lib/bluetooth/bluetoothd", "sys", 1, 6, 0.0),
    (556, 1, "udisksd", "udisksd", "/usr/lib/udisks2/udisksd", "sys", 5, 13, 0.0),
    (563, 1, "upowerd", "upowerd", "/usr/lib/upowerd", "sys", 3, 8, 0.0),
    (571, 1, "polkitd", "polkitd", "/usr/lib/polkit-1/polkitd --no-debug", "sys", 4, 11, 0.0),
    (602, 1, "greetd", "greetd", "/usr/bin/greetd", "sys", 1, 3, 0.0),
    (1180, 1, "systemd", "systemd", "/usr/lib/systemd/systemd --user", "user", 1, 12, 0.0),
    (1190, 1180, "pipewire", "pipewire", "/usr/bin/pipewire", "user", 3, 18, 0.6),
    (1191, 1180, "wireplumber", "wireplumber", "/usr/bin/wireplumber", "user", 5, 26, 0.2),
    (1192, 1180, "pipewire-pulse", "pipewire-pulse", "/usr/bin/pipewire-pulse", "user", 3, 15, 0.3),
    (1204, 1180, "dbus-broker", "dbus-broker", "/usr/bin/dbus-broker --scope user", "user", 1, 5, 0.0),
    (1230, 602, "wayfire", "wayfire", "/usr/bin/wayfire -c ~/.config/wayfire.ini", "user", 12, 164, 3.1),
    (1262, 1230, "sonata-dock", "sonata-dock", "sonata-dock", "user", 6, 88, 0.4),
    (1263, 1230, "sonata-menubar", "sonata-menubar", "sonata-menubar", "user", 6, 74, 0.3),
    (1264, 1230, "sonata-wallpape", "sonata-wallpaper", "sonata-wallpaper", "user", 4, 52, 0.0),
    (1265, 1230, "sonata-notify", "sonata-notify", "sonata-notify", "user", 4, 41, 0.0),
    (1266, 1180, "xdg-desktop-por", "xdg-desktop-portal", "/usr/lib/xdg-desktop-portal", "user", 5, 17, 0.0),
    (2001, 1230, "firefox", "firefox", "/usr/lib/firefox/firefox", "user", 92, 612, 6.4),
    (2040, 2001, "Isolated Web Co", "firefox", "/usr/lib/firefox/firefox -contentproc tab 1", "user", 28, 348, 4.2),
    (2041, 2001, "Isolated Web Co", "firefox", "/usr/lib/firefox/firefox -contentproc tab 2", "user", 26, 212, 1.3),
    (2042, 2001, "Isolated Web Co", "firefox", "/usr/lib/firefox/firefox -contentproc tab 3", "user", 24, 176, 0.4),
    (2050, 2001, "RDD Process", "firefox", "/usr/lib/firefox/firefox -contentproc rdd", "user", 6, 44, 0.8),
    (2051, 2001, "Socket Process", "firefox", "/usr/lib/firefox/firefox -contentproc socket", "user", 5, 28, 0.1),
    (2300, 1230, "python3", "python3", "python3 -m sonata2 files", "user", 7, 118, 0.3),
    (2320, 1230, "python3", "python3", "python3 -m sonata2 videos", "user", 9, 142, 1.9),
    (2340, 1230, "python3", "python3", "python3 -m sonata2 notes", "user", 5, 96, 0.1),
    (2360, 1230, "python3", "python3", "python3 -m sonata2 terminal", "user", 5, 84, 0.2),
    (2361, 2360, "zsh", "zsh", "-zsh", "user", 1, 6, 0.0),
    (2380, 1230, "python3", "python3", "python3 -m sonata2 activity", "user", 6, 104, 1.4),
    (2400, 1230, "Telegram", "telegram-desktop", "/usr/bin/telegram-desktop", "user", 31, 286, 0.7),
    (2500, 1230, "thunderbird", "thunderbird", "/usr/lib/thunderbird/thunderbird", "user", 64, 402, 0.5),
]
APPS = {  # app key -> (icon name, display name)
    "firefox": ("firefox", "Firefox"), "telegram-desktop": ("telegram", "Telegram"),
    "thunderbird": ("thunderbird", "Thunderbird"),
    "io.github.vinioliveiras.sonata2.files": ("system-file-manager", "Files"),
    "io.github.vinioliveiras.sonata2.videos": ("sonata-videos", "Videos"),
    "io.github.vinioliveiras.sonata2.notes": ("sonata-notes", "Notes"),
    "io.github.vinioliveiras.sonata2.terminal": ("utilities-terminal", "Terminal"),
    "io.github.vinioliveiras.sonata2.activity": ("utilities-system-monitor", "Task Manager"),
}


class DemoSampler:
    """Stands in for procfs.Sampler: plausible, invented figures that move a
    little each sample (for the graphs)."""
    boot_time = 0.0
    GIB = 1024 ** 3

    def __init__(self):
        import time
        self.rnd = random.Random(42)
        self.n = 0
        self.util = 14.0
        self.mem = 9.4 * self.GIB
        self.net_rx = 180e3
        self.boot_time = time.time() - 3 * 3600 - 42 * 60

    def user_name(self, uid):
        return "demo" if uid == MY_UID else "system"

    def cpu_info(self):
        return {"model": "AMD Ryzen 7 7840HS w/ Radeon 780M Graphics", "cores": 8, "logical": 16, "sockets": 1,
                "mhz": 3800.0, "base_mhz": 3800.0}

    def sample(self, want=("io",)):
        import time
        from sonata2.activity import procfs
        r, self.n = self.rnd, self.n + 1
        # a slow random walk with a burst halfway (an app starting)
        burst = 26 * math.exp(-((self.n % 62) - 50) ** 2 / 10)
        self.util = min(60, max(6, self.util + r.uniform(-3, 3) * 0.8 + (14 - self.util) * 0.08))
        util = self.util + burst
        scale = util / 16.0
        snap = procfs.Snapshot(interval=2.0, uptime=time.time() - self.boot_time, cpu_mhz=4100 + r.uniform(-300, 500))
        for pid, ppid, comm, exe, cmd, owner, thr, mb, cpu in PROCS:
            p = procfs.Proc(pid=pid, name=exe if len(comm) >= 15 and exe.startswith(comm) else comm, comm=comm,
                            cmdline=cmd, exe=exe, uid=MY_UID if owner == "user" else 0,
                            user="demo" if owner == "user" else "system", ppid=ppid, threads=thr,
                            rss=int(mb * 1024 * 1024 * r.uniform(.98, 1.02)), ticks=pid * 37,
                            cpu=max(0.0, cpu * scale * r.uniform(.5, 1.5)), cpu_time=pid * .37,
                            started=self.boot_time + pid, start_ticks=pid * 10, state="S" if exe else "I",
                            read_bytes=pid * 4096, write_bytes=pid * 1024)
            if cpu > 1:                                   # a little disk traffic from the busy ones
                p.read_ps = cpu * r.uniform(15e3, 60e3)
                p.write_ps = cpu * r.uniform(0, 20e3)
            snap.procs[pid] = p
            snap.threads += thr * 3
        snap.cpu = {"user": util * .7, "system": util * .3, "idle": 100 - util}
        self.mem = min(14 * self.GIB, max(8.6 * self.GIB, self.mem + r.uniform(-60, 80) * 1024 ** 2 + burst * 8e6))
        total = 31.2 * self.GIB
        snap.memory = {"total": total, "used": self.mem, "cached": 7.8 * self.GIB, "swap": 0.0,
                       "swap_total": 16 * self.GIB, "app": self.mem * .7, "wired": .9 * self.GIB,
                       "compressed": 0.0, "available": total - self.mem, "pressure": self.mem / total,
                       "free": total - self.mem - 7.8 * self.GIB, "modified": 42 * 1024 ** 2,
                       "committed": self.mem * 1.6, "commit_limit": 31.6 * self.GIB,
                       "paged_pool": 640 * 1024 ** 2, "non_paged_pool": 210 * 1024 ** 2}
        act = max(0.0, r.gauss(2, 2) + burst * .6)
        disk = {"reads": 184233 + self.n * 12, "writes": 96120 + self.n * 20, "read_bytes": 9.1e9,
                "write_bytes": 4.2e9, "io_ms": 0, "reads_ps": 6.0, "writes_ps": 9.0,
                "read_bytes_ps": act * 2.4e5, "write_bytes_ps": r.uniform(0, 3e5), "io_ms_ps": act * 10,
                "active": act, "capacity": 1000204886016, "model": "Samsung SSD 990 PRO 1TB", "kind": "SSD"}
        snap.disks = {"nvme0n1": disk}
        snap.disk = {k: disk[k] for k in ("reads", "writes", "read_bytes", "write_bytes", "reads_ps", "writes_ps",
                                          "read_bytes_ps", "write_bytes_ps")}
        self.net_rx = max(20e3, self.net_rx * r.uniform(.7, 1.35) + burst * 4e4)
        wl = {"rx_bytes": 2.3e9 + self.n * 1e6, "tx_bytes": 3.1e8 + self.n * 2e5, "rx_packets": 1840221,
              "tx_packets": 902113, "rx_bytes_ps": self.net_rx, "tx_bytes_ps": self.net_rx * r.uniform(.08, .2),
              "rx_packets_ps": 120.0, "tx_packets_ps": 60.0}
        lo = dict.fromkeys(wl, 0.0)
        snap.interfaces = {"lo": lo, "wlan0": wl}
        snap.net = {k: wl[k] for k in wl}
        snap.gpus = {"card1": max(0.0, min(100.0, r.gauss(8, 3) + burst * .5))}
        return snap


def shot_taskmanager(app, name, size):
    from gi.repository import Gio
    from sonata2.activity import manage
    from sonata2.activity import window as W
    idx = {k: (Gio.ThemedIcon.new(icon), label) for k, (icon, label) in APPS.items()}
    W.icon_index = lambda: idx
    group = manage.group_processes
    manage.group_processes = lambda procs, app_of, _uid: group(procs, app_of, MY_UID)
    win = W.TaskManagerWindow(app, sampler=DemoSampler())
    win.icons = idx
    for _ in range(62):                                   # two minutes of history for the graphs
        win.apply_snapshot(win.sampler.sample())
    _present(win, size)
    win.show_page("performance" if name.endswith("performance") else "processes")
    settle(1400)
    return win, False


def shot_diskmanager(app, name, size):
    from sonata2.diskutil.window import DiskUtilityWindow
    from tests.test_diskutil import GB, FakeClient, fake_objects

    def clean(v):                                        # the test tree's user paths -> the demo user's
        if isinstance(v, dict):
            return {clean(k): clean(x) for k, x in v.items()}
        if isinstance(v, list) and v and all(isinstance(i, int) for i in v):
            return list(clean(bytes(v[:-1]).decode()).encode()) + [0]
        if isinstance(v, list):
            return [clean(x) for x in v]
        return re.sub(r"\bvini\b", "demo", v) if isinstance(v, str) else v
    win = DiskUtilityWindow(app, client=FakeClient(clean(fake_objects())))
    win.usage = {"/run/media/demo/BACKUP": (31 * GB, 17 * GB), "/": (84 * GB, 116 * GB), "/home": (240 * GB, 360 * GB)}
    _present(win, size)
    settle(500)
    i = 0
    while (row := win.list.get_row_at_index(i)) is not None:
        item = getattr(row, "item", None)
        if item is not None and "980" in (item.name or ""):
            win.list.select_row(row)
            break
        i += 1
    settle(700)
    return win, False


def shot_textedit(app, name, size):
    from gi.repository import Gio
    from sonata2.textedit import window as tw
    docs = os.path.join(os.environ["HOME"], "Documents")
    py = os.path.join(docs, "server.py")
    with open(py, "w") as f:
        f.write('#!/usr/bin/env python3\n"""A tiny HTTP server that says hello."""\nimport http.server\n\n\n'
                'class Handler(http.server.BaseHTTPRequestHandler):\n    def do_GET(self):  # answer every request\n'
                '        body = b"Hello, Sonata!"\n        self.send_response(200)\n'
                '        self.send_header("Content-Type", "text/plain")\n        self.end_headers()\n'
                '        self.wfile.write(body)\n\n\nif __name__ == "__main__":\n    port = 8080\n'
                '    print(f"Serving on port {port}")\n'
                '    http.server.HTTPServer(("", port), Handler).serve_forever()\n')
    with open(os.path.join(docs, "Shopping.md"), "w") as f:
        f.write("# Shopping\n\n- Coffee beans\n- Oat milk\n- Basil\n\nRemember the **Friday** dinner.\n")
    with open(os.path.join(docs, "Letter.txt"), "w") as f:
        f.write("Dear Sam,\n\nThe café opens at nine. We will meet there and walk to the old town together; "
                "bring a coat, it gets cold by the river in the evening.\n\nSee you soon,\nAlex\n")
    win = tw.TextEditWindow(app, py)
    win.open_file(Gio.File.new_for_path(os.path.join(docs, "Shopping.md")))
    win.open_file(Gio.File.new_for_path(os.path.join(docs, "Letter.txt")))
    win.select(win.docs[0])
    _present(win, size)
    settle(1200)
    return win, False


def shot_preview(app, name, size):
    from sonata2.preview.window import PreviewWindow
    pics = os.path.join(os.environ["HOME"], "Pictures")
    paths = [os.path.join(pics, p[0]) for p in PICTURES]
    win = PreviewWindow(app, paths[1], paths=paths)
    _present(win, size)
    settle(1500)
    return win, False


def shot_calculator(app, name, size):
    from sonata2.calculator.window import CalculatorWindow
    win = CalculatorWindow(app)
    _present(win, None)
    settle(500)
    for k in ("1", "2", "8", "×", "2", "4", "="):
        win.press(k)
    settle(300)
    return win, False


def shot_terminal(app, name, size):
    import gi
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte  # noqa: F401  (raises without VTE: the shot is skipped)
    from sonata2.terminal.window import TerminalWindow
    win = TerminalWindow(app, os.environ["HOME"])
    _present(win, size)
    settle(1500)
    return win, True


# == full-screen shots: the desktop, the menu bar, the Dock, Launchpad ===========================
SCREEN = (1920, 1080)
BAR_H = 24                                   # shell/topbar.py
DOCK_ROOM = 76                               # the Dock's strip at the bottom (a zoomed window stops above it)
APP_MAX = (SCREEN[0] - 24, SCREEN[1] - BAR_H - DOCK_ROOM)
SHOTS.update({"files-max": ("light", APP_MAX), "settings-max": ("light", APP_MAX)})
INTERNAL = {"files-max", "settings-max"}     # parts of the full-screen shots, not saved on their own
SONATA = "io.github.vinioliveiras.sonata2."
# name -> (appearance, base picture, parts); base "wall" = the wallpaper, else another raw capture
SCENES = {
    "desktop": ("light", "wall", {"icons", "bar", "dock"}),
    "desktop-dark": ("dark", "wall", {"icons", "bar", "dock"}),
    "files-full": ("light", "files-max", {"bar", "dock"}),
    "settings-full": ("light", "settings-max", {"bar", "dock"}),
    "launchpad-grid": ("light", "desktop", {"launchpad"}),
    "launchpad": ("light", "launchpad-grid", {"dock"}),
    "controlcenter": ("light", "wall", {"icons", "bar", "dock", "cc"}),
}
SCENE_ACTIVE = {"files-full": SONATA + "files", "settings-full": "sonata2-settings"}
DOCK_PINS = [SONATA + "files", "sonata2-launchpad", "firefox", "thunderbird", SONATA + "calendar", SONATA + "notes",
             SONATA + "videos", SONATA + "preview", SONATA + "textedit", SONATA + "terminal", SONATA + "activity",
             "sonata2-settings"]
RUNNING = [SONATA + "files", SONATA + "files", SONATA + "files", "firefox", SONATA + "videos", SONATA + "videos",
           SONATA + "notes"]                    # windows: 3 Files, 2 Music, 1 each (the dots)
# other apps in the demo's Launchpad: (desktop id, name, icon, categories)
DEMO_APPS = [
    ("firefox", "Firefox", "firefox", "Network;WebBrowser;"),
    ("thunderbird", "Thunderbird", "thunderbird", "Network;Email;"),
    ("org.telegram.desktop", "Telegram", "telegram", "Network;Chat;"),
    ("discord", "Discord", "discord", "Network;Chat;"),
    ("spotify", "Spotify", "spotify", "AudioVideo;Audio;"),
    ("steam", "Steam", "steam", "Game;"),
    ("libreoffice-writer", "LibreOffice Writer", "libreoffice-writer", "Office;WordProcessor;"),
    ("libreoffice-calc", "LibreOffice Calc", "libreoffice-calc", "Office;Spreadsheet;"),
    ("gimp", "GIMP", "gimp", "Graphics;"),
    ("org.inkscape.Inkscape", "Inkscape", "inkscape", "Graphics;"),
    ("org.kde.krita", "Krita", "krita", "Graphics;"),
    ("blender", "Blender", "blender", "Graphics;3DGraphics;"),
    ("vlc", "VLC", "vlc", "AudioVideo;Video;"),
    ("com.obsproject.Studio", "OBS Studio", "com.obsproject.Studio", "AudioVideo;Recorder;"),
    ("org.gnome.Maps", "Maps", "org.gnome.Maps", "Utility;Maps;"),
    ("org.gnome.Weather", "Weather", "org.gnome.Weather", "Utility;"),
    ("chromium", "Chromium", "chromium", "Network;WebBrowser;"),
]


def install_demo_apps() -> None:
    """Desktop entries for Sonata's apps and a few well-known ones, in the
    shot's own data folder; app lists only look there (apps.app_dirs)."""
    import importlib
    from gi.repository import GLib
    from sonata2 import apps
    for mod, fn in (("shell.launchpad", "launchpad_desktop_file"), ("settings.app", "settings_desktop_file"),
                    ("files", "files_desktop_file"), ("calculator.window", "calculator_desktop_file"),
                    ("textedit.window", "textedit_desktop_file"), ("preview.window", "preview_desktop_file"),
                    ("terminal.window", "terminal_desktop_file"), ("notes.window", "notes_desktop_file"),
                    ("activity.window", "activity_desktop_file"), ("videos.window", "videos_desktop_file"),
                    ("diskutil.window", "diskutil_desktop_file"),
                    ("calendar.window", "calendar_desktop_file")):
        getattr(importlib.import_module("sonata2." + mod), fn)("sonata2")
    for did, name, icon, cats in DEMO_APPS:
        apps.write_desktop_file(did + ".desktop", f"[Desktop Entry]\nType=Application\nName={name}\n"
                                f"Exec={did.split('.')[-1].lower()}\nIcon={icon}\nCategories={cats}\n")
    folder = os.path.join(GLib.get_user_data_dir(), "applications")
    apps.app_dirs = lambda: [folder]
    apps.scan()
    apps.refresh()
    lookup = apps.lookup                                 # GIO notices new entries late: the scan answers first
    apps.lookup = lambda did: apps._scan.get(did if did.endswith(".desktop") else did + ".desktop") or lookup(did)


class FakeToplevel:
    def __init__(self, app_id, activated=False):
        self.app_id, self.activated, self.title = app_id, activated, app_id
        self.minimized = self.maximized = self.fullscreen = False


class FakeManager:
    """Stands in for wl.toplevels.ToplevelManager: the demo's open windows."""
    available = True

    def __init__(self, running, active=None):
        self.toplevels = [FakeToplevel(k, k == active) for k in running + ([active] if active and active not in running
                                                                           else [])]
        self.listeners = []

    def __getattr__(self, _name):                       # activate, minimize, set_rectangle...: nothing to do
        return lambda *a, **k: None


def patch_system() -> None:
    """Menu bar and Control Center read the machine (NetworkManager, BlueZ,
    PipeWire, the battery): demo values instead."""
    from sonata2.backend import system
    for name, value in (("wifi_available", True), ("wifi_enabled", True), ("wifi_current", ("Home", 82, False)),
                        ("battery", (78, "Discharging")), ("on_ac", False), ("power_profile_fast", None),
                        ("volume", (64, False)), ("input_volume", (45, False)), ("brightness", 72),
                        ("bluetooth_state", True), ("bluetooth_devices", [])):
        setattr(system, name, lambda *_a, v=value: v)
    from sonata2.shell import mpris
    art = os.path.join(os.environ["HOME"], ".cache", "np-cover.jpg")
    cover(art, 3, *ALBUMS[3][4], ALBUMS[3][5])
    meta = {"xesam:title": "Aurora", "xesam:artist": ["The Tidewater"], "mpris:artUrl": "file://" + art}
    mpris.Players.active = property(lambda self: True)
    mpris.Players.playing = property(lambda self: True)
    mpris.Players._prop = lambda self, name: {"Metadata": meta, "PlaybackStatus": "Playing"}.get(name)
    mpris.Players.app = property(lambda self: "Music")
    mpris.Players.call = lambda self, *a: None


def _texture(path):
    from gi.repository import Gdk
    return Gdk.Texture.new_from_filename(path)


def run_scene(name: str, raw_path: str, base: str, frosted: str) -> None:
    """In the child process: a 1920x1080 screen -- `base` (wallpaper, or a
    window already composited on it), desktop icons, the real menu bar and
    Dock (their glass shows `frosted`, the blurred base, as the compositor's
    blur would), Launchpad or Control Center."""
    mode, _base, parts = SCENES[name]
    app = _child_setup(name, mode)
    from gi.repository import Gtk
    install_demo_apps()
    patch_system()
    from sonata2 import ui
    from sonata2.shell import dock as D
    from sonata2.shell import topbar
    mgr = FakeManager(RUNNING, SCENE_ACTIVE.get(name))
    frost = _texture(frosted)
    W, H = SCREEN
    if "launchpad" in parts:
        from sonata2.shell import launchpad
        win = launchpad.Launchpad(app)
        win.set_default_size(W, H)
        win._dock_above = lambda *_a: None
        win.bin.backdrop = frost
        win.present()
        win.open_launchpad()
        settle(1500)
        win.bin.progress = 1.0
        win.bin.thaw()
        settle(400)
        _check(name, [win])
        capture(win, raw_path)
        with open(raw_path + ".json", "w") as f:
            json.dump({}, f)
        os._exit(0)
    win = Gtk.Window(decorated=False, default_width=W, default_height=H)
    over = Gtk.Overlay()
    pic = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=True, hexpand=True, vexpand=True)
    pic.set_filename(base)
    over.set_child(pic)
    desk = bar = None
    if "icons" in parts:
        from sonata2.shell.desktop import Desktop
        desk = Desktop()
        over.add_overlay(desk)
    if "bar" in parts:
        bar = topbar.Bar(mgr)
        bar.set_valign(Gtk.Align.START)
        bar.backdrop = frost
        bar._bt_powered = lambda: True
        over.add_overlay(bar)
    if "dock" in parts:
        cfg = D.load_config()
        cfg.update(pinned=list(DOCK_PINS), recent=[], show_recents=False, magnification=False)
        D.load_css(cfg)
        dock = D.Dock(cfg, mgr)
        D.apply_margins(dock)
        dock.backdrop = frost
        over.add_overlay(dock)
    win.set_child(over)
    win.present()
    settle(600)
    if desk is not None:
        desk.resized(W, H)
    if "dock" in parts:
        dock._sync()
    if bar is not None:
        bar._wifi_state((True, True, ("Home", 82, False)))
        bar._battery_state((78, "Discharging", False, None))
        bar._sound_state((64, False))
        bar._bt_update()
        bar._active_changed()
        import datetime
        day = datetime.date.today()
        bar._set_text(bar.clock, day.strftime("%a ") + str(day.day) + day.strftime(" %b  09:41"))
    settle(2500)                                         # thumbnails, icons, tray
    pops = []
    if "cc" in parts:
        bar.cc.emit("clicked")
        settle(1500)
        pops = [p for p in ui.menu.OPEN if p.get_mapped()]
    _check(name, [win] + pops)
    capture(win, raw_path)
    meta = {}
    if pops:
        pop = pops[0]
        path = raw_path + ".pop.png"
        from gi.repository import Graphene
        w, h = pop.get_width(), pop.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(pop).snapshot(snap, w, h)
        pop.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h)).save_to_png(path)
        surf = pop.get_surface()
        contents = pop.get_child().get_parent()
        ok, b = contents.compute_bounds(pop)
        ok2, ab = bar.cc.compute_bounds(win)
        meta["pop"] = {"path": path, "x": surf.get_position_x(), "y": surf.get_position_y(),
                       "contents": [b.origin.x, b.origin.y, b.size.width, b.size.height],
                       "anchor": [ab.origin.x, ab.origin.y, ab.size.width, ab.size.height]}
    with open(raw_path + ".json", "w") as f:
        json.dump(meta, f)
    os._exit(0)


def frost(img):
    """The compositor's glass blur (config/wayfire.ini [blur], shell/preview.py): blur + saturation."""
    from PIL import ImageEnhance, ImageFilter
    return ImageEnhance.Color(img.convert("RGB").filter(ImageFilter.GaussianBlur(18))).enhance(1.6)


def scene_base(name: str, raws: dict, work: str):
    """(base, frosted) PNG paths for a scene."""
    from PIL import Image
    src = SCENES[name][1]
    wall_img = wall(SCREEN, SCENES[name][0] == "dark")
    if src == "wall":
        img = wall_img
    elif src in INTERNAL:                                # a zoomed window between menu bar and Dock
        with open(raws[src] + ".json") as f:
            meta = json.load(f)
        img = wall_img.copy()
        layer = window_layer(Image.open(raws[src]).convert("RGBA"), meta)
        place(img, wall_img, layer, ((SCREEN[0] - layer.width) // 2, BAR_H + 6), False)
    else:
        img = Image.open(raws[src]).convert("RGBA")
    base = os.path.join(work, name + "-base.png")
    frosted = os.path.join(work, name + "-frost.png")
    img.convert("RGB").save(base)
    frost(img).save(frosted)
    return base, frosted


def finish_scene(raw: str, out: str) -> int:
    """The scene capture, plus an open panel over the same frosted glass."""
    from PIL import Image, ImageDraw
    img = Image.open(raw).convert("RGBA")
    with open(raw + ".json") as f:
        meta = json.load(f)
    pop = meta.get("pop")
    if pop:
        layer = Image.open(pop["path"]).convert("RGBA")
        cx, cy, cw, ch = (round(v) for v in pop["contents"])
        ax, ay, aw, ah = pop["anchor"]
        # where the popover sits: from its surface, else right-aligned under its button
        x, y = pop["x"], pop["y"]
        if not (0 <= x <= SCREEN[0] - cw and BAR_H - cy <= y <= BAR_H + 20):
            x = round(min(SCREEN[0] - 8 - cw, ax + aw / 2 - cw / 2) - cx)
            y = round(ay + ah + 2 - cy)
        mask = Image.new("L", layer.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([cx, cy, cx + cw - 1, cy + ch - 1], 12, fill=255)
        region = img.crop((x, y, x + layer.width, y + layer.height))
        glass = frost(region).convert("RGBA")
        region.paste(glass, (0, 0), mask)
        region.alpha_composite(layer)
        img.paste(region, (x, y))
    return save_png(img, out, 700 * 1024)


# == framing ==========================================================================================
def _rgba(css: str):
    m = re.match(r"rgba?\(([^)]*)\)", css.strip())
    if m:
        v = [float(x) for x in m.group(1).split(",")]
        return tuple(int(x) for x in v[:3]) + (round(255 * (v[3] if len(v) > 3 else 1)),)
    h = css.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def _tokens():
    """sonata2/ui/tokens.py on its own (the ui package would load GTK here)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sonata_tokens", os.path.join(ROOT, "sonata2", "ui", "tokens.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _font(size: int):
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(os.path.join(ROOT, "sonata2", "data", "fonts", "InterVariable.ttf"), size)
        try:
            f.set_variation_by_name("SemiBold")
        except Exception:
            pass
        return f
    except OSError:
        return ImageFont.load_default()


def window_layer(raw, meta):
    """The captured window plus the compositor's title bar (when Sonata's
    compositor draws it), still see-through where the window is glass."""
    from PIL import Image, ImageDraw
    pal = _tokens().palette(meta["mode"] == "dark")
    box = raw.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    raw = raw.crop(box) if box else raw                  # drop the transparent resize border (CSD)
    if meta["own_titlebar"]:
        return raw
    w, h = raw.size
    win = Image.new("RGBA", (w, h + TITLEBAR_H), (0, 0, 0, 0))
    win.paste(Image.new("RGBA", (w, TITLEBAR_H), _rgba(pal["titlebar_bg"])), (0, 0))
    decor = os.path.join(ROOT, "sonata2", "data", "decor")
    for i, n in enumerate(("close", "minimize", "maximize")):
        dot = Image.open(os.path.join(decor, n + ".png")).convert("RGBA")
        win.alpha_composite(dot, (7 + 20 * i, (TITLEBAR_H - dot.height) // 2))
    d = ImageDraw.Draw(win)
    f = _font(13)
    title = meta["title"]
    tw = d.textlength(title, font=f)
    d.text(((w - tw) / 2, TITLEBAR_H / 2), title, font=f, fill=_rgba(pal["titlebar_text"]), anchor="lm")
    win.alpha_composite(raw, (0, TITLEBAR_H))
    return win


def _round_mask(size, radius, scale=4):
    from PIL import Image, ImageDraw
    w, h = size
    m = Image.new("L", (w * scale, h * scale), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w * scale - 1, h * scale - 1], radius * scale, fill=255)
    return m.resize(size, Image.LANCZOS)


def place(canvas, wall, layer, pos, dark: bool) -> None:
    """Draw a window layer on the canvas at pos: shadow, the bare wallpaper
    `wall` through its glass (never the windows below, like the
    compositor's blur), rounded corners, a hairline edge."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    x, y = pos
    w, h = layer.size
    mask = _round_mask((w, h), RADIUS)
    # soft shadow (the window-frame CSS: 0 22px 56px rgba(0,0,0,.30), scaled to the margin)
    pad = 60
    sh = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    sh.paste(mask, (pad, pad))
    sh = sh.filter(ImageFilter.GaussianBlur(20)).point(lambda v: int(v * .42))
    black = Image.new("RGBA", sh.size, (0, 0, 0, 255))
    black.putalpha(sh)
    shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_layer.paste(black, (x - pad, y - pad + 14))
    behind = wall.crop((x, y, x + w, y + h)).convert("RGBA")
    canvas.alpha_composite(shadow_layer)
    behind.alpha_composite(layer)
    edge = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle([0, 0, w - 1, h - 1], RADIUS, outline=(0, 0, 0, 90 if dark else 60))
    if dark:
        ImageDraw.Draw(edge).rounded_rectangle([1, 1, w - 2, h - 2], RADIUS - 1, outline=(255, 255, 255, 38))
    behind.alpha_composite(edge)
    canvas.paste(behind, (x, y), ImageChops.multiply(mask, behind.getchannel("A")))


def save_png(im, path: str, limit: int = MAX_BYTES) -> int:
    """Full colour, by the file's extension: WebP (quality 90) for the
    published shots, lossless PNG for the work files. (The PNGs used to be cut
    to 256 colours when big: with photo wallpapers the colours looked off --
    Vini.)"""
    im = im.convert("RGB")
    if path.endswith(".webp"):
        im.save(path, "WEBP", quality=90, method=6)
    elif path.endswith(".jpg"):
        im.save(path, "JPEG", quality=88, optimize=True, progressive=True)
    else:
        im.save(path, optimize=True)
    return os.path.getsize(path)


def frame(raw_path: str, out_path: str) -> int:
    from PIL import Image
    with open(raw_path + ".json") as f:
        meta = json.load(f)
    layer = window_layer(Image.open(raw_path).convert("RGBA"), meta)
    w, h = layer.size
    bg = wall((w + 2 * MARGIN, h + 2 * MARGIN), meta["mode"] == "dark")
    canvas = bg.copy()
    place(canvas, bg, layer, (MARGIN, MARGIN), meta["mode"] == "dark")
    return save_png(canvas, out_path)


def hero(raws: dict, out_path: str) -> int:
    """Several windows overlapping on the desktop, like a real session."""
    from PIL import Image
    size = (1400, 860)
    bg = wall(size)
    canvas = bg.copy()
    plan = (("taskmanager", .62, (40, 40)), ("calendar", .60, (690, 56)), ("notes", .60, (716, 400)),
            ("files", .74, (170, 370)))
    for name, s, pos in plan:
        if name not in raws:
            continue
        with open(raws[name] + ".json") as f:
            meta = json.load(f)
        layer = window_layer(Image.open(raws[name]).convert("RGBA"), meta)
        layer = layer.resize((round(layer.width * s), round(layer.height * s)), Image.LANCZOS)
        place(canvas, bg, layer, pos, meta["mode"] == "dark")
    return save_png(canvas, out_path)


# == driver ============================================================================================
def main(argv) -> int:
    if "--capture" in argv:                               # internal: one shot in this process
        i = argv.index("--capture")
        run_capture(argv[i + 1], argv[i + 2])
        return 0
    if "--scene" in argv:
        i = argv.index("--scene")
        run_scene(*argv[i + 1:i + 5])
        return 0
    # (hero: the older composite of four windows, made only when asked for)
    public = [n for n in list(SHOTS) + list(SCENES) if n not in INTERNAL and n != "launchpad-grid"]
    if "--list" in argv:
        print("\n".join(public))
        return 0
    if not os.environ.get("DISPLAY"):
        print('Run under Xvfb: xvfb-run -a -s "-screen 0 1920x1200x24" python3 tools/readme-shots.py',
              file=sys.stderr)
        return 2
    wanted = [a for a in argv if not a.startswith("-")] or public
    base = tempfile.mkdtemp(prefix="sonata-shots-")
    raw_dir = os.path.join(base, "raw")
    os.makedirs(raw_dir)
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        home = make_home(base)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DBUS_", "WAYLAND_", "SONATA"))}
        runtime = os.path.join(base, "run")
        os.makedirs(runtime, mode=0o700)
        env.update(HOME=home, USER="demo", LOGNAME="demo", HOSTNAME="sonata-demo", TMPDIR=os.path.join(home, ".cache"),
                   XDG_CONFIG_HOME=os.path.join(home, ".config"), XDG_DATA_HOME=os.path.join(home, ".local", "share"),
                   XDG_CACHE_HOME=os.path.join(home, ".cache"), XDG_STATE_HOME=os.path.join(home, ".local", "state"),
                   XDG_RUNTIME_DIR=runtime, GDK_BACKEND="x11", NO_AT_BRIDGE="1", GTK_A11Y="none", GSETTINGS_BACKEND="memory",
                   PYTHONPATH=ROOT + os.pathsep + env.get("PYTHONPATH", ""),
                   PATH=os.path.join(home, ".local", "bin") + os.pathsep + env.get("PATH", ""))
        bus = ["dbus-run-session", "--"] if shutil.which("dbus-run-session") else []
        need = set(wanted)
        if "hero" in need:
            need |= {"taskmanager", "calendar", "notes", "files"}
        for name in reversed(list(SCENES)):              # what the full-screen shots are made from
            if name in need:
                need.add(SCENES[name][1])
        raws, report = {}, []
        for name in SHOTS:
            if name not in need:
                continue
            raw = os.path.join(raw_dir, name + ".png")
            r = subprocess.run(bus + [sys.executable, os.path.abspath(__file__), "--capture", name, raw],
                               env=env, cwd=home, capture_output=True, text=True, timeout=120)
            if not os.path.exists(raw + ".json"):
                why = (r.stderr.strip().splitlines() or ["no output"])[-1]
                report.append(f"{name}: skipped ({why})")
                continue
            raws[name] = raw
            if name in wanted and name not in INTERNAL:
                out = os.path.join(OUT_DIR, name + ".webp")
                report.append(f"{name}: {frame(raw, out) // 1024} KB")
        for name in SCENES:
            if name not in need:
                continue
            if SCENES[name][1] != "wall" and SCENES[name][1] not in raws:
                report.append(f"{name}: skipped (no {SCENES[name][1]})")
                continue
            raw = os.path.join(raw_dir, name + ".png")
            base_png, frosted = scene_base(name, raws, raw_dir)
            r = subprocess.run(bus + [sys.executable, os.path.abspath(__file__), "--scene", name, raw, base_png,
                                      frosted], env=env, cwd=home, capture_output=True, text=True, timeout=180)
            if not os.path.exists(raw + ".json"):
                why = (r.stderr.strip().splitlines() or ["no output"])[-1]
                report.append(f"{name}: skipped ({why})")
                continue
            final = os.path.join(raw_dir, name + "-final.png")
            size = finish_scene(raw, final)
            raws[name] = final
            if name in wanted:
                from PIL import Image
                size = save_png(Image.open(final), os.path.join(OUT_DIR, name + ".webp"))
                if name == "desktop":                    # link previews (og:image) want a JPEG
                    save_png(Image.open(final), os.path.join(OUT_DIR, name + ".jpg"))
                report.append(f"{name}: {size // 1024} KB")
        if "hero" in wanted:
            report.append(f"hero: {hero(raws, os.path.join(OUT_DIR, 'hero.webp')) // 1024} KB")
        print("\n".join(report))
    finally:
        shutil.rmtree(base, ignore_errors=True) if not os.environ.get("KEEP_RAW") else print("raw:", base)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
