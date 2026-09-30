"""Preview's Info panel (macOS Preview: Tools > Show Inspector, ⌘I):
General (size in pixels, file size, kind, colour space, dates) and, when
the picture carries them, the camera's EXIF details (make/model, lens,
exposure, ISO, focal length, date taken, GPS). Pillow reads the EXIF when
installed; without it the panel shows what GTK/GIO know and says so.

    data = read(path)          # off the main loop: plain data
    panel.show(path, data)"""
import datetime
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402

INFO_W = 250

ui.register("""
.pv-info { background: %(sidebar_material)s; box-shadow: inset 1px 0 %(separator)s; }
.pv-info-body { padding: 14px 14px 12px 14px; }
.pv-info-title { font-weight: 700; font-size: %(text_body)s; color: %(label)s; }
.pv-info-section { font-size: %(text_small)s; font-weight: 700; color: %(label_secondary)s; margin-top: 12px;
  margin-bottom: 2px; }
.pv-info-key { font-size: %(text_small)s; color: %(label_secondary)s; }
.pv-info-value { font-size: %(text_small)s; color: %(label)s; }
.pv-info-note { font-size: %(text_small)s; color: %(label_tertiary)s; margin-top: 12px; }
""", key="preview-info")

MODES = {"1": "Black & White", "L": "Gray", "LA": "Gray", "P": "Indexed", "RGB": "RGB", "RGBA": "RGB",
         "CMYK": "CMYK", "YCbCr": "YCbCr", "LAB": "Lab", "HSV": "HSV", "I": "Gray (32-bit)", "F": "Gray (float)",
         "I;16": "Gray (16-bit)"}
# EXIF tags (ids, so no Pillow import is needed to name them)
MAKE, MODEL, DATETIME = 0x010F, 0x0110, 0x0132
EXIF_IFD, GPS_IFD = 0x8769, 0x8825
EXPOSURE, FNUMBER, ISO, TAKEN, FOCAL, LENS, LENS_MAKE = 0x829A, 0x829D, 0x8827, 0x9003, 0x920A, 0xA434, 0xA433


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            return v[0] / v[1]
        except Exception:
            return None


def _text(v) -> str:
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return str(v).strip("\x00 ").strip() if v is not None else ""


def exposure_text(v) -> str:
    x = _num(v)
    if not x or x <= 0:
        return ""
    if x < 1:
        return f"1/{round(1 / x)} s"
    return f"{x:g} s"


def fnumber_text(v) -> str:
    x = _num(v)
    return f"ƒ/{x:.1f}".replace(".0", "") if x else ""


def focal_text(v) -> str:
    x = _num(v)
    return f"{x:g} mm" if x else ""


def exif_date_text(v) -> str:
    """"2024:05:01 12:34:56" -> "May 1, 2024 at 12:34"."""
    s = _text(v)
    try:
        d = datetime.datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return s
    return date_text(d)


def date_text(d: datetime.datetime) -> str:
    return f"{d.strftime('%b')} {d.day}, {d.year} at {d.strftime('%H:%M')}"


def read(path: str, texture_size=None) -> dict:
    """Everything the panel shows, as plain data (safe in a thread)."""
    info = {"name": os.path.basename(path), "general": [], "camera": [], "note": ""}
    g = info["general"]
    try:
        st = os.stat(path)
    except OSError:
        st = None
    kind = ""
    try:
        ct = Gio.content_type_guess(path, None)[0]
        kind = Gio.content_type_get_description(ct) or ""
    except Exception:
        pass
    im_info = _pillow(path)
    w, h = im_info.get("size") or texture_size or (0, 0)
    if w and h:
        g.append(("Dimensions", f"{w} × {h} pixels"))
        mp = w * h / 1e6
        if mp >= 1:
            g.append(("Megapixels", f"{mp:.1f}"))
    if st:
        from ..ui import fmt
        g.append(("File Size", fmt.size(st.st_size)))
    fmt_name = im_info.get("format")
    g.append(("Kind", f"{kind} ({fmt_name})" if fmt_name and kind and fmt_name.lower() not in kind.lower()
              else kind or fmt_name or "Picture"))
    if im_info.get("profile"):
        g.append(("Color Space", im_info["profile"]))
    if im_info.get("mode"):
        g.append(("Color Model", MODES.get(im_info["mode"], im_info["mode"])))
        g.append(("Alpha Channel", "Yes" if im_info.get("alpha") else "No"))
    if im_info.get("dpi"):
        g.append(("Resolution", f"{round(im_info['dpi'])} ppi"))
    if st:
        g.append(("Modified", date_text(datetime.datetime.fromtimestamp(st.st_mtime))))
    info["camera"] = im_info.get("camera", [])
    if im_info.get("missing"):
        info["note"] = "Camera details need Pillow (python-pillow)."
    return info


