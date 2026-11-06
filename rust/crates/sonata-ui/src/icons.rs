//! Sonata's own icons, independent of the system icon theme (icons.py).
//!
//! Icons are looked up in the bundled `Sonata` theme (overrides ->
//! Sonata-MacTahoe -> hicolor), chosen in appearance.json (`icon_theme`),
//! never in the Linux one; only an icon none of those has comes from the
//! system theme. setup() runs once, before any widget (sonata_ui::setup).
//!
//! App icons (app_icon): the desktop entry's icon is looked up under several
//! names (Icon=, desktop id, StartupWMClass, executable...) in Sonata's own
//! themes. Apps Sonata has no artwork for keep their own icon, drawn on a
//! Big Sur squircle plate so every app icon has the same shape; the plate
//! takes the icon's colour when the icon is a solid tile of its own. The
//! plated icons are made once and kept as PNGs (~/.cache/sonata2/app-icons,
//! the Python Sonata's: the same names, so either one reuses the other's).
//!
//! The plate's outline is drawn with cairo, used as a mask: icons.py fills
//! a GSK path, which needs GTK 4.14; this build runs on 4.12.

use gtk::prelude::*;
use gtk::{gdk, gdk_pixbuf, gio, glib, graphene, gsk};
use serde_json::{json, Map, Value};
use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

pub const SHAPES: [&str; 3] = ["squircle", "circle", "rounded"];
pub const SHAPE_TITLES: [(&str, &str); 3] = [("squircle", sonata_i18n::n_!("Squircle")), ("circle", sonata_i18n::n_!("Circle")), ("rounded", sonata_i18n::n_!("Rounded Square"))];
pub const SOURCES: [&str; 4] = ["auto", "package", "file", "theme"];
/// how big an app's picture sits in its frame: a fraction of the frame's width
pub const SCALE_RANGE: (f64, f64) = (0.3, 1.0);
pub const GEN_SIZE: i32 = 256;
/// bump when the plate's look changes: every icon is made again (icons.py's)
pub const PLATE_VERSION: u32 = 11;
/// The plate's geometry, measured on MacTahoe's own artwork: a
/// superellipse |x|^4 + |y|^4 = 1 filling 89 % of the tile, a soft shadow
/// under it, the app's icon at 62 %.
pub const PLATE_INSET: f64 = 0.055;
pub const PLATE_EXPONENT: f64 = 4.0;
pub const PLATE_ARTWORK: f64 = 0.62;
/// an icon's own tile: this much past the frame (its edge cut off)
const TILE_BLEED: f64 = 1.06;
/// what's on an icon's own white tile: this share of the frame
const WHITE_TILE_SIZE: f64 = 0.56;
/// the logo on an icon's own coloured tile: this share of the frame
const TILE_LOGO_SIZE: f64 = 0.42;
/// a round icon: its circle this share of the frame
const CIRCLE_SIZE: f64 = 0.94;
const PLATE_WHITE: &str = "#ffffff";
/// the sheen over the whole plate and the icon on it: top, bottom
const PLATE_SHEEN: (&str, &str) = ("rgba(255,255,255,0.10)", "rgba(0,0,0,0.075)");
/// a game's shortcut (steam steam://rungameid/...): not the launcher's icon
const LAUNCHERS: [&str; 7] = ["steam", "lutris", "heroic", "xdg-open", "gtk-launch", "gio", "env"];
/// Icons drawn in two versions, one per appearance: name -> (Light, Dark)
const APPEARANCE_VARIANTS: [(&str, &str, &str); 3] = [("user-trash", "user-trash", "user-trash-dark"), ("user-trash-full", "user-trash-full", "user-trash-full-dark"), ("sonata-launchpad", "sonata-launchpad-light", "sonata-launchpad")];

thread_local! {
    /// a Gtk.IconTheme with the system's theme, for fallbacks
    static SYSTEM: RefCell<Option<gtk::IconTheme>> = const { RefCell::new(None) };
    static HICOLOR: RefCell<Option<gtk::IconTheme>> = const { RefCell::new(None) };
    static OWN: RefCell<Option<HashSet<String>>> = const { RefCell::new(None) };
    static ANALYSED: RefCell<Analysed> = RefCell::default();
    static RENDERED: RefCell<HashMap<(String, i32), gdk::Texture>> = RefCell::default();
}

#[derive(Default)]
struct Analysed {
    tones: HashMap<(String, bool), Option<String>>,
    boxes: HashMap<String, Option<Box4>>,
    shapes: HashMap<String, Option<(String, String)>>,
    filtered: HashMap<String, bool>,
}

/// (x, y, w, h) as fractions of a picture
pub type Box4 = (f64, f64, f64, f64);

/// Sonata's icon themes (data/icons).
pub fn icons_dir() -> PathBuf {
    sonata_config::data_path("icons")
}

/// The icon theme chosen in appearance.json ("Sonata").
pub fn theme_name() -> String {
    let a = sonata_config::load("appearance", &json!({"icon_theme": "Sonata"}));
    a.get("icon_theme").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("Sonata").to_string()
}

/// Sonata's own icon theme, process-local (the system setting stays).
pub fn setup() {
    let (Some(settings), Some(display)) = (gtk::Settings::default(), gdk::Display::default()) else { return };
    let system = gtk::IconTheme::new();
    if let Some(name) = settings.gtk_icon_theme_name() {
        system.set_theme_name(Some(&name));
    }
    SYSTEM.with(|s| s.replace(Some(system)));
    gtk::IconTheme::for_display(&display).add_search_path(icons_dir());
    settings.set_gtk_icon_theme_name(Some(&theme_name()));
}

fn system() -> Option<gtk::IconTheme> {
    SYSTEM.with(|s| s.borrow().clone())
}

fn hicolor() -> gtk::IconTheme {
    HICOLOR.with(|h| {
        h.borrow_mut()
            .get_or_insert_with(|| {
                let t = gtk::IconTheme::new();
                t.set_theme_name(Some("hicolor"));
                t
            })
            .clone()
    })
}

// -- app icons ---------------------------------------------------------------------------------
/// Icon names Sonata's themes draw (app artwork).
fn own() -> HashSet<String> {
    OWN.with(|o| {
        o.borrow_mut()
            .get_or_insert_with(|| {
                let mut names = HashSet::new();
                for rel in ["Sonata/apps/scalable", "Sonata-MacTahoe/apps/scalable"] {
                    if let Ok(rd) = std::fs::read_dir(icons_dir().join(rel)) {
                        for e in rd.flatten() {
                            let n = e.file_name().to_string_lossy().into_owned();
                            names.insert(n.rsplit_once('.').map(|x| x.0.to_string()).unwrap_or(n));
                        }
                    }
                }
                names
            })
            .clone()
    })
}

