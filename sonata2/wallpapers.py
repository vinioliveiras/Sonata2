"""Sonata's own wallpapers (data/wallpapers, photos from Unsplash under the
Unsplash License), shown in Settings > Wallpaper. No GTK here.

A wallpaper is a picture for Light and one for Dark (the same file for
most); "Mountains", the default, changes with the appearance: snowy peaks
by day, the same kind of peaks under the stars at night.

    CATALOG                      [Wallpaper(id, name, light, dark)]
    uri(path) -> "file://..."
    default_uris() -> (light, dark)
    current(light_uri, dark_uri) -> id or ""
    thumb(wallpaper) -> small picture for the gallery
"""
import os
from collections import namedtuple

from .i18n import N_

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wallpapers")

Wallpaper = namedtuple("Wallpaper", "id name light dark")        # name: shown translated, _(w.name)


def _w(wid, name, light, dark=None) -> Wallpaper:
    return Wallpaper(wid, name, os.path.join(FOLDER, light + ".jpg"), os.path.join(FOLDER, (dark or light) + ".jpg"))


CATALOG = [
    _w("mountains", N_("Mountains"), "snow-peaks", "starry-night"),        # the default (Light / Dark)
    _w("above-the-clouds", N_("Above the Clouds"), "above-the-clouds"),
    _w("snow-peaks", N_("Snow Peaks"), "snow-peaks"),
    _w("starry-night", N_("Starry Night"), "starry-night"),
    _w("lake-cabin", N_("Lake Cabin"), "lake-cabin"),
    _w("valley", N_("Valley"), "valley"),
    _w("golden-meadow", N_("Golden Meadow"), "golden-meadow"),
    _w("rolling-hills", N_("Rolling Hills"), "rolling-hills"),
    _w("highlands", N_("Highlands"), "highlands"),
    _w("pier", N_("Pier"), "pier"),
]
DEFAULT = CATALOG[0]


def uri(path: str) -> str:
    return "file://" + path


def default_uris() -> tuple:
    return uri(DEFAULT.light), uri(DEFAULT.dark)


def current(light_uri: str, dark_uri: str) -> str:
    """The catalog id the desktop shows now ("" for the user's own picture)."""
    dark_uri = dark_uri or light_uri
    for w in CATALOG:
        if (uri(w.light), uri(w.dark)) == (light_uri, dark_uri):
            return w.id
    return ""


def thumb(w: Wallpaper) -> str:
    """Small copy for the gallery (the full 4K picture would cost memory)."""
    path = os.path.join(FOLDER, "thumbs", w.id + ".jpg")       # Mountains: half day, half night
    return path if os.path.exists(path) else w.light
