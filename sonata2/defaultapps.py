"""Default apps (Settings > Default Apps): which app opens web links, mail,
calendar invites, music, videos, pictures, PDFs, text files and folders.

Each kind is a set of MIME types; choosing an app makes it the default for
the ones it can open (mimeapps.list, through Gio), the web browser through
xdg-settings (it also sets http/https/text/html the way every desktop reads).

    for k in KINDS: candidates(k.id), current(k.id)
    set_default(kind_id, desktop_id)"""
from collections import namedtuple

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

Kind = namedtuple("Kind", "id title icon types")

KINDS = (
    Kind("web", "Web Browser", "web-browser-symbolic",
         ("x-scheme-handler/https", "x-scheme-handler/http", "text/html")),
    Kind("mail", "Mail", "mail-unread-symbolic", ("x-scheme-handler/mailto",)),
    Kind("calendar", "Calendar", "x-office-calendar-symbolic", ("text/calendar",)),
    Kind("music", "Music", "audio-x-generic-symbolic",
         ("audio/mpeg", "audio/flac", "audio/x-flac", "audio/ogg", "audio/x-vorbis+ogg", "audio/opus",
          "audio/mp4", "audio/x-m4a", "audio/aac", "audio/wav", "audio/x-wav")),
    Kind("video", "Videos", "video-x-generic-symbolic",
         ("video/mp4", "video/x-matroska", "video/webm", "video/quicktime", "video/x-msvideo", "video/mpeg",
          "video/ogg")),
    Kind("photos", "Pictures", "image-x-generic-symbolic",
         ("image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff", "image/heic",
          "image/avif")),
    Kind("pdf", "PDF Documents", "x-office-document-symbolic", ("application/pdf",)),
    Kind("text", "Text Files", "text-x-generic-symbolic", ("text/plain", "text/markdown")),
    Kind("files", "Folders", "folder-symbolic", ("inode/directory",)),
)
BY_ID = {k.id: k for k in KINDS}


def candidates(kind_id: str) -> list:
    """[(desktop id, name)] of the apps that open this kind, each once."""
    seen, out = set(), []
    for a in Gio.AppInfo.get_all_for_type(BY_ID[kind_id].types[0]):
        did = a.get_id()
        if did and did not in seen and a.should_show():
            seen.add(did)
            out.append((did, a.get_display_name()))
    return out


def current(kind_id: str) -> str:
    """The default app's desktop id ("" when none)."""
    a = Gio.AppInfo.get_default_for_type(BY_ID[kind_id].types[0], False)
    return (a.get_id() or "") if a else ""


def types_for(app, kind_id: str) -> list:
    """The kind's types this app opens (always the first one)."""
    types = BY_ID[kind_id].types
    supported = set(app.get_supported_types() or ())
    return [t for i, t in enumerate(types) if i == 0 or t in supported]


def set_default(kind_id: str, desktop_id: str) -> bool:
    if kind_id == "web":
        from .backend import system
        return system.set_default_browser(desktop_id)
    try:
        from .apps import DesktopAppInfo
        app = DesktopAppInfo.new(desktop_id)
    except Exception:
        app = None
    if app is None:
        return False
    ok = True
    for t in types_for(app, kind_id):
        try:
            app.set_as_default_for_type(t)
        except Exception:
            ok = False
    return ok