fn desktop(info: &gio::AppInfo) -> Option<gio::DesktopAppInfo> {
    info.clone().downcast::<gio::DesktopAppInfo>().ok()
}

/// A desktop entry's field ("" when it has none).
pub fn entry_field(info: &gio::AppInfo, key: &str) -> String {
    desktop(info).and_then(|d| d.string(key)).map(|s| s.to_string()).unwrap_or_default()
}

/// The desktop id without ".desktop".
pub fn app_key(info: &gio::AppInfo) -> String {
    let id = info.id().map(|s| s.to_string()).unwrap_or_default();
    id.strip_suffix(".desktop").map(String::from).unwrap_or(id)
}

/// The names an app's icon may have in Sonata's themes, in order.
pub fn candidates_from(icon_names: &[String], file_icon: Option<&str>, key: &str, wm: &str, exec: &str, name: &str) -> Vec<String> {
    let mut out: Vec<String> = icon_names.to_vec();
    if let Some(base) = file_icon {
        let stem = Path::new(base).file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        out.push(stem.clone());
        out.push(stem.split("-linux").next().unwrap_or("").to_string()); // spotify-linux-512 -> spotify
    }
    let first = exec.split(' ').next().unwrap_or("");
    let mut exe = Path::new(first).file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    if LAUNCHERS.contains(&exe.as_str()) || exec.contains("://") {
        exe.clear();
    }
    for n in [key.to_string(), wm.to_string(), exe, name.replace(' ', "-")] {
        if !n.is_empty() {
            out.push(n.clone());
            out.push(n.to_lowercase());
        }
    }
    out
}

fn candidates(info: &gio::AppInfo) -> Vec<String> {
    let icon = info.icon();
    let themed: Vec<String> = icon.as_ref().and_then(|i| i.downcast_ref::<gio::ThemedIcon>()).map(|t| t.names().iter().map(|s| s.to_string()).collect()).unwrap_or_default();
    let file = icon.as_ref().and_then(|i| i.downcast_ref::<gio::FileIcon>()).and_then(|f| f.file().basename()).map(|b| b.to_string_lossy().into_owned());
    let wm = desktop(info).and_then(|d| d.startup_wm_class()).map(|s| s.to_string()).unwrap_or_default();
    let exec = info.commandline().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default();
    candidates_from(&themed, file.as_deref(), &app_key(info), &wm, &exec, &info.name())
}

/// A Steam game's shortcut (Icon=steam_icon_<id>): its picture, or None.
fn steam_game_picture(info: &gio::AppInfo) -> Option<Option<PathBuf>> {
    let icon = info.icon()?;
    let t = icon.downcast_ref::<gio::ThemedIcon>()?;
    let id = t.names().iter().find_map(|n| n.strip_prefix("steam_icon_").filter(|d| !d.is_empty() && d.chars().all(|c| c.is_ascii_digit())).map(String::from))?;
    Some(steam_icon_path(&id))
}

/// The biggest icon Steam keeps for a game (steamgames.icon_path): a
/// desktop shortcut's hicolor PNG, else the library cache's square icon.
pub fn steam_icon_path(id: &str) -> Option<PathBuf> {
    let home = glib::home_dir();
    let data = std::env::var_os("XDG_DATA_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| home.join(".local/share"));
    let mut best: Option<(i32, PathBuf)> = None;
    if let Ok(sizes) = std::fs::read_dir(data.join("icons/hicolor")) {
        for s in sizes.flatten() {
            let px: i32 = s.file_name().to_string_lossy().split('x').next().and_then(|n| n.parse().ok()).unwrap_or(0);
            let f = s.path().join("apps").join(format!("steam_icon_{id}.png"));
            if f.exists() && best.as_ref().is_none_or(|b| px > b.0) {
                best = Some((px, f));
            }
        }
    }
    if let Some((_, f)) = best {
        return Some(f);
    }
    for d in ["~/.local/share/Steam", "~/.steam/steam", "~/.var/app/com.valvesoftware.Steam/.local/share/Steam"] {
        let root = home.join(&d[2..]);
        let cache = std::fs::canonicalize(&root).unwrap_or(root).join("appcache/librarycache");
        if let Ok(rd) = std::fs::read_dir(cache.join(id)) {
            for e in rd.flatten() {
                let n = e.file_name().to_string_lossy().into_owned();
                if n.len() == 44 && n.ends_with(".jpg") && n[..40].chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()) {
                    return Some(e.path());
                }
            }
        }
        let old = cache.join(format!("{id}_icon.jpg"));
        if old.exists() {
            return Some(old);
        }
    }
    None
}

// -- the user's choices (Settings > App Icons; icons.json) ----------------------------------------
pub fn clamp_scale(v: f64) -> f64 {
    (v.clamp(SCALE_RANGE.0, SCALE_RANGE.1) * 100.0).round() / 100.0
}

pub fn default_scale() -> f64 {
    (PLATE_ARTWORK / (1.0 - 2.0 * PLATE_INSET) * 100.0).round() / 100.0
}

/// icons.json, with its values checked.
pub fn prefs() -> Map<String, Value> {
    let mut data = sonata_config::load("icons", &json!({"shape": "squircle", "source": "auto", "apps": {}}));
    if !data.get("shape").and_then(Value::as_str).is_some_and(|s| SHAPES.contains(&s)) {
        data.insert("shape".into(), json!("squircle"));
    }
    if !data.get("source").and_then(Value::as_str).is_some_and(|s| s == "auto" || s == "package") {
        data.insert("source".into(), json!("auto"));
    }
    if !data.get("apps").is_some_and(Value::is_object) {
        data.insert("apps".into(), json!({}));
    }
    data
}

#[derive(Clone, Debug, PartialEq)]
pub struct AppPref {
    pub source: String,
    pub path: String,
    pub name: String,
    pub scale: Option<f64>,
    pub shape: String,
}

/// {"source", "path", "name", "scale", "shape"} of one app (shape resolved).
pub fn pref_of(prefs: &Map<String, Value>, key: &str) -> AppPref {
    let own = prefs.get("apps").and_then(|a| a.get(key)).cloned().unwrap_or(json!({}));
    let s = |k: &str| own.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let all = |k: &str| prefs.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let source = Some(s("source")).filter(|v| SOURCES.contains(&v.as_str())).unwrap_or_else(|| all("source"));
    let shape = Some(s("shape")).filter(|v| SHAPES.contains(&v.as_str())).unwrap_or_else(|| all("shape"));
    AppPref { source, path: s("path"), name: s("name"), scale: own.get("scale").and_then(Value::as_f64).map(clamp_scale), shape }
}

