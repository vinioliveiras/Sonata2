"""Folders linked between the Dock and Launchpad (Vini): a folder dragged
from one to the other stays the same folder there -- renamed, or an app put
in or taken out, the other follows. (The Dock's lock stays the Dock's.)

The link is a random id kept in both files: dock.json folders[id]["link"]
and the Launchpad folder's "link". Each side pushes its folders after it
saves; the other process reloads from its file (config.watch). A file is
written only when something differs, so the two never answer each other in
a loop. Folders saved before links existed are linked when their name and
apps match.

    new_link()
    to_launchpad(dock_folders) -> bool     # written?
    to_dock(launchpad_data) -> bool
"""
import json
import os
import uuid

from . import config

LP_DEFAULTS = {"pages": [], "hidden": []}


def new_link() -> str:
    return uuid.uuid4().hex[:12]


def _raw(name: str) -> dict:
    try:
        with open(os.path.join(config.CONFIG_DIR, name + ".json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _lp_folders(data: dict) -> list:
    return [it for page in data.get("pages") or [] if isinstance(page, list)
            for it in page if isinstance(it, dict) and isinstance(it.get("apps"), list)]


def _dock_folders(folders) -> list:
    return [f for f in (folders or {}).values() if isinstance(f, dict) and isinstance(f.get("apps"), list)]


def link_existing(dock_folders, lp_data) -> bool:
    """Unlinked folders with the same name and apps on both sides: linked."""
    changed = False
    for d in _dock_folders(dock_folders):
        if d.get("link"):
            continue
        for f in _lp_folders(lp_data):
            if not f.get("link") and f.get("folder") == d.get("name") and set(f["apps"]) == set(d["apps"]):
                f["link"] = d["link"] = new_link()
                changed = True
                break
    return changed


def to_launchpad(dock_folders) -> bool:
    """The Dock's linked folders -> launchpad.json: name and apps. An app
    put in leaves its other place in Launchpad; one taken out goes at the end."""
    dock_folders = dock_folders or {}
    data = _raw("launchpad")
    if not data.get("pages"):
        return False
    linked_now = link_existing(dock_folders, data)
    links = {d["link"]: d for d in _dock_folders(dock_folders) if d.get("link")}
    hidden = set(data.get("hidden") or [])
    changed = linked_now
    for f in _lp_folders(data):
        d = links.get(f.get("link"))
        if d is None:
            continue
        if d.get("name") and f.get("folder") != d["name"]:
            f["folder"] = d["name"]
            changed = True
        want = [a for a in dict.fromkeys(d["apps"]) if a not in hidden]
        if want == f["apps"]:
            continue
        gone = [a for a in f["apps"] if a not in want]
        for page in data["pages"]:                         # an app put in: out of its other place
            page[:] = [it for it in page if not (isinstance(it, str) and it in want)]
            for it in page:
                if isinstance(it, dict) and it is not f and isinstance(it.get("apps"), list):
                    it["apps"] = [a for a in it["apps"] if a not in want]
        f["apps"] = want
        data["pages"][-1].extend(gone)                     # taken out: at the end (Launchpad repacks)
        changed = True
    if changed:
        config.save("launchpad", data)
    if linked_now:
        config.update("dock", folders=dock_folders)
    return changed


def to_dock(lp_data) -> bool:
    """Launchpad's linked folders -> dock.json: name and apps (apps hidden
    in Launchpad stay in the Dock's copy)."""
    dock = _raw("dock")
    folders = dock.get("folders")
    if not isinstance(folders, dict) or not folders:
        return False
    lp_data = lp_data or {}
    changed = link_existing(folders, lp_data)
    if changed:
        config.save("launchpad", lp_data)
    hidden = set(lp_data.get("hidden") or [])
    by_link = {d["link"]: d for d in _dock_folders(folders) if d.get("link")}
    for f in _lp_folders(lp_data):
        d = by_link.get(f.get("link"))
        if d is None:
            continue
        if f.get("folder") and d.get("name") != f["folder"]:
            d["name"] = f["folder"]
            changed = True
        want = list(f["apps"]) + [a for a in d["apps"] if a in hidden and a not in f["apps"]]
        if want != d["apps"]:
            d["apps"] = want
            changed = True
    if changed:
        config.update("dock", folders=folders)
    return changed
