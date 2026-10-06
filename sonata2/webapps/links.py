"""Which links stay in a web app (the same site): its WebKit window.
(A Chromium web app -- chromeguard.py -- sends every new tab or window to
the default browser: Chromium has no way to open one inside the app.)"""
import ipaddress
import urllib.parse

import gi
from gi.repository import GLib


def site(url: str) -> str:
    """The part of a host that is "the same site": web.whatsapp.com -> whatsapp.com."""
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        return host                                  # an IP is a site of its own (no labels to share)
    except ValueError:
        pass
    base = _base_domain(host)
    if base:
        return base
    parts = host.split(".")
    if len(parts) > 2 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:      # example.co.uk
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _base_domain(host: str):
    """libsoup's public suffix list (WebKit's own): a.github.io and b.github.io
    are different sites. None without it (or for localhost and the like)."""
    try:
        gi.require_version("Soup", "3.0")
        from gi.repository import Soup
        return Soup.tld_get_base_domain(host)
    except (ValueError, ImportError, GLib.Error):
        return None


def stays_inside(app_url: str, target: str) -> bool:
    if not target or target.startswith(("about:", "blob:", "data:")):
        return True
    if urllib.parse.urlsplit(target).scheme not in ("http", "https"):
        return False
    return site(target) == site(app_url)