pub fn app_pref(info: &gio::AppInfo) -> AppPref {
    pref_of(&prefs(), &app_key(info))
}

/// The shape every frame follows (folders too).
pub fn frame_shape() -> String {
    prefs().get("shape").and_then(Value::as_str).unwrap_or("squircle").to_string()
}

pub fn package_icon(info: &gio::AppInfo) -> gio::Icon {
    info.icon().unwrap_or_else(|| gio::ThemedIcon::new("application-x-executable").upcast())
}

/// The icon to show for an app: what the user chose for it (Settings > App
/// Icons), else Sonata's artwork when any of its names match, else the
/// app's own icon -- on the frame, in the chosen shape.
pub fn app_icon(info: &gio::AppInfo) -> gio::Icon {
    let pref = app_pref(info);
    let (shape, scale) = (pref.shape.as_str(), pref.scale);
    if pref.source == "file" && !pref.path.is_empty() {
        if let Some(made) = picture_icon(Path::new(&pref.path), shape, true, scale) {
            return made;
        }
    }
    if pref.source == "theme" && !pref.name.is_empty() {
        return plated_icon(&gio::ThemedIcon::new(&pref.name).upcast(), shape, scale);
    }
    if pref.source != "package" {
        if let Some(pic) = steam_game_picture(info) {
            if let Some(made) = pic.and_then(|p| picture_icon(&p, shape, false, None)) {
                return made;
            }
        }
        let own = own();
        for name in candidates(info) {
            if own.contains(&name) {
                let themed: gio::Icon = gio::ThemedIcon::new(&name).upcast();
                if shape == "squircle" {
                    return themed;
                }
                return generated(&themed, shape, true, None).unwrap_or(themed);
            }
        }
    }
    plated_icon(&package_icon(info), shape, scale)
}

/// Its picture sits on a plate (its size can change): not Sonata's own
/// artwork, not a Steam game's picture.
pub fn plated(info: &gio::AppInfo) -> bool {
    let pref = app_pref(info);
    if ["file", "theme", "package"].contains(&pref.source.as_str()) {
        return true;
    }
    if steam_game_picture(info).flatten().is_some() {
        return false;
    }
    let own = own();
    !candidates(info).iter().any(|n| own.contains(n))
}

/// (Without a display the plate can't be drawn: the icon as it is.)
fn plated_icon(icon: &gio::Icon, shape: &str, scale: Option<f64>) -> gio::Icon {
    generated(icon, shape, false, scale).unwrap_or_else(|| icon.clone())
}

// -- generated app icons: plated once, saved to disk ---------------------------------------------
fn generated_dir() -> PathBuf {
    glib::user_cache_dir().join("sonata2").join("app-icons")
}

fn sha20(text: &str) -> String {
    let s = glib::compute_checksum_for_data(glib::ChecksumType::Sha1, text.as_bytes()).map(|s| s.to_string()).unwrap_or_default();
    s[..20.min(s.len())].to_string()
}

/// Python's str() of the values in a cache stamp: None, True/False, 0.75.
fn py_opt(v: Option<f64>) -> String {
    v.map(|f| format!("{f:?}")).unwrap_or_else(|| "None".into())
}

fn py_bool(b: bool) -> &'static str {
    if b { "True" } else { "False" }
}

fn mtime(path: &Path) -> Option<i64> {
    use std::os::unix::fs::MetadataExt;
    std::fs::metadata(path).ok().map(|m| m.mtime())
}

/// The cache file names and stamp of a plated icon (icons.py's, so both
/// builds share them).
pub fn generated_key(gicon_string: &str, src_path: &str, src_mtime: i64, shape: &str, reshape: bool, scale: Option<f64>) -> (String, String) {
    let stamp = format!("{src_path}\n{src_mtime}\n{PLATE_VERSION}\n{shape}\n{}\n{}", py_bool(reshape), py_opt(scale));
    (sha20(&format!("{gicon_string}|{shape}|{}|{}", py_bool(reshape), py_opt(scale))), stamp)
}

fn fresh(meta: &Path, png: &Path, stamp: &str) -> bool {
    std::fs::read_to_string(meta).is_ok_and(|s| s == stamp) && png.exists()
}

/// A gio::FileIcon of `gicon` on its plate (in `shape`), rendering it if
/// needed; None when it can't be rendered here (no display, icon not
/// found). reshape: Sonata's own artwork (already on a squircle) cut to
/// another shape instead of put on a plate.
pub fn generated(gicon: &gio::Icon, shape: &str, reshape: bool, scale: Option<f64>) -> Option<gio::Icon> {
    let display = gdk::Display::default()?;
    let size = if reshape || scale.is_some() { GEN_SIZE } else { (GEN_SIZE as f64 * PLATE_ARTWORK) as i32 };
    let src = resolve(&display, 1, gicon, size)?;
    // drawn by librsvg (an SVG with filters): its file still dates the picture
    let path = src.path.clone().or_else(|| theme_path(&display, gicon, GEN_SIZE))?;
    let (name, stamp) = generated_key(&gio::prelude::IconExt::to_string(gicon)?, &path.display().to_string(), mtime(&path)?, shape, reshape, scale);
    let dir = generated_dir();
    let (png, meta) = (dir.join(format!("{name}.png")), dir.join(format!("{name}.src")));
    if !fresh(&meta, &png, &stamp) {
        let paint: Box<dyn Fn(&gtk::Snapshot, f64)> = if reshape {
            let s = shape.to_string();
            Box::new(move |snap, w| draw_reshaped(snap, &src, w, &s))
        } else {
            let plate = Plate::new(src, false, shape, scale);
            Box::new(move |snap, w| plate.draw(snap, w))
        };
        if !render(&display, &paint, &png, &meta, &stamp) {
            return None;
        }
    }
    Some(gio::FileIcon::new(&gio::File::for_path(&png)).upcast())
}

/// A picture on the frame: a square one (a Steam game's icon) filling it
/// edge to edge; artwork (a custom icon the user picked): like an app's
/// own icon. None when it can't be made. Kept with the generated icons.
pub fn picture_icon(path: &Path, shape: &str, artwork: bool, scale: Option<f64>) -> Option<gio::Icon> {
    let display = gdk::Display::default()?;
    if !path.exists() {
        return None;
    }
    let p = path.display().to_string();
    let stamp = format!("picture\n{p}\n{}\n{PLATE_VERSION}\n{shape}\n{}\n{}", mtime(path)?, py_bool(artwork), py_opt(scale));
    let name = format!("pic-{}", sha20(&format!("{p}|{shape}|{}|{}", py_bool(artwork), py_opt(scale))));
    let dir = generated_dir();
    let (png, meta) = (dir.join(format!("{name}.png")), dir.join(format!("{name}.src")));
    if !fresh(&meta, &png, &stamp) {
        let src = if artwork {
            Source { paint: gtk::IconPaintable::for_file(&gio::File::for_path(path), GEN_SIZE, 1).upcast(), path: Some(path.to_path_buf()) }
        } else {
            Source { paint: gdk::Texture::from_filename(path).ok()?.upcast(), path: None }
        };
        let plate = Plate::new(src, !artwork, shape, scale);
        if !render(&display, &|snap: &gtk::Snapshot, w: f64| plate.draw(snap, w), &png, &meta, &stamp) {
            return None;
        }
    }
    Some(gio::FileIcon::new(&gio::File::for_path(&png)).upcast())
}