def _pillow(path: str) -> dict:
    try:
        from PIL import Image
    except ImportError:
        return {"missing": True}
    from ..imageload import is_raw, raw_preview
    out = {}
    try:
        if is_raw(path):
            import io
            data = raw_preview(path)
            if not data:
                return out
            im = Image.open(io.BytesIO(data))
            out["format"] = os.path.splitext(path)[1].lstrip(".").upper() + " (RAW)"
        else:
            im = Image.open(path)
            out["format"] = im.format
        with im:
            out["size"] = im.size if not is_raw(path) else None
            out["mode"] = im.mode
            out["alpha"] = "A" in im.getbands() or "transparency" in im.info
            dpi = im.info.get("dpi")
            if dpi and _num(dpi[0]) and _num(dpi[0]) > 1:
                out["dpi"] = _num(dpi[0])
            icc = im.info.get("icc_profile")
            if icc:
                out["profile"] = _profile_name(icc)
            exif = im.getexif() if hasattr(im, "getexif") else None
            if exif:
                out["camera"] = camera_rows(exif)
    except Exception:
        pass
    return out


def _profile_name(icc: bytes) -> str:
    try:
        import io
        from PIL import ImageCms
        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip()
    except Exception:
        return "Embedded profile"


def camera_rows(exif) -> list:
    """(label, value) rows from a Pillow Exif mapping."""
    try:
        sub = exif.get_ifd(EXIF_IFD) or {}
    except Exception:
        sub = {}
    try:
        gps = exif.get_ifd(GPS_IFD) or {}
    except Exception:
        gps = {}

    def tag(t):
        v = sub.get(t)
        return exif.get(t) if v is None else v
    rows = []
    make, model = _text(exif.get(MAKE)), _text(exif.get(MODEL))
    if model and make and not model.lower().startswith(make.split()[0].lower()):
        model = f"{make} {model}"
    if model or make:
        rows.append(("Camera", model or make))
    lens = _text(tag(LENS))
    if lens:
        rows.append(("Lens", lens))
    for label, t, f in (("Exposure", EXPOSURE, exposure_text), ("Aperture", FNUMBER, fnumber_text),
                        ("Focal Length", FOCAL, focal_text)):
        v = f(tag(t))
        if v:
            rows.append((label, v))
    iso = tag(ISO)
    if isinstance(iso, (tuple, list)):
        iso = iso[0] if iso else None
    if iso:
        rows.append(("ISO", str(iso)))
    taken = tag(TAKEN) or exif.get(DATETIME)
    if taken:
        rows.append(("Date Taken", exif_date_text(taken)))
    if gps:
        rows.append(("Location", "GPS coordinates included"))
    return rows


class InfoPanel(Gtk.ScrolledWindow):
    def __init__(self):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["pv-info"])
        self.set_size_request(INFO_W, -1)
        self.set_hexpand(False)                  # its wrapping labels mustn't widen it
        self.path = None
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["pv-info-body"])
        self.set_child(self.box)

    def clear(self) -> None:
        while (c := self.box.get_first_child()) is not None:
            self.box.remove(c)

    def show(self, path: str, data: dict) -> None:
        self.path = path
        self.clear()
        title = Gtk.Label(label=data.get("name", ""), xalign=0, wrap=True, css_classes=["pv-info-title"])
        title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.box.append(title)
        grid = Gtk.Grid(column_spacing=8, row_spacing=4)      # one grid: both sections' columns line up
        self._section(grid, "General Info", data.get("general", []))
        if data.get("camera"):
            self._section(grid, "Camera", data["camera"])
        self.box.append(grid)
        if data.get("note"):
            self.box.append(Gtk.Label(label=data["note"], xalign=0, wrap=True, css_classes=["pv-info-note"]))

    @staticmethod
    def _section(grid, name: str, rows) -> None:
        top = 0
        while grid.get_child_at(0, top) is not None or grid.get_child_at(1, top) is not None:
            top += 1
        grid.attach(Gtk.Label(label=name, xalign=0, css_classes=["pv-info-section"]), 0, top, 2, 1)
        for i, (k, v) in enumerate(rows, top + 1):
            grid.attach(Gtk.Label(label=k, xalign=1, yalign=0, css_classes=["pv-info-key"]), 0, i, 1, 1)
            val = Gtk.Label(label=v, xalign=0, wrap=True, hexpand=True, css_classes=["pv-info-value"])
            val.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            grid.attach(val, 1, i, 1, 1)
