"""Launchpad layout: pages of apps and folders (no GTK here; unit-tested).

Stored in ~/.config/sonata2/launchpad.json as
  {"pages": [["firefox", {"folder": "Games", "apps": ["steam", ...]}, ...], ...]}
Items are desktop ids (without .desktop) or folder dicts. Pages hold up to
COLS x ROWS items; overflow cascades to the next page (macOS behaviour when
an item is dropped on a full page)."""
COLS, ROWS = 7, 5
PER_PAGE = COLS * ROWS


def set_grid(cols: int, rows: int) -> bool:
    """Page size for this display (responsive: 7 columns like macOS, fewer on
    narrow or portrait screens; as many rows as fit, 3 to 8). True if it changed."""
    global COLS, ROWS, PER_PAGE
    cols, rows = max(3, min(8, int(cols))), max(3, min(8, int(rows)))
    if (cols, rows) == (COLS, ROWS):
        return False
    COLS, ROWS, PER_PAGE = cols, rows, cols * rows
    return True

# Folder names from freedesktop main categories (macOS names its new folders
# after the apps' App Store category).
CATEGORY_NAMES = (
    ("Game", "Games"), ("Development", "Developer Tools"), ("Office", "Productivity"),
    ("Graphics", "Creativity"), ("AudioVideo", "Entertainment"), ("Audio", "Music"),
    ("Video", "Video"), ("Network", "Internet"), ("Education", "Education"),
    ("Science", "Science"), ("Settings", "Settings"), ("System", "System"),
    ("Utility", "Utilities"),
)


def is_folder(item) -> bool:
    return isinstance(item, dict)


def folder_name(categories_a, categories_b) -> str:
    """Name for a new folder made of two apps (shared category, else first's)."""
    for cats in ((set(categories_a) & set(categories_b)), set(categories_a), set(categories_b)):
        for key, name in CATEGORY_NAMES:
            if key in cats:
                return name
    return "Untitled Folder"


class Model:
    def __init__(self, data: dict, installed: dict):
        """installed: desktop id -> display name (apps that should be shown)."""
        self.installed = installed
        self.pages = [list(p) for p in (data or {}).get("pages", [])]
        self.hidden = list((data or {}).get("hidden", []))
        self.reconcile()

    # -- persistence -------------------------------------------------------------
    def to_json(self) -> dict:
        return {"pages": [list(p) for p in self.pages], "hidden": self.hidden}

    def reconcile(self) -> None:
        """Drop uninstalled apps, dissolve 1-app folders, append new apps
        (alphabetically) at the end, fix overflow."""
        seen = set()
        pages = []
        for page in self.pages:
            out = []
            for item in page:
                if is_folder(item):
                    apps = [a for a in item.get("apps", []) if a in self.installed and a not in seen]
                    seen.update(apps)
                    if len(apps) > 1:
                        out.append({"folder": item.get("folder") or "Untitled Folder", "apps": apps})
                    elif apps:
                        out.append(apps[0])
                elif item in self.installed and item not in seen:
                    seen.add(item)
                    out.append(item)
            pages.append(out)
        new = sorted((a for a in self.installed if a not in seen and a not in self.hidden),
                     key=lambda a: self.installed[a].lower())
        if not pages:
            pages = [[]]
        pages[-1].extend(new)
        self.pages = pages
        self.normalize()

    def repack(self) -> None:
        """Pages refilled in order to the current size (after the page grew):
        no empty rows left at the bottom of a page."""
        items = [it for page in self.pages for it in page]
        self.pages = [items[i:i + PER_PAGE] for i in range(0, len(items), PER_PAGE)] or [[]]

    def normalize(self) -> None:
        """Cascade overflow to the next pages; drop empty pages (keep one)."""
        i = 0
        while i < len(self.pages):
            page = self.pages[i]
            if len(page) > PER_PAGE:
                extra = page[PER_PAGE:]
                del page[PER_PAGE:]
                if i + 1 == len(self.pages):
                    self.pages.append([])
                self.pages[i + 1][:0] = extra
            i += 1
        self.pages = [p for p in self.pages if p] or [[]]

    # -- lookups -----------------------------------------------------------------
    def find(self, app_id: str):
        """(page, index, folder or None) of an app."""
        for p, page in enumerate(self.pages):
            for i, item in enumerate(page):
                if item == app_id:
                    return p, i, None
                if is_folder(item) and app_id in item["apps"]:
                    return p, i, item
        return None

    def all_apps(self) -> list:
        out = []
        for page in self.pages:
            for item in page:
                out.extend(item["apps"] if is_folder(item) else [item])
        return out

    # -- edits -------------------------------------------------------------------
    def move(self, item, page: int, index: int) -> None:
        """Move a top-level item (id or folder dict) to page/index."""
        self._remove_top(item)
        while page >= len(self.pages):
            self.pages.append([])
        self.pages[page].insert(min(index, len(self.pages[page])), item)
        self.normalize()

    def _remove_top(self, item) -> None:
        for page in self.pages:
            for i, it in enumerate(page):
                if it is item or (not is_folder(item) and it == item):
                    del page[i]
                    return

    def make_folder(self, target, dragged: str, name: str) -> dict:
        """Drop app `dragged` on `target` (an app id or a folder)."""
        if is_folder(target):
            if dragged not in target["apps"]:
                self._remove_app_anywhere(dragged)
                target["apps"].append(dragged)
            self.normalize()
            return target
        self._remove_app_anywhere(dragged)
        for page in self.pages:
            for i, it in enumerate(page):
                if it == target:
                    folder = {"folder": name, "apps": [target, dragged]}
                    page[i] = folder
                    self.normalize()
                    return folder
        raise ValueError(f"{target} not on any page")

    def _remove_app_anywhere(self, app_id: str) -> None:
        loc = self.find(app_id)
        if not loc:
            return
        p, i, folder = loc
        if folder is None:
            del self.pages[p][i]
        else:
            folder["apps"].remove(app_id)
            if len(folder["apps"]) == 1:          # a folder needs 2 apps
                self.pages[p][i] = folder["apps"][0]

    def take_out_of_folder(self, folder: dict, app_id: str, page: int, index: int) -> None:
        folder["apps"].remove(app_id)
        loc = [(p, i) for p, pg in enumerate(self.pages) for i, it in enumerate(pg) if it is folder]
        if loc and len(folder["apps"]) == 1:
            p, i = loc[0]
            self.pages[p][i] = folder["apps"][0]
        self.pages[page].insert(min(index, len(self.pages[page])), app_id)
        self.normalize()

    def hide(self, app_id: str) -> None:
        self._remove_app_anywhere(app_id)
        if app_id not in self.hidden:
            self.hidden.append(app_id)
        self.normalize()


def search(installed_meta: dict, query: str, limit: int = None) -> list:
    """installed_meta: id -> (name, extra searchable text). Prefix matches on
    the name first, then word prefixes, then substrings anywhere."""
    q = query.strip().lower()
    if not q:
        return []
    ranked = []
    for app_id, (name, extra) in installed_meta.items():
        n = name.lower()
        if n.startswith(q):
            rank = 0
        elif any(w.startswith(q) for w in n.split()):
            rank = 1
        elif q in n:
            rank = 2
        elif q in extra.lower():
            rank = 3
        else:
            continue
        ranked.append((rank, n, app_id))
    return [a for _r, _n, a in sorted(ranked)][:limit or PER_PAGE]