fn render(display: &gdk::Display, paint: &dyn Fn(&gtk::Snapshot, f64), png: &Path, meta: &Path, stamp: &str) -> bool {
    let _ = display;
    let Some(dir) = png.parent() else { return false };
    if std::fs::create_dir_all(dir).is_err() {
        return false;
    }
    let snap = gtk::Snapshot::new();
    paint(&snap, GEN_SIZE as f64);
    let Some(node) = snap.to_node() else { return false };
    let renderer = gsk::CairoRenderer::new();
    if renderer.realize(None::<&gdk::Surface>).is_err() {
        return false;
    }
    let tex = renderer.render_texture(&node, Some(&graphene::Rect::new(0.0, 0.0, GEN_SIZE as f32, GEN_SIZE as f32)));
    renderer.unrealize();
    // several shell processes may do it at once
    let pid = std::process::id();
    let (tmp, mtmp) = (PathBuf::from(format!("{}.{pid}.tmp", png.display())), PathBuf::from(format!("{}.{pid}.tmp", meta.display())));
    let ok = tex.save_to_png(&tmp).is_ok() && std::fs::rename(&tmp, png).is_ok() && std::fs::write(&mtmp, stamp).is_ok() && std::fs::rename(&mtmp, meta).is_ok();
    let _ = std::fs::remove_file(&tmp);
    let _ = std::fs::remove_file(&mtmp);
    ok
}

/// Settings: forget every generated icon (made again when next shown).
pub fn clear_generated() {
    let _ = std::fs::remove_dir_all(generated_dir());
}

// -- the plate -------------------------------------------------------------------------------------
/// A picture to draw, and its file when it has one (what the plate's
/// analysis reads).
#[derive(Clone)]
pub struct Source {
    pub paint: gdk::Paintable,
    pub path: Option<PathBuf>,
}

/// The frame's outline as a cairo path: macOS squircle, a circle, or a rounded square.
pub fn shape_path(cr: &gtk::cairo::Context, shape: &str, x: f64, y: f64, w: f64, h: f64) {
    cr.new_path();
    match shape {
        "circle" => cr.arc(x + w / 2.0, y + h / 2.0, w.min(h) / 2.0, 0.0, std::f64::consts::TAU),
        "rounded" => {
            let r = w.min(h) * 0.2;
            cr.new_sub_path();
            cr.arc(x + w - r, y + r, r, -std::f64::consts::FRAC_PI_2, 0.0);
            cr.arc(x + w - r, y + h - r, r, 0.0, std::f64::consts::FRAC_PI_2);
            cr.arc(x + r, y + h - r, r, std::f64::consts::FRAC_PI_2, std::f64::consts::PI);
            cr.arc(x + r, y + r, r, std::f64::consts::PI, 1.5 * std::f64::consts::PI);
        }
        _ => {
            for (i, (px, py)) in squircle_points(x, y, w, h, PLATE_EXPONENT, 96).into_iter().enumerate() {
                if i == 0 { cr.move_to(px, py) } else { cr.line_to(px, py) }
            }
        }
    }
    cr.close_path();
}

pub fn squircle_points(x: f64, y: f64, w: f64, h: f64, n: f64, steps: usize) -> Vec<(f64, f64)> {
    let (cx, cy, ax, ay) = (x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0);
    (0..steps)
        .map(|i| {
            let t = std::f64::consts::TAU * i as f64 / steps as f64;
            let (c, s) = (t.cos(), t.sin());
            (cx + ax * c.abs().powf(2.0 / n).copysign(c), cy + ay * s.abs().powf(2.0 / n).copysign(s))
        })
        .collect()
}

fn rgba(spec: &str) -> gdk::RGBA {
    gdk::RGBA::parse(spec).unwrap_or(gdk::RGBA::WHITE)
}

/// Everything drawn between this and pop() is cut to the shape (a cairo
/// mask: GSK path fills need GTK 4.14).
fn push_shape(snap: &gtk::Snapshot, shape: &str, x: f64, y: f64, w: f64, h: f64, size: f64) {
    snap.push_mask(gsk::MaskMode::Alpha);
    let cr = snap.append_cairo(&graphene::Rect::new(0.0, 0.0, size as f32, size as f32));
    shape_path(&cr, shape, x, y, w, h);
    cr.set_source_rgba(0.0, 0.0, 0.0, 1.0);
    let _ = cr.fill();
    snap.pop();
}

fn stroke_shape(snap: &gtk::Snapshot, shape: &str, x: f64, y: f64, w: f64, h: f64, size: f64) {
    let cr = snap.append_cairo(&graphene::Rect::new(0.0, 0.0, size as f32, size as f32));
    shape_path(&cr, shape, x, y, w, h);
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.10);
    cr.set_line_width((size / 256.0).max(0.5));
    let _ = cr.stroke();
}

fn paint_at(snap: &gtk::Snapshot, paint: &gdk::Paintable, x: f64, y: f64, a: f64) {
    snap.save();
    snap.translate(&graphene::Point::new(x as f32, y as f32));
    paint.snapshot(snap.upcast_ref::<gdk::Snapshot>(), a, a);
    snap.restore();
}

/// Sonata's own artwork (drawn on its squircle) cut to another shape: a
/// little larger, so its squircle edge falls outside the new outline.
fn draw_reshaped(snap: &gtk::Snapshot, src: &Source, w: f64, shape: &str) {
    let inset = w * PLATE_INSET;
    let pw = w - 2.0 * inset;
    push_shape(snap, shape, inset, inset, pw, pw, w);
    let a = w * 1.06; // the artwork's own edge, outside
    paint_at(snap, &src.paint, (w - a) / 2.0, (w - a) / 2.0, a);
    snap.pop();
    stroke_shape(snap, shape, inset, inset, pw, pw, w);
}

/// An app's own icon on a Big Sur squircle -- white, or the icon's own
/// colour when its edges are one solid colour (Claude's orange tile).
struct Plate {
    src: Source,
    full: bool,
    shape: String,
    scale: Option<f64>,
    color: String,
    tile: Option<Box4>,
    circle: Option<Box4>,
    logo: Option<(gdk::Texture, Box4)>,
    white_tile: Option<Box4>,
}

impl Plate {
    fn new(src: Source, full: bool, shape: &str, scale: Option<f64>) -> Self {
        let shape = if SHAPES.contains(&shape) { shape.to_string() } else { "squircle".into() };
        let path = src.path.clone();
        let tone = if full { None } else { path.as_deref().and_then(|p| solid_edge(p, false)) };
        let mut color = tone.clone().unwrap_or_else(|| PLATE_WHITE.into());
        // an icon with a tile of its own fills the frame: its own rounded
        // edge falls outside the frame's outline (a white tile too)
        let own = tone.clone().or_else(|| if full { None } else { path.as_deref().and_then(|p| solid_edge(p, true)) });
        let mut tile = if own.is_some() && scale.is_none() { path.as_deref().and_then(tile_of) } else { None };
        // a coloured tile: the plate takes its colour and only its logo is
        // drawn, at TILE_LOGO_SIZE of the frame (Vini: too big otherwise)
        let logo = match (&tone, tile, &path) {
            (Some(t), Some(_), Some(p)) => keyed_content(p, t, 256),
            _ => None,
        };
        let mut circle = None;
        if !full && scale.is_none() && tile.is_none() {
            if let Some((kind, colour)) = path.as_deref().and_then(shape_of_file) {
                color = colour; // its own tile, square or disc: fills the frame
                if kind == "circle" {
                    circle = path.as_deref().and_then(tile_of);
                } else {
                    tile = path.as_deref().and_then(tile_of);
                }
            }
        }
        let mut white_tile = None;
        if let (Some(o), None, Some(_)) = (&own, &tone, tile) {
            color = o.clone();
            white_tile = path.as_deref().and_then(|p| pixels_at(p, 96)).and_then(|px| content_box(&px, o));
        }
        Self { src, full, shape, scale, color, tile, circle, logo, white_tile }
    }

    fn draw(&self, snap: &gtk::Snapshot, w: f64) {
        let h = w;
        let inset = w * PLATE_INSET;
        let (pw, ph) = (w - 2.0 * inset, h - 2.0 * inset);
        let rect = graphene::Rect::new(inset as f32, inset as f32, pw as f32, ph as f32);
        let radius = (pw * if self.shape == "circle" { 0.5 } else { 0.3 }) as f32;
        let shadow = gsk::RoundedRect::from_rect(rect, radius);
        snap.append_outset_shadow(&shadow, &rgba("rgba(0,0,0,0.22)"), 0.0, (w * 0.012) as f32, 0.0, (w * 0.02) as f32);
        push_shape(snap, &self.shape, inset, inset, pw, ph, w);
        snap.append_color(&rgba(&self.color), &rect);
        let paint = &self.src.paint;
        if self.full {
            match paint.downcast_ref::<gdk::Texture>() {
                Some(tex) => snap.append_scaled_texture(tex, gsk::ScalingFilter::Trilinear, &rect),
                None => paint_at(snap, paint, inset, inset, pw),
            }
        } else if let Some((tex, (lx, ly, lw, lh))) = &self.logo {
            let a = pw * TILE_LOGO_SIZE / lw.max(*lh); // its logo at that share
            let (ox, oy) = (w / 2.0 - (lx + lw / 2.0) * a, h / 2.0 - (ly + lh / 2.0) * a);
            snap.append_scaled_texture(tex, gsk::ScalingFilter::Trilinear, &graphene::Rect::new(ox as f32, oy as f32, a as f32, a as f32));
        } else if let Some((cx0, cy0, cw, ch)) = self.white_tile.filter(|b| b.2 > 0.2 && b.3 > 0.2) {
            // only what's on the white tile, at WHITE_TILE_SIZE of the frame, on the plate's white
            let a = pw * WHITE_TILE_SIZE / cw.max(ch);
            let (ox, oy) = (w / 2.0 - (cx0 + cw / 2.0) * a, h / 2.0 - (cy0 + ch / 2.0) * a);
            let cut = cw.min(ch) * a * 0.01;
            let clip = gsk::RoundedRect::from_rect(graphene::Rect::new((ox + cx0 * a + cut) as f32, (oy + cy0 * a + cut) as f32, (cw * a - 2.0 * cut) as f32, (ch * a - 2.0 * cut) as f32), (cw.min(ch) * a * 0.22) as f32);
            snap.push_rounded_clip(&clip);
            paint_at(snap, paint, ox, oy, a);
            snap.pop();
        } else if let Some((tx, ty, tw, th)) = self.circle.filter(|b| b.2 > 0.3 && b.3 > 0.3) {
            // a disc: centred at CIRCLE_SIZE of the frame, on a plate of its edge's colour
            let a = pw * CIRCLE_SIZE / tw.max(th);
            paint_at(snap, paint, w / 2.0 - (tx + tw / 2.0) * a, h / 2.0 - (ty + th / 2.0) * a, a);
        } else if let Some((tx, ty, tw, th)) = self.tile.filter(|b| b.2 > 0.3 && b.3 > 0.3) {
            let a = pw * TILE_BLEED / tw.min(th); // the tile a little bigger than the frame
            paint_at(snap, paint, w / 2.0 - (tx + tw / 2.0) * a, h / 2.0 - (ty + th / 2.0) * a, a);
        } else {
            // the usual size, or the chosen one (up to the whole frame: cut to its shape)
            let a = match self.scale {
                None => w * PLATE_ARTWORK,
                Some(s) => pw * s,
            };
            paint_at(snap, paint, (w - a) / 2.0, (h - a) / 2.0, a);
        }
        snap.append_linear_gradient(&rect, &graphene::Point::new(0.0, inset as f32), &graphene::Point::new(0.0, (h - inset) as f32), &[gsk::ColorStop::new(0.0, rgba(PLATE_SHEEN.0)), gsk::ColorStop::new(1.0, rgba(PLATE_SHEEN.1))]);
        snap.pop();
        stroke_shape(snap, &self.shape, inset, inset, pw, ph, w);
    }
}

// -- what an icon's picture is (pixels; no display needed) ------------------------------------------
/// An RGBA picture's pixels (row by row, `n` bytes each).
#[derive(Clone)]
pub struct Pixels {
    pub w: usize,
    pub h: usize,
    pub n: usize,
    pub stride: usize,
    pub data: Vec<u8>,
}

impl Pixels {
    pub fn at(&self, x: usize, y: usize) -> [u8; 4] {
        let o = y * self.stride + x * self.n;
        let d = &self.data;
        [d[o], d[o + 1], d[o + 2], if self.n == 4 { d[o + 3] } else { 255 }]
    }

    fn opaque(&self) -> Vec<(usize, usize)> {
        let mut out = Vec::new();
        for y in 0..self.h {
            for x in 0..self.w {
                if self.at(x, y)[3] > 200 {
                    out.push((x, y));
                }
            }
        }
        out
    }

    pub fn from_pixbuf(pb: &gdk_pixbuf::Pixbuf) -> Self {
        let bytes = pb.read_pixel_bytes();
        Self { w: pb.width() as usize, h: pb.height() as usize, n: pb.n_channels() as usize, stride: pb.rowstride() as usize, data: bytes.to_vec() }
    }
}

fn bounds(pts: &[(usize, usize)]) -> (usize, usize, usize, usize) {
    let (mut x0, mut x1, mut y0, mut y1) = (usize::MAX, 0, usize::MAX, 0);
    for &(x, y) in pts {
        x0 = x0.min(x);
        x1 = x1.max(x);
        y0 = y0.min(y);
        y1 = y1.max(y);
    }
    (x0, x1, y0, y1)
}

fn fractions(px: &Pixels, pts: &[(usize, usize)]) -> Box4 {
    let (x0, x1, y0, y1) = bounds(pts);
    let (w, h) = (px.w as f64, px.h as f64);
    (x0 as f64 / w, y0 as f64 / h, (x1 + 1 - x0) as f64 / w, (y1 + 1 - y0) as f64 / h)
}

/// Where a picture's opaque tile is.
pub fn tile_box(px: &Pixels) -> Box4 {
    if px.n < 4 {
        return (0.0, 0.0, 1.0, 1.0);
    }
    let pts = px.opaque();
    if pts.is_empty() { (0.0, 0.0, 1.0, 1.0) } else { fractions(px, &pts) }
}

pub fn hex(tone: &str) -> [i32; 3] {
    let p = |i: usize| i32::from_str_radix(tone.get(i..i + 2).unwrap_or("ff"), 16).unwrap_or(255);
    [p(1), p(3), p(5)]
}

/// What sits on a tile of colour `tone` (what differs from it).
pub fn content_box(px: &Pixels, tone: &str) -> Option<Box4> {
    let t = hex(tone);
    let mut pts = Vec::new();
    for y in 0..px.h {
        for x in 0..px.w {
            let c = px.at(x, y);
            if (px.n < 4 || c[3] > 200) && (0..3).map(|i| (c[i] as i32 - t[i]).abs()).sum::<i32>() > 90 {
                pts.push((x, y));
            }
        }
    }
    (!pts.is_empty()).then(|| fractions(px, &pts))
}

/// What sits on an icon's own tile of colour `tone`: the tile's colour
/// (and what's outside it) made transparent, so only the logo is left.
/// (RGBA pixels, w, h, box) -- None if nothing is left.
pub fn key_out(px: &Pixels, tone: &str) -> Option<(Vec<u8>, Box4)> {
    if px.n != 4 {
        return None;
    }
    let t = hex(tone);
    let (w, h) = (px.w, px.h);
    let mut dst = vec![0u8; w * h * 4];
    let (mut xs, mut ys) = (Vec::new(), Vec::new());
    // only inside the tile, clear of its edge (a rim or shading there isn't the logo)
    let (bx, by, bw, bh) = tile_box(px);
    let m = 0.08 * bw.min(bh);
    let (x0, x1) = (((bx + m) * w as f64) as usize, ((bx + bw - m) * w as f64) as usize);
    let (y0, y1) = (((by + m) * h as f64) as usize, ((by + bh - m) * h as f64) as usize);
    for y in y0..y1.min(h) {
        for x in x0..x1.min(w) {
            let [r, g, b, a] = px.at(x, y);
            let d = (r as i32 - t[0]).abs() + (g as i32 - t[1]).abs() + (b as i32 - t[2]).abs();
            let k = ((d as f64 - 30.0) / 90.0).clamp(0.0, 1.0) * (a as f64 / 255.0); // soft key: smooth edges
            if k <= 0.0 {
                continue;
            }
            let q = (y * w + x) * 4;
            dst[q..q + 4].copy_from_slice(&[r, g, b, (k * 255.0) as u8]);
            if k > 0.5 {
                xs.push(x);
                ys.push(y);
            }
        }
    }
    if xs.is_empty() {
        return None;
    }
    let pts: Vec<(usize, usize)> = xs.into_iter().zip(ys).collect();
    Some((dst, fractions(px, &pts)))
}

fn keyed_content(path: &Path, tone: &str, size: i32) -> Option<(gdk::Texture, Box4)> {
    let px = pixels_at(path, size)?;
    let (dst, b) = key_out(&px, tone)?;
    let tex = gdk::MemoryTexture::new(px.w as i32, px.h as i32, gdk::MemoryFormat::R8g8b8a8, &glib::Bytes::from_owned(dst), px.w * 4);
    Some((tex.upcast(), b))
}

pub fn median_colour(cols: &[[u8; 4]]) -> String {
    let cols: Vec<&[u8; 4]> = cols.iter().filter(|c| c[3] > 200).collect();
    if cols.is_empty() {
        return PLATE_WHITE.into();
    }
    let med = |i: usize| {
        let mut v: Vec<u8> = cols.iter().map(|c| c[i]).collect();
        v.sort();
        v[v.len() / 2]
    };
    format!("#{:02x}{:02x}{:02x}", med(0), med(1), med(2))
}

/// The ring just inside a box's edge (past rounded corners).
fn ring_inside(px: &Pixels, x0: usize, x1: usize, y0: usize, y1: usize, m: usize) -> Vec<[u8; 4]> {
    let mut out = Vec::new();
    for x in (x0 + m)..=(x1.saturating_sub(m)) {
        for y in [y0 + 2, y1.saturating_sub(2)] {
            if x < px.w && y < px.h {
                out.push(px.at(x, y));
            }
        }
    }
    for y in (y0 + m)..=(y1.saturating_sub(m)) {
        for x in [x0 + 2, x1.saturating_sub(2)] {
            if x < px.w && y < px.h {
                out.push(px.at(x, y));
            }
        }
    }
    out
}

/// What an icon's picture is, from its transparency: ("square", colour) --
/// no transparency; ("tile", colour) -- a rounded square or squircle of its
/// own; ("circle", colour) -- a disc; None -- anything else (a logo, an
/// object: it stays on the plate). colour: the one just inside its edge.
pub fn shape_of(px: &Pixels) -> Option<(String, String)> {
    let (w, h) = (px.w, px.h);
    if px.n < 4 {
        let edge: Vec<[u8; 4]> = (0..w).flat_map(|x| [px.at(x, 1), px.at(x, h.saturating_sub(2))]).collect();
        return Some(("square".into(), median_colour(&edge)));
    }
    let opaque = px.opaque();
    if (opaque.len() as f64) < (w * h) as f64 * 0.3 {
        return None;
    }
    let (x0, x1, y0, y1) = bounds(&opaque);
    let (bw, bh) = (x1 - x0 + 1, y1 - y0 + 1);
    let ratio = bw as f64 / bh as f64;
    if !(0.88..=1.14).contains(&ratio) || (bw as f64) < w as f64 * 0.5 {
        return None; // not square-ish, or small on its canvas
    }
    let cover = opaque.len() as f64 / (bw * bh) as f64;
    let corners = [px.at(x0, y0)[3], px.at(x1, y0)[3], px.at(x0, y1)[3], px.at(x1, y1)[3]];
    if cover > 0.985 && corners.iter().all(|a| *a > 200) {
        let edge: Vec<[u8; 4]> = (x0..=x1).flat_map(|x| [px.at(x, y0 + 1), px.at(x, y1 - 1)]).collect();
        return Some(("square".into(), median_colour(&edge)));
    }
    if corners.iter().all(|a| *a < 120) {
        if cover >= 0.86 {
            // a rounded square / squircle
            let edge = ring_inside(px, x0, x1, y0, y1, (bw / 8).max(2));
            return Some(("tile".into(), median_colour(&edge)));
        }
        let (cx, cy, r) = ((x0 + x1) as f64 / 2.0, (y0 + y1) as f64 / 2.0, bw.min(bh) as f64 / 2.0);
        let inside: Vec<[u8; 4]> = (0..48)
            .filter_map(|i| {
                let a = std::f64::consts::TAU * i as f64 / 48.0;
                let (x, y) = ((cx + a.cos() * r * 0.9).round_ties_even(), (cy + a.sin() * r * 0.9).round_ties_even());
                (x >= 0.0 && y >= 0.0 && (x as usize) < w && (y as usize) < h).then(|| px.at(x as usize, y as usize))
            })
            .collect();
        if (0.72..=0.84).contains(&cover) && inside.iter().all(|c| c[3] > 200) {
            return Some(("circle".into(), median_colour(&inside))); // a disc: its whole rim opaque
        }
    }
    None
}

/// The colour of an icon whose outer edge is one solid colour (a tile of
/// its own, like Claude's); None otherwise (logos on transparency,
/// gradients, white tiles unless allowed).
pub fn edge_tone(px: &Pixels, allow_white: bool) -> Option<String> {
    let opaque = px.opaque();
    if (opaque.len() as f64) < (px.w * px.h) as f64 * 0.5 {
        return None; // a logo on transparency: white plate
    }
    let (x0, x1, y0, y1) = bounds(&opaque);
    let ring: Vec<[u8; 4]> = ring_inside(px, x0, x1, y0, y1, ((x1 - x0) / 8).max(2)).into_iter().filter(|c| c[3] > 200).collect();
    if ring.len() < 20 {
        return None;
    }
    let med: Vec<i32> = (0..3)
        .map(|i| {
            let mut v: Vec<u8> = ring.iter().map(|c| c[i]).collect();
            v.sort();
            v[v.len() / 2] as i32
        })
        .collect();
    let close = ring.iter().filter(|c| (0..3).map(|i| (c[i] as i32 - med[i]).abs()).sum::<i32>() < 48).count();
    if (close as f64) < ring.len() as f64 * 0.85 || (*med.iter().min().unwrap_or(&0) > 225 && !allow_white) {
        return None; // not one colour, or white already
    }
    Some(format!("#{:02x}{:02x}{:02x}", med[0], med[1], med[2]))
}

/// An image file's pixels at px (SVG through GdkPixbuf's librsvg loader).
pub fn pixels_at(path: &Path, px: i32) -> Option<Pixels> {
    let pb = gdk_pixbuf::Pixbuf::from_file_at_size(path, px, px).ok()?;
    Some(Pixels::from_pixbuf(&pb))
}

fn cached<K: std::hash::Hash + Eq + Clone, V: Clone>(pick: fn(&mut Analysed) -> &mut HashMap<K, V>, key: K, make: impl FnOnce() -> V) -> V {
    if let Some(v) = ANALYSED.with(|a| pick(&mut a.borrow_mut()).get(&key).cloned()) {
        return v;
    }
    let v = make();
    ANALYSED.with(|a| pick(&mut a.borrow_mut()).insert(key, v.clone()));
    v
}

fn solid_edge(path: &Path, allow_white: bool) -> Option<String> {
    cached(|a| &mut a.tones, (path.display().to_string(), allow_white), || pixels_at(path, 48).and_then(|px| edge_tone(&px, allow_white)))
}

fn tile_of(path: &Path) -> Option<Box4> {
    cached(|a| &mut a.boxes, path.display().to_string(), || pixels_at(path, 96).map(|px| tile_box(&px)))
}

fn shape_of_file(path: &Path) -> Option<(String, String)> {
    cached(|a| &mut a.shapes, path.display().to_string(), || pixels_at(path, 64).and_then(|px| shape_of(&px)))
}

// -- looking icons up --------------------------------------------------------------------------------
fn is_dark() -> bool {
    adw::StyleManager::default().is_dark()
}

/// `gicon` in the current appearance's version (the Trash, Launchpad).
pub fn for_appearance(gicon: &gio::Icon) -> gio::Icon {
    if let Some(t) = gicon.downcast_ref::<gio::ThemedIcon>() {
        if let Some(first) = t.names().first() {
            if let Some((_, light, dark)) = APPEARANCE_VARIANTS.iter().find(|v| v.0 == first.as_str()) {
                return gio::ThemedIcon::new(if is_dark() { dark } else { light }).upcast();
            }
        }
    }
    gicon.clone()
}

fn is_symbolic(path: &Path) -> bool {
    let base = path.file_name().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
    base.contains("-symbolic.") || base.contains(".symbolic.")
}

fn asks_symbolic(gicon: &gio::Icon) -> bool {
    gicon.downcast_ref::<gio::ThemedIcon>().map(|t| t.names()).is_some_and(|n| !n.is_empty() && n.iter().all(|x| x.ends_with("-symbolic")))
}

fn has_filter(path: &Path) -> bool {
    let key = path.display().to_string();
    cached(|a| &mut a.filtered, key, || std::fs::read(path).is_ok_and(|b| b.windows(7).any(|w| w == b"<filter")))
}

/// Full-colour SVG icons with filters (MacTahoe's soft shadows) are drawn
/// by librsvg (GdkPixbuf's loader): newer GTK SVG renderers draw those
/// filters as a stray translucent square at the top left.
fn rsvg_texture(path: &Path, px: i32) -> Option<gdk::Texture> {
    let key = (path.display().to_string(), px);
    if let Some(t) = RENDERED.with(|r| r.borrow().get(&key).cloned()) {
        return Some(t);
    }
    let pb = gdk_pixbuf::Pixbuf::from_file_at_scale(path, px, px, true).ok()?;
    #[allow(deprecated)]
    let tex = gdk::Texture::for_pixbuf(&pb);
    RENDERED.with(|r| {
        let mut r = r.borrow_mut();
        // (16 MB of pixels at most: a few dozen big icons)
        let bytes: usize = r.values().map(|t| (t.width() * t.height() * 4) as usize).sum();
        if bytes > 16 << 20 {
            r.clear();
        }
        r.insert(key, tex.clone());
    });
    Some(tex)
}

fn theme_for(display: &gdk::Display, gicon: &gio::Icon) -> gtk::IconTheme {
    let theme = gtk::IconTheme::for_display(display);
    match system() {
        Some(s) if !theme.has_gicon(gicon) && s.has_gicon(gicon) => s,
        _ => theme,
    }
}

fn theme_path(display: &gdk::Display, gicon: &gio::Icon, size: i32) -> Option<PathBuf> {
    let gicon = for_appearance(gicon);
    theme_for(display, &gicon).lookup_by_gicon(&gicon, size, 1, gtk::TextDirection::None, gtk::IconLookupFlags::empty()).file()?.path()
}

/// The picture for `gicon` at `size`: from Sonata's theme, else the
/// system's; a colour icon rather than a grey symbolic another theme has;
/// SVGs with filters drawn by librsvg.
pub fn resolve(display: &gdk::Display, scale: i32, gicon: &gio::Icon, size: i32) -> Option<Source> {
    let gicon = for_appearance(gicon);
    let theme = theme_for(display, &gicon);
    let mut icon = theme.lookup_by_gicon(&gicon, size, scale, gtk::TextDirection::None, gtk::IconLookupFlags::empty());
    let mut path = icon.file().and_then(|f| f.path());
    if path.as_deref().is_some_and(is_symbolic) && !asks_symbolic(&gicon) {
        // GTK falls back to "<name>-symbolic" inside the first theme that has
        // it (Sonata-MacTahoe draws some app logos as grey symbolics) before
        // looking in the next theme, where the app's colour icon is
        for other in [system(), Some(hicolor())].into_iter().flatten() {
            if other == theme || !other.has_gicon(&gicon) {
                continue;
            }
            let alt = other.lookup_by_gicon(&gicon, size, scale, gtk::TextDirection::None, gtk::IconLookupFlags::empty());
            if let Some(p) = alt.file().and_then(|f| f.path()).filter(|p| !is_symbolic(p)) {
                icon = alt;
                path = Some(p);
                break;
            }
        }
    }
    if let Some(p) = path.as_deref().filter(|p| p.extension().is_some_and(|e| e == "svg") && !is_symbolic(p) && has_filter(p)) {
        if let Some(tex) = rsvg_texture(p, size * scale) {
            return Some(Source { paint: tex.upcast(), path: None });
        }
    }
    Some(Source { paint: icon.upcast(), path })
}

/// Show `gicon` from Sonata's theme, or from the system theme if only that
/// one has it.
pub fn set_image(image: &gtk::Image, gicon: &gio::Icon) {
    let gicon = for_appearance(gicon);
    let size = if image.pixel_size() > 0 { image.pixel_size() } else { 48 };
    let display = image.display();
    let found = resolve(&display, image.scale_factor(), &gicon, size);
    if let Some(f) = &found {
        if f.paint.is::<gdk::Texture>() {
            image.set_paintable(Some(&f.paint)); // drawn by librsvg
            return;
        }
    }
    let theme = gtk::IconTheme::for_display(&display);
    match (system(), found) {
        (Some(s), Some(f)) if !theme.has_gicon(&gicon) && s.has_gicon(&gicon) => image.set_paintable(Some(&f.paint)),
        _ => image.set_from_gicon(&gicon), // follows icon theme changes
    }
}

/// An icon's picture (drag icons etc.), with the same fallback as set_image.
pub fn paintable(widget: &impl IsA<gtk::Widget>, gicon: &gio::Icon, size: i32) -> Option<gdk::Paintable> {
    resolve(&widget.display(), widget.scale_factor(), gicon, size).map(|s| s.paint)
}

/// File of the system's logo (os-release LOGO=, distributor-logo-<ID>,
/// the ID_LIKE family...) from the icon themes or /usr/share/pixmaps.
pub fn distro_logo(size: i32) -> Option<PathBuf> {
    let osr: Vec<(String, String)> = std::fs::read_to_string("/etc/os-release").unwrap_or_default().lines().filter_map(|l| l.split_once('=').map(|(k, v)| (k.to_string(), v.trim().trim_matches('"').to_string()))).collect();
    let get = |k: &str| osr.iter().find(|(x, _)| x == k).map(|(_, v)| v.clone()).unwrap_or_default();
    let mut ids = vec![get("ID")];
    ids.extend(get("ID_LIKE").split_whitespace().map(String::from));
    let mut names = vec![get("LOGO")];
    for i in ids.iter().filter(|i| !i.is_empty()) {
        names.extend([format!("distributor-logo-{i}"), format!("{i}-logo"), i.clone()]);
    }
    let themes: Vec<gtk::IconTheme> = [gdk::Display::default().map(|d| gtk::IconTheme::for_display(&d)), system()].into_iter().flatten().collect();
    for n in names.iter().filter(|n| !n.is_empty()) {
        for t in &themes {
            if t.has_icon(n) {
                if let Some(p) = t.lookup_icon(n, &[], size, 1, gtk::TextDirection::None, gtk::IconLookupFlags::empty()).file().and_then(|f| f.path()) {
                    return Some(p);
                }
            }
        }
        for ext in ["svg", "png"] {
            let p = PathBuf::from(format!("/usr/share/pixmaps/{n}.{ext}"));
            if p.exists() {
                return Some(p);
            }
        }
    }
    None
}

/// `image` shows the system's logo at its pixel size, in its own colours;
/// the Sonata logo when the distro has none.
pub fn set_logo(image: &gtk::Image) {
    let px = if image.pixel_size() > 0 { image.pixel_size() } else { 96 };
    let tex = distro_logo(px).and_then(|p| if p.extension().is_some_and(|e| e == "svg") { rsvg_texture(&p, px * 2) } else { gdk::Texture::from_filename(&p).ok() });
    match tex {
        Some(t) => image.set_paintable(Some(&t)),
        None => image.set_icon_name(Some("sonata-logo-symbolic")),
    }
}

#[cfg(test)]
mod tests;
