//! The Dock (macOS Big Sur), shell/dock.py: pinned apps, running/recent
//! apps, a divider, stacks and the Trash -- at the bottom, left or right
//! screen edge.
//!
//! Metrics follow Big Sur at the default 48 px icon size: rounded plate
//! floating a few px from the screen edge, running dot between the icon and
//! the edge, name label beside the hovered icon. The plate is translucent
//! "glass": the compositor blurs what is behind it; with glass off it is
//! nearly opaque. It is painted by the plate's snapshot at a fixed
//! thickness against the edge, so magnified icons grow away from it.
//!
//! Layout along the Dock:
//!   [pinned apps] | [recent + running unpinned apps] || [stacks] [Trash]
//! (the single bar only when that middle section is non-empty.)
//!
//! Running apps come from toplevels.rs (Wayfire's IPC). Menus:
//! dock_menu.rs. File drops: dock_drop.rs. Stacks: dock_stack.rs.
//! Auto-hide lives in DockWindow.
//!
//! Minimize targets (each window flies into its icon): wlr.rs, aimed by
//! update_rectangles / aim_at.

use crate::toplevels::{Manager, Toplevel};
use crate::{dock_drop, dock_folder, dock_menu, dock_preview, dock_stack, poof, uninstall_ui};
use adw::prelude::*;
use gtk::{gdk, gio, glib, graphene, gsk, pango};
use serde_json::{json, Map, Value};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::rc::{Rc, Weak};
use std::time::{Duration, Instant};

pub type Cfg = Rc<RefCell<Map<String, Value>>>;

pub fn defaults() -> Value {
    json!({"pinned": null, "icon_size": 48, "edge_gap": 4, "window_gap": 6, "glass": true,
           "indicators": true, "bounce": true, "minimize_effect": "genie", "click_minimizes": true,
           "magnification": false, "magnified_size": 80, "position": "bottom",
           "autohide": false, "autohide_delay_ms": 300, "show_recents": true,
           "recent": [], "stacks": null, "all_displays": false, "folders": {},
           "quit_on_close": true})
}

pub const EDGES: [&str; 3] = ["left", "bottom", "right"];
pub const MIN_SIZE: i64 = 16;
pub const MAX_SIZE: i64 = 128;
/// pins must still fit at this size: more can't be kept in the Dock
pub const PIN_MIN_SIZE: f64 = 36.0;
/// px kept free at both ends of the screen edge
pub const SCREEN_MARGIN: f64 = 16.0;
pub const MAX_RECENTS: usize = 3;
/// running dots: one per window, up to this many (a + looked off -- Vini)
pub const MAX_DOTS: usize = 3;
/// Always in the Dock: Launchpad / the Apps Menu, which reaches every app.
/// Files can leave it (Vini): it's in the Apps Menu, pinned again from there
pub const PERMANENT: [&str; 1] = ["sonata2-launchpad"];
pub const LAUNCHPAD_KEY: &str = PERMANENT[0];
pub const FILES_KEY: &str = "io.github.vinioliveiras.sonata2.files";
/// shell tools that open instantly and have no app window: no launch
/// bounce, and never "stuck" (Vini: the Screenshot icon bounced on and on)
pub const NO_BOUNCE: [&str; 2] = ["sonata2-launchpad", "sonata2-screenshot"];
pub const BOUNCE_MS: u32 = 620;
/// clicked again this long after a launch that never showed a window: stuck
pub const STUCK_S: f64 = 4.0;
/// ... but not later than this: a slow app long since started isn't stopped
pub const STUCK_MAX_S: f64 = 90.0;
pub const RELAUNCH_MS: u64 = 900;
/// a launch bounces until the app's first window shows up, 10 bounces at
/// most (Vini: it went on ~30 s when no window came)
pub const LAUNCH_MAX_BOUNCES: u32 = 10;
pub const LAUNCH_MAX_MS: u32 = LAUNCH_MAX_BOUNCES * BOUNCE_MS;
pub const CLOSE_UP_MS: u32 = 260;
pub const OPEN_UP_MS: u32 = 260;
pub const UNGROUP_MS: u32 = 340;
pub const UNGROUP_STAGGER_MS: u64 = 45;
pub const SETTLE_MS: u32 = 200;
/// held this long over another app's middle: drop makes a folder
pub const FOLDER_HOLD_MS: u64 = 350;
/// the middle: within this part of a cell from the icon's centre
pub const FOLDER_ZONE: f64 = 0.3;
/// magnification reaches this many icons away
pub const MAG_RADIUS: f64 = 3.0;
pub const MAG_IN_MS: u32 = 120;
pub const MAG_OUT_MS: u32 = 250;
pub const HIDE_MS: u32 = 250;
/// px strip at the screen edge that reveals a hidden Dock (layer.EDGE_TRIGGER)
pub const TRIGGER: f64 = 2.0;
pub const GLIDE_MS: u32 = 260;

// Plate padding and running dot, in px. Unlike macOS (where it sits low),
// the dot is centred between the icon's visible artwork and the plate edge.
// Icons have a transparent margin inside their box (1/12 of the size), so
// the gap between icon and dot is shortened by that margin.
pub const PAD_TOP: i64 = 5;
pub const PAD_SIDE: f64 = 4.0;
pub const TILE_PAD: i64 = 2;
pub const DOT: i64 = 4;
pub const DOT_GAP: i64 = 4;
pub const SHADOW: i64 = 12;
/// 1 px line + 5 px each side (also the drag handle)
pub const DIVIDER_W: i64 = 11;
/// measured: 4 px at 48 px
pub const ART_INSET: f64 = 1.0 / 12.0;
/// a white number down to this contrast (macOS: white on green); else black
pub const BADGE_WHITE_MIN: f64 = 1.9;
pub const BADGE_POP_MS: u32 = 320;
/// an app's entry still missing this long later: uninstalled, not updating
pub const GONE_CONFIRM_MS: u64 = 20000;
pub const PACKAGE_LOCKS: [&str; 1] = ["/var/lib/pacman/db.lck"];
/// a window moved to this display: Wayfire takes it, then it comes back
pub const BRING_MS: u64 = 120;
/// the apps of the last session, once the session has settled
pub const REOPEN_AFTER_MS: u64 = 3500;
pub const RADIUS_DEFAULT: f64 = 18.0;

// -- pure logic ------------------------------------------------------------------------------

/// The pinned keys in the tiles' order; keys without a tile stay where
/// they were (between the same neighbours).
pub fn merge_order(pinned: &[String], tile_keys: &[String]) -> Vec<String> {
    let shown: Vec<&String> = tile_keys.iter().filter(|k| pinned.contains(k)).collect();
    let mut it = shown.into_iter();
    let mut out: Vec<String> = Vec::new();
    for k in pinned {
        if tile_keys.contains(k) {
            if let Some(n) = it.next() {
                out.push(n.clone());
            }
        } else {
            out.push(k.clone());
        }
    }
    for k in it {
        if !out.contains(k) {
            out.push(k.clone());
        }
    }
    let mut seen = HashSet::new();
    out.into_iter().filter(|k| seen.insert(k.clone())).collect()
}

/// The Dock's corner radius (Settings > Appearance, appearance.json
/// "radius": {"dock": n}, 0..32; tokens.user_radii).
/// Read again only when the file changes (tokens.user_radii keeps it by
/// mtime): it is asked on every paint and twice a frame while magnifying.
pub fn dock_radius() -> f64 {
    thread_local! {
        static CACHE: RefCell<Option<(std::path::PathBuf, Option<std::time::SystemTime>, f64)>> = const { RefCell::new(None) };
    }
    let p = sonata_config::path("appearance");
    let mtime = std::fs::metadata(&p).and_then(|m| m.modified()).ok();
    CACHE.with(|c| {
        if let Some((cp, cm, r)) = c.borrow().as_ref() {
            if *cp == p && *cm == mtime {
                return *r;
            }
        }
        let r = radius_of(&sonata_config::stored("appearance"));
        c.replace(Some((p, mtime, r)));
        r
    })
}

/// appearance.json's Dock radius as tokens.user_radii reads it (rounded
/// half to even, kept in its range).
fn radius_of(appearance: &Map<String, Value>) -> f64 {
    sonata_ui::tokens::user_radii(appearance).iter().find(|(k, _)| *k == "dock").map(|(_, r)| *r as f64).unwrap_or(RADIUS_DEFAULT)
}

/// tokens.corner_extra: half the extra radius, never closer than designed.
pub fn corner_extra(radius: f64, designed: f64) -> f64 {
    ((radius - designed) / 2.0).max(0.0)
}

/// Room before the first icon and after the last: more with a rounder
/// plate, so the end icons keep their distance from its corners.
pub fn pad_side() -> f64 {
    PAD_SIDE + corner_extra(dock_radius(), RADIUS_DEFAULT)
}

/// (gap icon->dot, gap dot->edge), in px, for visual centring.
pub fn dot_gaps(icon_size: i64) -> (i64, i64) {
    ((DOT_GAP - (icon_size as f64 * ART_INSET).round() as i64).max(0), DOT_GAP)
}

pub fn dot_row(icon_size: i64) -> i64 {
    let (inner, outer) = dot_gaps(icon_size);
    inner + DOT + outer
}

/// Plate thickness (perpendicular to the edge).
pub fn plate_height(icon_size: i64) -> i64 {
    PAD_TOP + icon_size + dot_row(icon_size)
}

fn geti(cfg: &Map<String, Value>, k: &str) -> i64 {
    cfg.get(k).and_then(|v| v.as_i64().or_else(|| v.as_f64().map(|f| f as i64))).unwrap_or_else(|| defaults()[k].as_i64().unwrap_or(0))
}

fn getb(cfg: &Map<String, Value>, k: &str) -> bool {
    match cfg.get(k) {
        Some(Value::Bool(b)) => *b,
        Some(Value::Null) | None => defaults()[k].as_bool().unwrap_or(false),
        Some(v) => truthy(v),
    }
}

/// Python's truth of a JSON value (config.py keeps whatever was written).
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn gets(cfg: &Map<String, Value>, k: &str) -> String {
    cfg.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

fn list(cfg: &Map<String, Value>, k: &str) -> Vec<String> {
    cfg.get(k).and_then(Value::as_array).map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default()
}

/// How far the resting Dock reaches in from its screen edge (0 when it
/// hides itself): full-screen shell surfaces keep their content clear of it.
pub fn reserved(cfg: &Map<String, Value>) -> i64 {
    if getb(cfg, "autohide") {
        return 0;
    }
    let s = geti(cfg, "icon_size");
    geti(cfg, "edge_gap") + 2 * PAD_TOP + s + dot_row(s)
}

pub fn max_icon(cfg: &Map<String, Value>) -> i64 {
    let s = geti(cfg, "icon_size");
    if getb(cfg, "magnification") {
        s.max(geti(cfg, "magnified_size"))
    } else {
        s
    }
}

/// The icon size that fits `n` icons along `span` px (macOS: a full Dock
/// shrinks its icons, and grows back when apps close).
pub fn fit_size(span: f64, n: usize, user_size: i64, two_lines: bool, pad: f64) -> i64 {
    if span <= 0.0 || n == 0 {
        return user_size;
    }
    let lines = DIVIDER_W as f64 * if two_lines { 2.0 } else { 1.0 };
    let fit = (span - 2.0 * pad.round() - lines) / n as f64 - 2.0 * TILE_PAD as f64;
    (MIN_SIZE as f64).max((user_size as f64).min(fit)) as i64
}

/// How many icons of `size` fit along the edge.
pub fn cells_room(span: f64, size: f64, two_lines: bool, pad: f64) -> f64 {
    let lines = DIVIDER_W as f64 * if two_lines { 2.0 } else { 1.0 };
    (span - 2.0 * pad.round() - lines) / (size + 2.0 * TILE_PAD as f64)
}

/// The macOS wave: each tile's size with the pointer at `pos` along the
/// Dock (cosine falloff over MAG_RADIUS icons). Distances use the
/// unmagnified layout, so the wave doesn't feed back on itself. `lines_before`:
/// for each tile, the divider widths laid before it.
pub fn wave(base: f64, magnified: f64, strength: f64, pos: f64, span: f64, lines_before: &[f64], pad: f64) -> Vec<f64> {
    let n = lines_before.len();
    let extra = (magnified - base).max(0.0) * strength;
    let cell = base + 2.0 * TILE_PAD as f64;
    let lines: f64 = lines_before.iter().sum();
    let length = 2.0 * pad.round() + n as f64 * cell + lines;
    let mut at = (span - length) / 2.0 + pad.round();
    let radius = MAG_RADIUS * cell;
    let mut out = Vec::with_capacity(n);
    for l in lines_before {
        at += l;
        let d = (at + cell / 2.0 - pos).abs();
        let f = if d < radius { (std::f64::consts::FRAC_PI_2 * d / radius).cos().powi(2) } else { 0.0 };
        out.push(base + extra * f);
        at += cell;
    }
    out
}

/// "99+" counts as 100 (the badge pops only when the count grows).
pub fn badge_n(text: &str) -> i64 {
    if text == "99+" {
        100
    } else {
        text.parse().unwrap_or(0)
    }
}

/// Clicked again while the last launch never showed a window: stopped and
/// opened afresh (between STUCK_S and STUCK_MAX_S after it).
pub fn stuck(elapsed_s: f64, has_windows: bool) -> bool {
    !has_windows && (STUCK_S..=STUCK_MAX_S).contains(&elapsed_s)
}

/// load_config's own part: pins and stacks filled the first time,
/// Launchpad back in its place. True when something changed (saved).
pub fn fix_config(cfg: &mut Map<String, Value>, default_pins: impl FnOnce() -> Vec<String>, default_stacks: impl FnOnce() -> Value, known: impl Fn(&str) -> bool) -> bool {
    let mut changed = false;
    if list(cfg, "pinned").is_empty() {
        cfg.insert("pinned".into(), json!(default_pins()));
        changed = true;
    }
    if cfg.get("stacks").map_or(true, Value::is_null) {
        cfg.insert("stacks".into(), default_stacks());
        changed = true;
    }
    let mut pins = list(cfg, "pinned");
    let mut missing = false;
    for (i, k) in PERMANENT.iter().enumerate() {
        // Launchpad first (Files, taken out, stays out)
        if !pins.iter().any(|p| p == k) && known(k) {
            pins.insert(i.min(pins.len()), k.to_string());
            missing = true;
        }
    }
    if missing {
        cfg.insert("pinned".into(), json!(pins));
    }
    changed || missing
}

/// Show Desktop's plan (Sonata's own, not Wayfire's: there, bringing back
/// any window brought back all of them). tops: (id, minimized) now;
/// hidden: what the last Super+D hid. (minimize, activate, new hidden).
pub fn show_desktop_plan(tops: &[(i64, bool)], hidden: &[i64]) -> (Vec<i64>, Vec<i64>, Vec<i64>) {
    let shown: Vec<i64> = tops.iter().filter(|t| !t.1).map(|t| t.0).collect();
    let still: Vec<i64> = hidden.iter().copied().filter(|h| tops.iter().any(|t| t.0 == *h && t.1)).collect();
    if !shown.is_empty() {
        let mut keep = still;
        keep.extend(&shown);
        return (shown, Vec::new(), keep);
    }
    // the one that was in front comes back last (in front)
    (Vec::new(), still.into_iter().rev().collect(), Vec::new())
}

/// (app_id, title) of the windows on display `mine`, and of all windows
/// Wayfire knows; None when it can't tell.
pub type Placed = (HashSet<(String, String)>, Option<HashSet<(String, String)>>);

pub fn windows_here(views: Option<&Value>, mine: &str) -> Placed {
    let Some(views) = views.and_then(Value::as_array).filter(|_| !mine.is_empty()) else { return (HashSet::new(), None) };
    let views: Vec<&Value> = views.iter().filter(|v| matches!(v.get("type").and_then(Value::as_str), None | Some("toplevel")) && v.get("output-name").and_then(Value::as_str).is_some_and(|o| !o.is_empty())).collect();
    let pair = |v: &Value| (v.get("app-id").and_then(Value::as_str).unwrap_or("").to_string(), v.get("title").and_then(Value::as_str).unwrap_or("").to_string());
    (views.iter().filter(|v| v["output-name"] == mine).map(|v| pair(v)).collect(), Some(views.iter().map(|v| pair(v)).collect()))
}

pub fn package_manager_busy() -> bool {
    PACKAGE_LOCKS.iter().any(|p| std::path::Path::new(p).exists())
}

// -- the pieces shared with Launchpad ----------------------------------------------------------

/// Open an app as Sonata does (apps.DesktopAppInfo.launch): Sonata's
/// whole launch path (sonata_ui::apps::launch -- the app lock, a new
/// Flatpak's review, Settings > Apps' limits, Quit on Close called off, the
/// GPU's environment and arguments, its own scope, no GTK_THEME). It once
/// did only the lock and the scope here: apps opened from the Dock and
/// Launchpad ignored "Use Discrete Graphics" and Steam's arguments.
pub fn launch_info(info: &gio::DesktopAppInfo, files: &[gio::File], ctx: Option<gio::AppLaunchContext>) -> Result<(), glib::Error> {
    sonata_ui::apps::launch(info, files, ctx.as_ref())
}

pub fn launch_context(w: &impl IsA<gtk::Widget>) -> gio::AppLaunchContext {
    w.as_ref().display().app_launch_context().upcast()
}

fn rgba(spec: &str) -> gdk::RGBA {
    gdk::RGBA::parse(spec).unwrap_or(gdk::RGBA::BLACK)
}

fn rect(x: f64, y: f64, w: f64, h: f64) -> graphene::Rect {
    graphene::Rect::new(x as f32, y as f32, w as f32, h as f32)
}

fn rounded(x: f64, y: f64, w: f64, h: f64, r: f64) -> gsk::RoundedRect {
    gsk::RoundedRect::from_rect(rect(x, y, w, h), r as f32)
}

/// A shadow token ("dx dy blur colour") as (dx, dy, blur, colour)
/// (theme.shadow).
pub fn shadow(token: &str) -> (f64, f64, f64, gdk::RGBA) {
    let v = sonata_ui::values().get(token).cloned().unwrap_or_default();
    let mut parts = v.trim().splitn(4, char::is_whitespace);
    let mut num = || parts.next().map(|p| p.trim_end_matches("px").parse::<f64>().unwrap_or(0.0)).unwrap_or(0.0);
    let (dx, dy, blur) = (num(), num(), num());
    let col = v.trim().splitn(4, char::is_whitespace).nth(3).unwrap_or("rgba(0,0,0,0.2)").to_string();
    (dx, dy, blur, rgba(&col))
}

/// (fill, ink) of the Dock's count badges: the accent the user picked;
/// its number white or black, whichever reads better.
pub fn badge_colors() -> (gdk::RGBA, gdk::RGBA) {
    let fill = sonata_ui::rgba("accent_ink");
    let hexed = format!("#{:02x}{:02x}{:02x}", (fill.red() * 255.0).round() as u8, (fill.green() * 255.0).round() as u8, (fill.blue() * 255.0).round() as u8);
    let ink = if sonata_ui::tokens::contrast(&hexed, "#ffffff") >= BADGE_WHITE_MIN { "#ffffff" } else { "#000000" };
    (fill, rgba(ink))
}

// -- open menus (ui.menu.OPEN / on_closed): they keep an auto-hiding Dock up ----------------------

thread_local! {
    static OPEN_POPS: RefCell<Vec<glib::WeakRef<gtk::Popover>>> = const { RefCell::new(Vec::new()) };
    static ON_CLOSED: RefCell<Vec<(u64, Rc<dyn Fn()>)>> = const { RefCell::new(Vec::new()) };
    static DOCKS: RefCell<Vec<Weak<Dock>>> = const { RefCell::new(Vec::new()) };
    static DESK_HIDDEN: RefCell<Vec<i64>> = const { RefCell::new(Vec::new()) };
    static SEEN: RefCell<(Option<i64>, HashMap<String, i64>)> = RefCell::new((None, HashMap::new()));
    static REOPENED: Cell<bool> = const { Cell::new(false) };
}

/// A menu or panel of the Dock is open: an auto-hiding Dock stays. A kept
/// panel opens again and again: it counts once (ui.menu.OPEN is a set).
pub fn menu_opened(pop: &impl IsA<gtk::Popover>) {
    let pop: gtk::Popover = pop.as_ref().clone();
    let known = OPEN_POPS.with(|o| {
        let mut o = o.borrow_mut();
        o.retain(|w| w.upgrade().is_some());
        let had = o.iter().any(|w| w.upgrade().as_ref() == Some(&pop));
        if !had {
            o.push(pop.downgrade());
        }
        had
    });
    if known {
        return;
    }
    // SAFETY: the mark is only ever set and read as a bool, here
    if unsafe { pop.data::<bool>("sonata-dock-open") }.is_some() {
        return;
    }
    unsafe { pop.set_data("sonata-dock-open", true) };
    pop.connect_closed(|p| {
        OPEN_POPS.with(|o| o.borrow_mut().retain(|w| w.upgrade().is_some_and(|x| &x != p)));
        let cbs: Vec<_> = ON_CLOSED.with(|c| c.borrow().iter().map(|(_, f)| f.clone()).collect());
        for f in cbs {
            f();
        }
    });
}

pub fn menus_open() -> bool {
    OPEN_POPS.with(|o| o.borrow().iter().any(|w| w.upgrade().is_some_and(|p| p.get_visible())))
}

fn on_menu_closed(id: u64, f: Rc<dyn Fn()>) {
    ON_CLOSED.with(|c| c.borrow_mut().push((id, f)));
}

fn off_menu_closed(id: u64) {
    ON_CLOSED.with(|c| c.borrow_mut().retain(|(i, _)| *i != id));
}

fn docks() -> Vec<Rc<Dock>> {
    DOCKS.with(|d| d.borrow().iter().filter_map(Weak::upgrade).collect())
}

/// Super+D (`sonata2 show-desktop`): every window goes; a click brings
/// back only that app's; Super+D again brings back the ones it hid, if
/// nothing is showing. "hid" / "restored" / "".
pub fn toggle_show_desktop(manager: &Manager) -> &'static str {
    let tops = manager.toplevels();
    let pairs: Vec<(i64, bool)> = tops.iter().map(|t| (t.id, t.minimized)).collect();
    let hidden = DESK_HIDDEN.with(|h| h.borrow().clone());
    let (minimize, activate, keep) = show_desktop_plan(&pairs, &hidden);
    DESK_HIDDEN.with(|h| h.replace(keep));
    if !minimize.is_empty() {
        for d in docks() {
            d.update_rectangles(); // each window flies to the icon on its display
        }
    }
    for id in &minimize {
        if let Some(t) = manager.get(*id) {
            manager.minimize(&t);
        }
    }
    for id in &activate {
        if let Some(t) = manager.get(*id) {
            manager.activate(&t);
        }
    }
    if !minimize.is_empty() {
        "hid"
    } else if !activate.is_empty() {
        "restored"
    } else {
        ""
    }
}

// -- glide: items that change place slide there (ui/transition.py) ------------------------------

pub type Glide = Vec<(gtk::Widget, f64, f64, Option<gtk::Widget>)>;

/// Where each widget is drawn now (layout + running glide), in the plate's
/// coordinates. Call before re-ordering.
pub fn glide_record(plate: &Plate, widgets: &[gtk::Widget]) -> Glide {
    let g = plate.imp().glide.borrow();
    widgets
        .iter()
        .filter(|w| w.is_mapped())
        .filter_map(|w| {
            let p = w.compute_point(plate, &graphene::Point::new(0.0, 0.0))?;
            let (dx, dy) = g.get(w).copied().unwrap_or((0.0, 0.0));
            Some((w.clone(), p.x() as f64 + dx, p.y() as f64 + dy, w.parent()))
        })
        .collect()
}

/// After re-ordering: every recorded widget that moved (and kept its
/// parent) slides from its old place to the new one, in the layout phase
/// of the next frame, so the new place is never shown first.
pub fn glide_play(plate: &Plate, before: Glide, ms: u32) {
    let Some(clock) = plate.frame_clock() else { return };
    if before.is_empty() {
        return;
    }
    let id: Rc<RefCell<Option<glib::SignalHandlerId>>> = Rc::default();
    let plate2 = plate.clone();
    let id2 = id.clone();
    let handler = clock.connect_layout(move |clock| {
        if let Some(h) = id2.borrow_mut().take() {
            clock.disconnect(h);
        }
        for (w, ox, oy, parent) in &before {
            if w.parent() != *parent || !w.is_mapped() {
                continue;
            }
            let Some(p) = w.compute_point(&plate2, &graphene::Point::new(0.0, 0.0)) else { continue };
            let (dx, dy) = (ox - p.x() as f64, oy - p.y() as f64);
            if dx.abs() < 0.5 && dy.abs() < 0.5 {
                continue;
            }
            glide_start(&plate2, w, dx, dy, ms);
        }
    });
    id.replace(Some(handler));
    plate.queue_allocate();
}

fn glide_start(plate: &Plate, w: &gtk::Widget, dx: f64, dy: f64, ms: u32) {
    if let Some(a) = plate.imp().glide_anims.borrow_mut().remove(w) {
        a.pause();
    }
    plate.imp().glide.borrow_mut().insert(w.clone(), (dx, dy));
    let (p, w2) = (plate.downgrade(), w.clone());
    let anim = adw::TimedAnimation::new(w, 1.0, 0.0, sonata_ui::tokens::ms(ms as f64), adw::CallbackAnimationTarget::new(move |v| {
        if let Some(p) = p.upgrade() {
            if v <= 0.0 {
                p.imp().glide.borrow_mut().remove(&w2);
            } else {
                p.imp().glide.borrow_mut().insert(w2.clone(), (dx * v, dy * v));
            }
            p.queue_draw();
        }
    }));
    anim.set_easing(adw::Easing::EaseOutCubic);
    plate.imp().glide_anims.borrow_mut().insert(w.clone(), anim.clone());
    anim.play();
}

// -- widgets -----------------------------------------------------------------------------------

mod imp {
    use super::*;
    use gtk::subclass::prelude::*;

    #[derive(Default)]
    pub struct Plate {
        pub paint: RefCell<Option<Rc<dyn Fn(&super::Plate, &gtk::Snapshot)>>>,
        pub allocated: RefCell<Option<Rc<dyn Fn()>>>,
        pub glide: RefCell<HashMap<gtk::Widget, (f64, f64)>>,
        pub glide_anims: RefCell<HashMap<gtk::Widget, adw::TimedAnimation>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Plate {
        const NAME: &'static str = "SonataDockPlate";
        type Type = super::Plate;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for Plate {}

    impl WidgetImpl for Plate {
        fn snapshot(&self, snapshot: &gtk::Snapshot) {
            let obj = self.obj();
            let paint = self.paint.borrow().clone();
            if let Some(p) = paint {
                p(&obj, snapshot);
            }
            // children, each shifted by its glide offset
            let mut c = obj.first_child();
            while let Some(child) = c {
                let off = self.glide.borrow().get(&child).copied();
                match off {
                    Some((dx, dy)) if dx != 0.0 || dy != 0.0 => {
                        snapshot.save();
                        snapshot.translate(&graphene::Point::new(dx as f32, dy as f32));
                        obj.snapshot_child(&child, snapshot);
                        snapshot.restore();
                    }
                    _ => obj.snapshot_child(&child, snapshot),
                }
                c = child.next_sibling();
            }
        }

        fn size_allocate(&self, width: i32, height: i32, baseline: i32) {
            self.parent_size_allocate(width, height, baseline);
            let f = self.allocated.borrow().clone();
            if let Some(f) = f {
                f();
            }
        }
    }

    impl BoxImpl for Plate {}

    pub struct DockIcon {
        pub gicon: RefCell<Option<gio::Icon>>,
        pub size: Cell<i32>,
        pub base: Cell<i32>,
        pub big: Cell<i32>,
        pub paint: RefCell<HashMap<(i32, bool), gdk::Paintable>>,
        pub badge: RefCell<String>,
        pub badge_k: Cell<f64>,
        pub badge_anim: RefCell<Option<adw::TimedAnimation>>,
        pub locked: Cell<bool>,
        pub sandboxed: Cell<bool>,
        pub pop: Cell<f64>,
    }

    impl Default for DockIcon {
        fn default() -> Self {
            DockIcon {
                gicon: RefCell::default(),
                size: Cell::new(48),
                base: Cell::new(48),
                big: Cell::new(48),
                paint: RefCell::default(),
                badge: RefCell::default(),
                badge_k: Cell::new(1.0),
                badge_anim: RefCell::default(),
                locked: Cell::new(false),
                sandboxed: Cell::new(false),
                pop: Cell::new(1.0),
            }
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for DockIcon {
        const NAME: &'static str = "SonataDockIcon";
        type Type = super::DockIcon;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for DockIcon {}

    impl WidgetImpl for DockIcon {
        fn measure(&self, _o: gtk::Orientation, _for: i32) -> (i32, i32, i32, i32) {
            let s = self.size.get();
            (s, s, -1, -1)
        }

        fn snapshot(&self, snap: &gtk::Snapshot) {
            self.obj().draw(snap);
        }
    }
}

glib::wrapper! {
    /// The plate with all tiles (a Box painting the glass under them).
    pub struct Plate(ObjectSubclass<imp::Plate>) @extends gtk::Box, gtk::Widget, @implements gtk::Orientable, gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

glib::wrapper! {
    /// An icon drawn at any size from one paintable. Magnification changes
    /// the size every frame; resampling a cached texture on the GPU is far
    /// cheaper than re-rendering SVGs at each new size.
    pub struct DockIcon(ObjectSubclass<imp::DockIcon>) @extends gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

use gtk::subclass::prelude::ObjectSubclassIsExt;

impl DockIcon {
    pub fn new(gicon: Option<&gio::Icon>, size: i32) -> Self {
        let w: Self = glib::Object::builder().property("css-classes", vec!["dock-icon".to_string()]).build();
        w.imp().gicon.replace(gicon.cloned());
        w.imp().size.set(size);
        w.imp().base.set(size);
        w.imp().big.set(size);
        w
    }

    pub fn set_gicon(&self, gicon: Option<&gio::Icon>) {
        self.imp().gicon.replace(gicon.cloned());
        self.imp().paint.borrow_mut().clear();
        self.queue_draw();
    }

    pub fn gicon(&self) -> Option<gio::Icon> {
        self.imp().gicon.borrow().clone()
    }

    pub fn set_size(&self, size: f64) {
        let size = size.round() as i32;
        if size != self.imp().size.get() {
            self.imp().size.set(size);
            self.queue_resize();
        }
    }

    /// The Dock's icon size and its magnified maximum: a magnified icon
    /// draws one big paintable, scaled (crisp at rest).
    pub fn set_scale_hint(&self, base: i32, big: i32) {
        self.imp().base.set(base);
        self.imp().big.set(big);
    }

    pub fn set_locked(&self, on: bool) {
        if on != self.imp().locked.get() {
            self.imp().locked.set(on);
            self.queue_draw();
        }
    }

    pub fn set_sandboxed(&self, on: bool) {
        if on != self.imp().sandboxed.get() {
            self.imp().sandboxed.set(on);
            self.queue_draw();
        }
    }

    pub fn set_pop(&self, v: f64) {
        self.imp().pop.set(v);
        self.queue_draw();
    }

    /// macOS notification badge ("3", "99+"); a growing count pops.
    pub fn set_badge(&self, text: &str) {
        let old = self.imp().badge.borrow().clone();
        if text == old {
            return;
        }
        let grew = !text.is_empty() && (old.is_empty() || badge_n(text) > badge_n(&old));
        self.imp().badge.replace(text.to_string());
        if grew && self.is_mapped() {
            let w = self.downgrade();
            let anim = adw::TimedAnimation::new(self, 0.3, 1.0, sonata_ui::tokens::ms(BADGE_POP_MS as f64), adw::CallbackAnimationTarget::new(move |v| {
                if let Some(w) = w.upgrade() {
                    w.imp().badge_k.set(v);
                    w.queue_draw();
                }
            }));
            anim.set_easing(adw::Easing::EaseOutBack);
            self.imp().badge_k.set(0.3);
            anim.play();
            self.imp().badge_anim.replace(Some(anim));
        }
        self.queue_draw();
    }

    fn paintable(&self, size: i32) -> Option<gdk::Paintable> {
        // the Trash and Launchpad follow Light/Dark
        let key = (size, sonata_ui::is_dark());
        if let Some(p) = self.imp().paint.borrow().get(&key) {
            return Some(p.clone());
        }
        let gicon = self.imp().gicon.borrow().clone()?;
        let p = sonata_ui::icons::paintable(self, &gicon, size)?;
        let mut cache = self.imp().paint.borrow_mut();
        if cache.len() > 2 {
            cache.clear();
        }
        cache.insert(key, p.clone());
        Some(p)
    }

    fn draw(&self, snap: &gtk::Snapshot) {
        let imp = self.imp();
        let s = imp.size.get();
        let base = imp.base.get();
        let size = if s == base { s } else { s.max(imp.big.get()) };
        let pop = imp.pop.get();
        if pop != 1.0 {
            // popping out of an undone folder (Dock.pop_in)
            let c = s as f32 / 2.0;
            snap.translate(&graphene::Point::new(c, c));
            snap.scale(pop as f32, pop as f32);
            snap.translate(&graphene::Point::new(-c, -c));
        }
        if let Some(p) = self.paintable(size) {
            p.snapshot(snap, s as f64, s as f64);
        }
        if imp.locked.get() {
            dock_folder::draw_lock_badge(snap, s as f64);
        }
        let badge = imp.badge.borrow().clone();
        if !badge.is_empty() {
            let (fill, ink) = badge_colors();
            self.draw_badge(snap, &badge, &fill, &ink, imp.badge_k.get());
        } else if imp.sandboxed.get() {
            // Open in Sandbox: an orange "S" where a badge goes
            self.draw_badge(snap, "S", &rgba("#ff9f0a"), &rgba("#000000"), 1.0);
        }
    }

    /// A pill at the icon's top right in the accent colour, its number white
    /// or black (scales with the icon, so it grows with magnification).
    fn draw_badge(&self, snap: &gtk::Snapshot, text: &str, fill: &gdk::RGBA, ink: &gdk::RGBA, k: f64) {
        let s = self.imp().size.get() as f64;
        let layout = self.create_pango_layout(Some(text));
        let mut fd = pango::FontDescription::from_string(&format!("Sans Bold {:.1}px", (s * 0.2).max(6.0)));
        fd.set_absolute_size((s * 0.24).max(7.0) * pango::SCALE as f64);
        layout.set_font_description(Some(&fd));
        let (tw, th) = layout.pixel_size();
        let (tw, th) = (tw as f64, th as f64);
        let h = (s * 0.38).max(th + 2.0);
        let w = h.max(tw + h * 0.55);
        let (x, y) = (s - w * 0.78, -h * 0.12);
        if k != 1.0 {
            snap.save();
            snap.translate(&graphene::Point::new((x + w / 2.0) as f32, (y + h / 2.0) as f32));
            snap.scale(k as f32, k as f32);
            snap.translate(&graphene::Point::new(-(x + w / 2.0) as f32, -(y + h / 2.0) as f32));
        }
        let rr = rounded(x, y, w, h, h / 2.0);
        snap.append_outset_shadow(&rr, &rgba("rgba(0,0,0,0.25)"), 0.0, 1.0, 0.0, 2.0);
        snap.push_rounded_clip(&rr);
        snap.append_color(fill, &rect(x, y, w, h));
        snap.pop();
        snap.save();
        snap.translate(&graphene::Point::new((x + (w - tw) / 2.0) as f32, (y + (h - th) / 2.0) as f32));
        snap.append_layout(&layout, ink);
        snap.restore();
        if k != 1.0 {
            snap.restore();
        }
    }
}

/// A tile's icon: an app's, or a folder's (the same interface).
#[derive(Clone)]
pub enum Icon {
    App(DockIcon),
    Folder(dock_folder::FolderIcon),
}

impl Icon {
    pub fn widget(&self) -> gtk::Widget {
        match self {
            Icon::App(i) => i.clone().upcast(),
            Icon::Folder(f) => f.clone().upcast(),
        }
    }

    pub fn set_size(&self, size: f64) {
        match self {
            Icon::App(i) => i.set_size(size),
            Icon::Folder(f) => f.set_size(size),
        }
    }

    pub fn set_scale_hint(&self, base: i32, big: i32) {
        if let Icon::App(i) = self {
            i.set_scale_hint(base, big);
        }
    }

    pub fn set_badge(&self, text: &str) {
        if let Icon::App(i) = self {
            i.set_badge(text);
        }
    }

    pub fn set_locked(&self, on: bool) {
        match self {
            Icon::App(i) => i.set_locked(on),
            Icon::Folder(f) => f.set_locked(on),
        }
    }

    pub fn set_gicon(&self, gicon: Option<&gio::Icon>) {
        if let Icon::App(i) = self {
            i.set_gicon(gicon);
        }
    }

    pub fn set_pop(&self, v: f64) {
        match self {
            Icon::App(i) => i.set_pop(v),
            Icon::Folder(f) => f.set_pop(v),
        }
    }
}

/// The small name bubble macOS shows beside Dock icons (ui/label.py; its
/// CSS is registered in load_css until the exporter covers ui/label.py).
pub struct HoverLabel {
    pub pop: gtk::Popover,
    label: gtk::Label,
}

const LABEL_CSS: &str = "
popover.hover-label { background: none; box-shadow: none; padding: 0; }
popover.hover-label > contents {
  padding: 3px 10px; border-radius: %(r_label)s; min-height: 0;
  font-family: %(font)s; font-size: %(text_body)s; font-weight: 400;
  color: %(label)s; background-color: %(panel_material)s;     /* glass, like menus (Vini) */
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_label)s;
}
";

impl HoverLabel {
    pub fn new(widget: &impl IsA<gtk::Widget>, text: &str, position: gtk::PositionType) -> Self {
        let label = gtk::Label::new(Some(text));
        let pop = gtk::Popover::builder().css_classes(["hover-label"]).has_arrow(false).autohide(false).can_target(false).position(position).child(&label).build();
        let gap = 8;
        let (x, y) = match position {
            gtk::PositionType::Top => (0, -gap),
            gtk::PositionType::Bottom => (0, gap),
            gtk::PositionType::Left => (-gap, 0),
            _ => (gap, 0),
        };
        pop.set_offset(x, y);
        pop.set_parent(widget);
        let motion = gtk::EventControllerMotion::new();
        let (p1, p2) = (pop.downgrade(), pop.downgrade());
        motion.connect_enter(move |_, _, _| {
            if let Some(p) = p1.upgrade() {
                p.popup();
            }
        });
        motion.connect_leave(move |_| {
            if let Some(p) = p2.upgrade() {
                p.popdown();
            }
        });
        widget.add_controller(motion);
        HoverLabel { pop, label }
    }

    pub fn popup(&self) {
        self.pop.popup();
    }

    pub fn popdown(&self) {
        self.pop.popdown();
    }

    pub fn set_text(&self, text: &str) {
        self.label.set_label(text);
    }
}

/// One Dock icon: icon, running dots (towards the edge), hover label.
pub struct Tile {
    pub button: gtk::Button,
    pub key: RefCell<Option<String>>,
    pub name: RefCell<String>,
    pub gicon: RefCell<Option<gio::Icon>>,
    pub info: Option<gio::DesktopAppInfo>,
    pub icon: Icon,
    dots: Vec<gtk::Box>,
    pub label: HoverLabel,
    bounce_src: RefCell<Option<glib::SourceId>>,
    /// a window's app named by its title (windowapps): follows it
    pub dynamic: Cell<bool>,
    pub spawn: Cell<bool>,
    /// a stack's folder and its choices (dock_stack.py)
    pub spec: RefCell<Map<String, Value>>,
    pub stack: dock_stack::TileState,
    /// a folder's kept panel (dock_folder.py)
    pub folder_pop: RefCell<Option<Rc<dock_folder::Panel>>>,
    pub fade: RefCell<Option<adw::TimedAnimation>>,
}

pub type TileFn = Rc<dyn Fn(&Rc<Tile>)>;

impl Tile {
    pub fn new(dock: &Dock, name: &str, gicon: Option<gio::Icon>, on_click: TileFn, info: Option<gio::DesktopAppInfo>, on_menu: Option<TileFn>, icon: Option<Icon>) -> Rc<Tile> {
        let edge = dock.edge.as_str();
        let size = geti(&dock.cfg.borrow(), "icon_size") as i32;
        let button = gtk::Button::builder()
            .css_classes(["dock-tile"])
            .focus_on_click(false)
            .can_focus(false)
            .valign(if edge == "bottom" { gtk::Align::End } else { gtk::Align::Fill })
            .halign(match edge {
                "left" => gtk::Align::Start,
                "right" => gtk::Align::End,
                _ => gtk::Align::Fill,
            })
            .build();
        let icon = icon.unwrap_or_else(|| Icon::App(DockIcon::new(gicon.as_ref(), size)));
        icon.set_scale_hint(size, max_icon(&dock.cfg.borrow()) as i32);
        // one dot per open window (up to MAX_DOTS; Vini's call, macOS shows one)
        let dot_box = gtk::Box::builder()
            .css_classes(["dock-dots"])
            .spacing(3)
            .halign(gtk::Align::Center)
            .valign(gtk::Align::Center)
            .orientation(if edge == "bottom" { gtk::Orientation::Horizontal } else { gtk::Orientation::Vertical })
            .build();
        let dots: Vec<gtk::Box> = (0..MAX_DOTS).map(|_| gtk::Box::builder().css_classes(["dock-dot"]).build()).collect();
        for (i, d) in dots.iter().enumerate() {
            dot_box.append(d);
            d.set_visible(i == 0);
        }
        let inner = gtk::Box::new(if edge == "bottom" { gtk::Orientation::Vertical } else { gtk::Orientation::Horizontal }, 0);
        if edge == "left" {
            inner.append(&dot_box);
            inner.append(&icon.widget());
        } else {
            inner.append(&icon.widget());
            inner.append(&dot_box);
        }
        button.set_child(Some(&inner));
        let label = HoverLabel::new(&button, name, dock.away);
        let tile = Rc::new(Tile {
            button,
            key: RefCell::new(None),
            name: RefCell::new(name.to_string()),
            gicon: RefCell::new(gicon),
            info,
            icon,
            dots,
            label,
            bounce_src: RefCell::new(None),
            dynamic: Cell::new(false),
            spawn: Cell::new(false),
            spec: RefCell::default(),
            stack: dock_stack::TileState::default(),
            folder_pop: RefCell::new(None),
            fade: RefCell::new(None),
        });
        let w = Rc::downgrade(&tile);
        tile.button.connect_clicked(move |_| {
            if let Some(t) = w.upgrade() {
                on_click(&t);
            }
        });
        if let Some(on_menu) = on_menu {
            let right = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
            let w = Rc::downgrade(&tile);
            right.connect_pressed(move |_, _, _, _| {
                if let Some(t) = w.upgrade() {
                    on_menu(&t);
                }
            });
            tile.button.add_controller(right);
        }
        tile
    }

    pub fn key(&self) -> Option<String> {
        self.key.borrow().clone()
    }

    pub fn name(&self) -> String {
        self.name.borrow().clone()
    }

    pub fn widget(&self) -> gtk::Widget {
        self.button.clone().upcast()
    }

    pub fn set_gicon(&self, gicon: gio::Icon) {
        self.icon.set_gicon(Some(&gicon));
        self.gicon.replace(Some(gicon));
    }

    pub fn set_name(&self, name: &str) {
        self.name.replace(name.to_string());
        self.label.set_text(name);
    }

    /// `windows`: how many are open; that many dots show.
    pub fn set_running(&self, windows: usize) {
        let n = windows.min(MAX_DOTS);
        for (i, d) in self.dots.iter().enumerate() {
            d.set_visible(i < n.max(1)); // the first keeps its place when off
            if i < n {
                d.add_css_class("on");
            } else {
                d.remove_css_class("on");
            }
        }
        if n > 0 {
            self.button.add_css_class("running");
            if self.bounce_src.borrow().is_some() {
                self.stop_bounce();
            }
        } else {
            self.button.remove_css_class("running");
        }
    }

    /// Bounce until the app's first window appears (set_running) or `ms`.
    pub fn bounce(self: &Rc<Self>, ms: u32) {
        self.button.add_css_class("launching");
        if let Some(s) = self.bounce_src.borrow_mut().take() {
            sonata_ui::cancel(s);
        }
        let w = Rc::downgrade(self);
        let src = glib::timeout_add_local_once(Duration::from_millis(ms as u64), move || {
            if let Some(t) = w.upgrade() {
                t.bounce_src.replace(None);
                t.stop_bounce();
            }
        });
        self.bounce_src.replace(Some(src));
    }

    pub fn stop_bounce(&self) {
        if let Some(s) = self.bounce_src.borrow_mut().take() {
            sonata_ui::cancel(s);
        }
        self.button.remove_css_class("launching");
    }
}

/// `widget` is (inside) a Dock icon or the divider.
pub fn on_an_item(widget: Option<gtk::Widget>) -> bool {
    let mut w = widget;
    while let Some(x) = w {
        if x.has_css_class("dock-tile") || x.has_css_class("dock-divider") {
            return true;
        }
        w = x.parent();
    }
    false
}

/// A 1 px bar across the Dock (divider or recents separator).
fn dock_line(vertical: bool, edge: &str, css: &str) -> gtk::Box {
    let b = gtk::Box::builder().css_classes([css]).orientation(if vertical { gtk::Orientation::Horizontal } else { gtk::Orientation::Vertical }).build();
    b.append(&gtk::Box::builder().hexpand(true).vexpand(true).build());
    if edge == "bottom" {
        b.set_valign(gtk::Align::End);
    } else {
        b.set_halign(if edge == "left" { gtk::Align::Start } else { gtk::Align::End });
    }
    b
}

fn size_line(line: &gtk::Box, vertical: bool, icon_size: i64) {
    let span = (icon_size - 8).max(8) as i32; // the icon area, not the dot row
    if vertical {
        line.set_size_request(span, DIVIDER_W as i32);
    } else {
        line.set_size_request(DIVIDER_W as i32, span);
    }
}

/// Close Launchpad (its "close" action over D-Bus; nothing if it isn't running).
pub fn close_launchpad() {
    launchpad_action("close", None);
}

/// The Launchpad that runs (the Python one: Launchpad isn't ported yet,
/// so it's the one the Dock opens on trial too).
pub const LAUNCHPAD_APP_ID: &str = "io.github.vinioliveiras.sonata2.launchpad";

/// The running Launchpad's `action` over D-Bus, without waiting;
/// failed() when it isn't running (or doesn't answer).
pub fn launchpad_action(action: &str, failed: Option<Box<dyn Fn()>>) {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else {
        if let Some(f) = failed {
            f();
        }
        return;
    };
    let params = (action, Vec::<glib::Variant>::new(), HashMap::<String, glib::Variant>::new()).to_variant();
    let path = format!("/{}", LAUNCHPAD_APP_ID.replace('.', "/"));
    bus.call(Some(LAUNCHPAD_APP_ID), &path, "org.freedesktop.Application", "ActivateAction", Some(&params), None, gio::DBusCallFlags::NO_AUTO_START, 800, gio::Cancellable::NONE, move |res| {
        if res.is_err() {
            if let Some(f) = &failed {
                f();
            }
        }
    });
}

/// A window of the entry's own app id (StartupWMClass) is open. A web
/// app's browser runs in a scope of its own: the Dock thought WhatsApp
/// showed no window, killed it (Vini). Can't tell: true (never killed on a guess).
pub fn class_has_window(info: &gio::DesktopAppInfo) -> bool {
    let Some(wm) = info.startup_wm_class().filter(|w| !w.is_empty()) else { return false };
    match sonata_backend::wfipc::call("window-rules/list-views", json!({})) {
        Some(Value::Array(views)) => views.iter().any(|v| v["app-id"] == wm.as_str() && v.get("role").and_then(Value::as_str).unwrap_or("toplevel") == "toplevel"),
        Some(_) => false,
        None => true,
    }
}

/// A window of that launch's processes is open (each window's pid, then
/// its cgroup): the app runs, maybe under another icon -- never "stuck".
pub fn scope_has_window(unit: Option<&str>) -> bool {
    let Some(unit) = unit.filter(|u| !u.is_empty()) else { return false };
    let Some(views) = sonata_backend::wfipc::call("window-rules/list-views", json!({})) else { return true };
    views.as_array().into_iter().flatten().filter_map(|v| v.get("pid")?.as_i64()).filter(|p| *p > 0).any(|pid| std::fs::read_to_string(format!("/proc/{pid}/cgroup")).is_ok_and(|c| c.contains(unit)))
}

// -- the drag in progress -------------------------------------------------------------------------

#[derive(Default)]
pub struct Drag {
    pub key: String,
    pub index: usize,
    pub left: bool,
    pub dropped: bool,
    pub x: Option<f64>,
    pub y: Option<f64>,
    pub trash: bool,
    over: Option<Rc<Tile>>,
    target: Option<Rc<Tile>>,
    hold_src: Option<glib::SourceId>,
    gen: u64,
    bounds: Option<Vec<(Rc<Tile>, (f64, f64, f64, f64))>>,
    pub from_folder: Option<String>,
    pub folder_index: usize,
}

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum GapSlot {
    None,
    App(usize),
    Stack,
}

/// The plate with all tiles. Hosted by DockWindow or by the preview.
pub struct Dock {
    me: Weak<Dock>,
    pub plate: Plate,
    pub cfg: Cfg,
    pub edge: String,
    pub vertical: bool,
    /// labels' and menus' side
    pub away: gtk::PositionType,
    /// the size chosen; cfg["icon_size"] is the size shown
    pub user_size: Cell<i64>,
    pub manager: Option<Rc<Manager>>,
    manager_ids: RefCell<Vec<u64>>,
    pub on_menu_closed: RefCell<Option<Rc<dyn Fn()>>>,
    /// desktop id (or bare app_id) -> tile (apps only)
    pub tiles: RefCell<HashMap<String, Rc<Tile>>>,
    /// same keys -> windows
    pub windows: RefCell<HashMap<String, Vec<Toplevel>>>,
    pub launcher_open: Cell<bool>,
    starting: RefCell<HashMap<String, Instant>>,
    retries: RefCell<HashMap<String, u32>>,
    /// preview only: blurred wallpaper under the plate
    pub backdrop: RefCell<Option<gdk::Texture>>,
    pub on_geometry: RefCell<Vec<Rc<dyn Fn()>>>,
    pub on_rebuild: RefCell<Option<Rc<dyn Fn()>>>,
    /// 0 shown .. 1 slid out (auto-hide), set by the host
    pub hide_amount: Cell<f64>,
    pub recent_sep: gtk::Box,
    pub sep: gtk::Box,
    pub stacks: Rc<dock_stack::StackRow>,
    pub trash: Rc<Tile>,
    pub drag: RefCell<Option<Drag>>,
    drag_gen: Cell<u64>,
    pub badges: RefCell<HashMap<String, String>>,
    title_counts: RefCell<HashMap<String, i64>>,
    notes: RefCell<(Option<i64>, Vec<sonata_desktop::badges::Note>)>,
    badge_cfg: RefCell<Value>,
    monitors: RefCell<Vec<gio::FileMonitor>>,
    launcher_sub: RefCell<Option<(gio::DBusConnection, gio::SignalSubscriptionId)>>,
    apps_mon: RefCell<Option<glib::SignalHandlerId>>,
    apps_src: RefCell<Option<glib::SourceId>>,
    gone_src: RefCell<Option<glib::SourceId>>,
    refit_src: RefCell<Option<glib::SourceId>>,
    refit_tries: Cell<u32>,
    sync_src: RefCell<Option<glib::SourceId>>,
    sync_sig: RefCell<Option<Vec<(i64, String, bool, bool, bool)>>>,
    windows_seen: Cell<bool>,
    mag_pos: Cell<Option<f64>>,
    pub mag_strength: Cell<f64>,
    mag_anim: RefCell<Option<adw::TimedAnimation>>,
    mag_tick: RefCell<Option<gtk::TickCallbackId>>,
    gap: RefCell<Option<gtk::Box>>,
    gap_slot: Cell<GapSlot>,
    gap_hide: RefCell<Option<glib::SourceId>>,
    folders_pushed: RefCell<String>,
    /// the window the Dock spans along its edge (None: no fit, a preview)
    pub span_host: RefCell<Option<gtk::Widget>>,
    /// Launchpad is open over the desktop (the host's set_above)
    pub above: Cell<bool>,
    /// the display this Dock is on (layer shell)
    pub shown_on: RefCell<Option<gdk::Monitor>>,
    pub app: RefCell<Option<gtk::Application>>,
    menu_closed_id: Cell<u64>,
    slots: RefCell<Vec<adw::TimedAnimation>>,
    rects_src: RefCell<Option<glib::SourceId>>,
}

fn spacer(vertical: bool) -> gtk::Box {
    let b = gtk::Box::builder().css_classes(["dock-end"]).build();
    let pad = pad_side().round() as i32;
    if vertical {
        b.set_size_request(-1, pad);
    } else {
        b.set_size_request(pad, -1);
    }
    b
}

macro_rules! weak_do {
    ($w:expr, $d:ident => $body:expr) => {{
        let w = $w.clone();
        move || {
            if let Some($d) = w.upgrade() {
                $body;
            }
        }
    }};
}

impl Dock {
    pub fn new(cfg: Cfg, manager: Option<Rc<Manager>>) -> Rc<Dock> {
        let position = gets(&cfg.borrow(), "position");
        let edge = if EDGES.contains(&position.as_str()) { position } else { "bottom".into() };
        let vertical = edge != "bottom";
        let away = match edge.as_str() {
            "left" => gtk::PositionType::Right,
            "right" => gtk::PositionType::Left,
            _ => gtk::PositionType::Top,
        };
        let plate: Plate = glib::Object::builder()
            .property("css-classes", vec!["dock-plate".to_string(), format!("edge-{edge}")])
            .property("orientation", if vertical { gtk::Orientation::Vertical } else { gtk::Orientation::Horizontal })
            .property(
                "halign",
                match edge.as_str() {
                    "left" => gtk::Align::Start,
                    "right" => gtk::Align::End,
                    _ => gtk::Align::Center,
                },
            )
            .property("valign", if edge == "bottom" { gtk::Align::End } else { gtk::Align::Center })
            .build();
        let user_size = geti(&cfg.borrow(), "icon_size");
        let manager = manager.filter(|m| m.available);
        let recent_sep = dock_line(vertical, &edge, "dock-recent-sep");
        let sep = dock_line(vertical, &edge, "dock-divider");
        let dock = Rc::new_cyclic(|me: &Weak<Dock>| {
            let trash_click: TileFn = Rc::new(|_| crate::dock_menu::open_folder("trash:///"));
            let me2 = me.clone();
            let trash_menu: TileFn = Rc::new(move |t| {
                if let Some(d) = me2.upgrade() {
                    dock_menu::trash_menu(&d, t);
                }
            });
            // (a Tile needs a Dock for its edge and size: a stand-in made
            // from the same values until the Dock exists)
            let probe = Dock::probe(&cfg, &edge, away);
            let trash = Tile::new(&probe, &tr!("Trash"), Some(gio::ThemedIcon::new("user-trash").upcast()), trash_click, None, Some(trash_menu), None);
            Dock {
                me: me.clone(),
                plate: plate.clone(),
                cfg: cfg.clone(),
                edge: edge.clone(),
                vertical,
                away,
                user_size: Cell::new(user_size),
                manager: manager.clone(),
                manager_ids: RefCell::default(),
            on_menu_closed: RefCell::default(),
                tiles: RefCell::default(),
                windows: RefCell::default(),
                launcher_open: Cell::new(false),
                starting: RefCell::default(),
                retries: RefCell::default(),
                backdrop: RefCell::default(),
                on_geometry: RefCell::default(),
                on_rebuild: RefCell::default(),
                hide_amount: Cell::new(0.0),
                recent_sep: recent_sep.clone(),
                sep: sep.clone(),
                stacks: dock_stack::StackRow::new(me.clone()),
                trash,
                drag: RefCell::default(),
                drag_gen: Cell::new(0),
                badges: RefCell::default(),
                title_counts: RefCell::default(),
                notes: RefCell::new(sonata_desktop::badges::load(None)),
                badge_cfg: RefCell::new(Value::Object(sonata_config::load("notifications", &json!({})))),
                monitors: RefCell::default(),
                launcher_sub: RefCell::default(),
                apps_mon: RefCell::default(),
                apps_src: RefCell::default(),
                gone_src: RefCell::default(),
                refit_src: RefCell::default(),
                refit_tries: Cell::new(0),
                sync_src: RefCell::default(),
                sync_sig: RefCell::default(),
                windows_seen: Cell::new(false),
                mag_pos: Cell::new(None),
                mag_strength: Cell::new(0.0),
                mag_anim: RefCell::default(),
                mag_tick: RefCell::default(),
                gap: RefCell::default(),
                gap_slot: Cell::new(GapSlot::None),
                gap_hide: RefCell::default(),
                folders_pushed: RefCell::default(),
                span_host: RefCell::default(),
                above: Cell::new(false),
                shown_on: RefCell::default(),
                app: RefCell::default(),
                menu_closed_id: Cell::new(0),
                slots: RefCell::default(),
                rects_src: RefCell::default(),
            }
        });
        dock.build();
        dock
    }

    /// The values a Tile reads (edge, size, side) before the Dock exists.
    fn probe(cfg: &Cfg, edge: &str, away: gtk::PositionType) -> Dock {
        let plate: Plate = glib::Object::new();
        Dock {
            me: Weak::new(),
            plate,
            cfg: cfg.clone(),
            edge: edge.to_string(),
            vertical: edge != "bottom",
            away,
            user_size: Cell::new(0),
            manager: None,
            manager_ids: RefCell::default(),
            on_menu_closed: RefCell::default(),
            tiles: RefCell::default(),
            windows: RefCell::default(),
            launcher_open: Cell::new(false),
            starting: RefCell::default(),
            retries: RefCell::default(),
            backdrop: RefCell::default(),
            on_geometry: RefCell::default(),
            on_rebuild: RefCell::default(),
            hide_amount: Cell::new(0.0),
            recent_sep: gtk::Box::new(gtk::Orientation::Horizontal, 0),
            sep: gtk::Box::new(gtk::Orientation::Horizontal, 0),
            stacks: dock_stack::StackRow::new(Weak::new()),
            trash: Rc::new(Tile {
                button: gtk::Button::new(),
                key: RefCell::new(None),
                name: RefCell::default(),
                gicon: RefCell::default(),
                info: None,
                icon: Icon::App(DockIcon::new(None, 1)),
                dots: Vec::new(),
                label: HoverLabel { pop: gtk::Popover::new(), label: gtk::Label::new(None) },
                bounce_src: RefCell::default(),
                dynamic: Cell::new(false),
                spawn: Cell::new(false),
                spec: RefCell::default(),
                stack: dock_stack::TileState::default(),
                folder_pop: RefCell::default(),
                fade: RefCell::default(),
            }),
            drag: RefCell::default(),
            drag_gen: Cell::new(0),
            badges: RefCell::default(),
            title_counts: RefCell::default(),
            notes: RefCell::default(),
            badge_cfg: RefCell::new(Value::Null),
            monitors: RefCell::default(),
            launcher_sub: RefCell::default(),
            apps_mon: RefCell::default(),
            apps_src: RefCell::default(),
            gone_src: RefCell::default(),
            refit_src: RefCell::default(),
            refit_tries: Cell::new(0),
            sync_src: RefCell::default(),
            sync_sig: RefCell::default(),
            windows_seen: Cell::new(false),
            mag_pos: Cell::new(None),
            mag_strength: Cell::new(0.0),
            mag_anim: RefCell::default(),
            mag_tick: RefCell::default(),
            gap: RefCell::default(),
            gap_slot: Cell::new(GapSlot::None),
            gap_hide: RefCell::default(),
            folders_pushed: RefCell::default(),
            span_host: RefCell::default(),
            above: Cell::new(false),
            shown_on: RefCell::default(),
            app: RefCell::default(),
            menu_closed_id: Cell::new(0),
            slots: RefCell::default(),
            rects_src: RefCell::default(),
        }
    }

    pub fn rc(&self) -> Rc<Dock> {
        self.me.upgrade().expect("the Dock is alive")
    }

    pub fn weak(&self) -> Weak<Dock> {
        self.me.clone()
    }

    fn build(self: &Rc<Self>) {
        let plate = &self.plate;
        let w = self.weak();
        plate.imp().paint.replace(Some(Rc::new(move |p, snap| {
            if let Some(d) = w.upgrade() {
                d.paint(p, snap);
            }
        })));
        plate.imp().allocated.replace(Some(Rc::new(weak_do!(self.weak(), d => d.allocated()))));
        // an app uninstalled: its icon goes (no empty slot); a burst of changes -> one check
        let mon = gio::AppInfoMonitor::get();
        let w = self.weak();
        let hid = mon.connect_changed(move |_| {
            if let Some(d) = w.upgrade() {
                sonata_desktop::appmatch::refresh(); // a new app's windows find their entry
                if d.apps_src.borrow().is_none() {
                    let w = d.weak();
                    let src = glib::timeout_add_local_once(Duration::from_millis(800), move || {
                        if let Some(d) = w.upgrade() {
                            d.apps_src.replace(None);
                            d.missing_soon();
                        }
                    });
                    d.apps_src.replace(Some(src));
                }
            }
        });
        self.apps_mon.replace(Some(hid));
        // No CSS padding: the plate is painted over the allocation's edge
        // side, so padding would offset it. Spacers + a minimum thickness
        // (tiles hug the edge) give the same insets.
        plate.append(&spacer(self.vertical));
        plate.append(&self.recent_sep);
        size_line(&self.recent_sep, self.vertical, self.icon_size());
        plate.append(&self.sep);
        self.setup_divider();
        self.trash.button.add_css_class("dock-trash");
        plate.append(&self.trash.button);
        plate.append(&spacer(self.vertical));
        // the badge sources
        let mut mons = Vec::new();
        mons.extend(sonata_desktop::badges::watch(weak_do!(self.weak(), d => d.notes_changed()), None));
        // a Chromium web app's page title (its window's is the site's name): its count
        let page = sonata_desktop::badges::page_titles_path();
        mons.extend(sonata_desktop::badges::watch(weak_do!(self.weak(), d => { let g = d.windows.borrow().clone(); d.sync_titles(&g, false) }), Some(&page)));
        mons.extend(sonata_config::watch("notifications", weak_do!(self.weak(), d => d.badge_cfg_changed())));
        self.monitors.replace(mons);
        self.launcher_entries();
        // only the Dock's own icon drags: a Launchpad app (which also offers
        // a string) must reach the file-list target that pins it
        let drop = gtk::DropTarget::new(glib::Type::STRING, gdk::DragAction::MOVE);
        let w = self.weak();
        drop.connect_accept(move |_, _| w.upgrade().is_some_and(|d| d.drag.borrow().is_some()));
        let w = self.weak();
        drop.connect_motion(move |_, x, y| w.upgrade().map(|d| d.drag_motion(x, y)).unwrap_or(gdk::DragAction::empty()));
        let w = self.weak();
        drop.connect_enter(move |_, x, y| w.upgrade().map(|d| d.drag_motion(x, y)).unwrap_or(gdk::DragAction::empty()));
        let w = self.weak();
        drop.connect_leave(move |_| {
            if let Some(d) = w.upgrade() {
                d.drag_leave();
            }
        });
        let w = self.weak();
        drop.connect_drop(move |_, _, x, y| w.upgrade().is_some_and(|d| d.drag_drop(x, y)));
        plate.add_controller(drop);
        dock_drop::attach_plate(self);
        dock_drop::attach_trash(self, &self.trash);
        self.update_thickness();
        sonata_ui::apps::scan(); // Flatpak / AppImage entries GIO hasn't noticed yet get their icons too
        for did in list(&self.cfg.borrow(), "pinned") {
            self.add_known_tile(&did);
        }
        if getb(&self.cfg.borrow(), "show_recents") {
            for did in list(&self.cfg.borrow(), "recent") {
                if self.tiles.borrow().contains_key(&did) {
                    continue;
                }
                if let Some(info) = sonata_ui::apps::lookup(&did) {
                    self.add_tile(&did, &sonata_ui::apps::display_name(&info), Some(sonata_ui::icons::app_icon(info.upcast_ref())), Some(info), None);
                }
            }
        }
        self.stacks.load();
        self.relayout();
        self.watch_trash();
        self.setup_magnification();
        sonata_ui::on_change(weak_do!(self.weak(), d => d.appearance_changed()));
        let id = next_id();
        self.menu_closed_id.set(id);
        on_menu_closed(id, Rc::new(weak_do!(self.weak(), d => d.menu_closed())));
        if let Some(m) = &self.manager {
            let a = m.add_listener(Rc::new(weak_do!(self.weak(), d => d.schedule_sync())));
            let w = self.weak();
            let b = m.add_event_listener(Rc::new(move |msg: &Value| {
                if let Some(d) = w.upgrade() {
                    d.ipc_event(msg);
                }
            }));
            self.manager_ids.replace(vec![a, b]);
            self.schedule_sync();
            DOCKS.with(|d| d.borrow_mut().push(self.weak()));
        }
        if !REOPENED.with(|r| r.replace(true)) {
            // once a session (a rebuilt Dock doesn't again); never on trial:
            // the Python Dock reopens them, two would open every app twice
            let w = self.weak();
            glib::timeout_add_local_once(Duration::from_millis(REOPEN_AFTER_MS), move || {
                if let Some(d) = w.upgrade() {
                    d.reopen_last_session();
                }
            });
        }
    }

    /// Stop listening to shared objects (before the Dock is replaced).
    pub fn detach(&self) {
        if let Some(m) = &self.manager {
            for id in self.manager_ids.borrow_mut().drain(..) {
                m.remove_listener(id);
            }
        }
        DOCKS.with(|d| d.borrow_mut().retain(|w| w.upgrade().is_some_and(|x| !std::ptr::eq(&*x, self))));
        off_menu_closed(self.menu_closed_id.get());
        if let Some(h) = self.apps_mon.borrow_mut().take() {
            gio::AppInfoMonitor::get().disconnect(h);
        }
        for src in [&self.gone_src, &self.apps_src, &self.refit_src, &self.sync_src] {
            if let Some(s) = src.borrow_mut().take() {
                sonata_ui::cancel(s);
            }
        }
        for m in self.monitors.borrow_mut().drain(..) {
            m.cancel();
        }
        if let Some((bus, sub)) = self.launcher_sub.borrow_mut().take() {
            #[allow(deprecated)]
            bus.signal_unsubscribe(sub);
        }
        self.stacks.detach();
    }

    pub fn icon_size(&self) -> i64 {
        geti(&self.cfg.borrow(), "icon_size")
    }

    pub fn cfg_bool(&self, k: &str) -> bool {
        getb(&self.cfg.borrow(), k)
    }

    pub fn cfg_int(&self, k: &str) -> i64 {
        geti(&self.cfg.borrow(), k)
    }

    pub fn cfg_str(&self, k: &str) -> String {
        gets(&self.cfg.borrow(), k)
    }

    pub fn pinned(&self) -> Vec<String> {
        list(&self.cfg.borrow(), "pinned")
    }

    fn set_pins(&self, pins: Vec<String>) {
        self.cfg.borrow_mut().insert("pinned".into(), json!(pins));
    }

    fn recent(&self) -> Vec<String> {
        list(&self.cfg.borrow(), "recent")
    }

    fn setup_divider(self: &Rc<Self>) {
        let sep = &self.sep;
        size_line(sep, self.vertical, self.icon_size());
        sep.set_cursor_from_name(Some(if self.vertical { "ew-resize" } else { "ns-resize" }));
        // Drag it (towards/away from the screen centre) to resize the Dock
        let drag = gtk::GestureDrag::new();
        let start: Rc<Cell<Option<(i64, f64)>>> = Rc::default();
        let (w, s) = (self.weak(), start.clone());
        drag.connect_drag_begin(move |g, _, _| {
            if let Some(d) = w.upgrade() {
                s.set(d.reach(g).map(|r| (d.icon_size(), r)));
            }
        });
        let (w, s) = (self.weak(), start.clone());
        drag.connect_drag_update(move |g, _, _| {
            let (Some(d), Some((size0, r0))) = (w.upgrade(), s.get()) else { return };
            let Some(now) = d.reach(g) else { return };
            let size = size0 as f64 + (now - r0);
            if (size - d.icon_size() as f64).abs() >= 1.0 {
                d.set_icon_size(size, false, true);
            }
        });
        let w = self.weak();
        drag.connect_drag_end(move |_, _, _| {
            if let Some(d) = w.upgrade() {
                d.save_cfg();
            }
        });
        sep.add_controller(drag);
        let right = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
        let w = self.weak();
        right.connect_pressed(move |_, _, _, _| {
            if let Some(d) = w.upgrade() {
                dock_menu::divider_menu(&d);
            }
        });
        sep.add_controller(right);
    }

    /// How far the pointer is from the screen edge the Dock sits on. The
    /// gesture's own offsets are relative to the bar, which moves as the
    /// Dock grows -- the size fed back on itself and jumped up and down.
    fn reach(&self, g: &gtk::GestureDrag) -> Option<f64> {
        let ev = g.current_event()?;
        let root = self.plate.root()?;
        let (x, y) = ev.position()?;
        let (w, h) = (root.width() as f64, root.height() as f64);
        Some(match self.edge.as_str() {
            "bottom" => h - y,
            "left" => x,
            _ => w - x,
        })
    }

    fn update_thickness(&self) {
        let t = plate_height(self.icon_size()) as i32;
        if self.vertical {
            self.plate.set_size_request(t, -1);
        } else {
            self.plate.set_size_request(-1, t);
        }
    }

    // -- plate ---------------------------------------------------------------------------------

    pub fn plate_rect(&self) -> (f64, f64, f64, f64) {
        let (w, h) = (self.plate.width() as f64, self.plate.height() as f64);
        let t = plate_height(self.icon_size()) as f64;
        match self.edge.as_str() {
            "left" => (0.0, 0.0, t, h),
            "right" => (w - t, 0.0, t, h),
            _ => (0.0, h - t, w, t),
        }
    }

    fn paint(&self, p: &Plate, snap: &gtk::Snapshot) {
        let (w, h) = (p.width() as f64, p.height() as f64);
        let t = plate_height(self.icon_size()) as f64;
        if w <= 0.0 || h <= 0.0 || (if self.edge == "bottom" { h } else { w }) < t {
            return; // not allocated yet (a blurred shadow of 0 px aborts GSK)
        }
        let hide = self.hide_amount.get();
        if hide > 0.0 {
            // auto-hide: slide everything towards the edge
            let d = (hide * (t + self.cfg_int("edge_gap") as f64 + SHADOW as f64)) as f32;
            snap.translate(&match self.edge.as_str() {
                "left" => graphene::Point::new(-d, 0.0),
                "right" => graphene::Point::new(d, 0.0),
                _ => graphene::Point::new(0.0, d),
            });
        }
        let (x, y, pw, ph) = self.plate_rect();
        let radius = dock_radius();
        let rr = rounded(x, y, pw, ph, radius);
        let (dx, dy, blur, col) = shadow("shadow_plate");
        snap.append_outset_shadow(&rr, &col, dx as f32, dy as f32, 0.0, blur as f32);
        snap.push_rounded_clip(&rr);
        if let Some(tex) = self.backdrop.borrow().as_ref() {
            // preview: stands in for the compositor's blur
            // (drawn over the whole window: the blur is made smaller, at its size)
            if let Some(root) = p.root() {
                if let Some(pt) = p.compute_point(&root, &graphene::Point::new(0.0, 0.0)) {
                    snap.append_texture(tex, &rect(-pt.x() as f64, -pt.y() as f64, root.width() as f64, root.height() as f64));
                }
            }
        }
        snap.append_color(&sonata_ui::rgba("dock_material"), &rect(x, y, pw, ph));
        snap.pop();
        snap.append_inset_shadow(&rr, &sonata_ui::rgba("highlight"), 0.0, 0.0, 0.5, 0.0);
        let edge = rounded(x - 0.5, y - 0.5, pw + 1.0, ph + 1.0, radius + 0.5);
        let hair = sonata_ui::rgba("hairline");
        snap.append_border(&edge, &[0.5; 4], &[hair; 4]);
    }

    fn allocated(&self) {
        self.plate.queue_draw();
        if let Some(d) = self.drag.borrow_mut().as_mut() {
            d.bounds = None; // icons moved: folder_candidate measures again
        }
        if self.mag_strength.get() <= 0.0 {
            // not while magnifying (every frame): done once the wave settles
            self.refit_soon();
            self.rects_soon(); // icons moved (fit, apps opened/closed, reorder): minimize targets too
        }
        self.geometry_changed();
    }

    fn geometry_changed(&self) {
        let cbs: Vec<_> = self.on_geometry.borrow().clone();
        for cb in cbs {
            cb();
        }
    }

    /// dock.json with the chosen size (not the shrunk-to-fit one).
    pub fn save_cfg(&self) {
        let mut out = self.cfg.borrow().clone();
        out.insert("icon_size".into(), json!(self.user_size.get()));
        if let Err(e) = sonata_config::save("dock", &out) {
            eprintln!("sonata2-dock: dock.json not saved: {e}");
        }
        // folders linked with Launchpad's (folder_link.py): theirs follow
        let folders = self.cfg.borrow().get("folders").cloned().unwrap_or(json!({}));
        let sig = folders.to_string();
        if *self.folders_pushed.borrow() != sig {
            self.folders_pushed.replace(sig);
            if let Value::Object(mut m) = folders {
                if sonata_desktop::folder_link::to_launchpad(&mut m) {
                    // links made there are written into the Dock's folders
                    self.cfg.borrow_mut().insert("folders".into(), Value::Object(m));
                }
            }
        }
    }

    // -- fit (macOS): a full Dock shrinks its icons, and grows back when apps close

    fn span(&self) -> f64 {
        let Some(host) = self.span_host.borrow().clone() else { return 0.0 };
        let n = if self.vertical { host.height() } else { host.width() } as f64;
        n - 2.0 * SHADOW as f64 - 2.0 * SCREEN_MARGIN
    }

    fn fit(&self) -> i64 {
        fit_size(self.span(), self.all_tiles().len(), self.user_size.get(), self.recent_sep.get_visible(), pad_side())
    }

    pub fn refit(&self) {
        self.refit_src.replace(None);
        if self.span() <= 0.0 {
            if self.span_host.borrow().is_none() {
                return;
            }
            // the surface isn't laid out along the edge yet (login): try again
            let n = self.refit_tries.get() + 1;
            self.refit_tries.set(n);
            if n < 50 {
                let w = self.weak();
                let src = glib::timeout_add_local_once(Duration::from_millis(100), move || {
                    if let Some(d) = w.upgrade() {
                        d.refit();
                    }
                });
                self.refit_src.replace(Some(src));
            }
            return;
        }
        self.refit_tries.set(0);
        let size = self.fit();
        if size != self.icon_size() {
            self.set_icon_size(size as f64, false, false);
            self.plate.queue_resize();
            if let Some(p) = self.plate.parent() {
                p.queue_resize();
            }
        }
    }

    pub fn refit_soon(&self) {
        if self.refit_src.borrow().is_some() {
            return;
        }
        let w = self.weak();
        let src = glib::idle_add_local_once(move || {
            if let Some(d) = w.upgrade() {
                d.refit();
            }
        });
        self.refit_src.replace(Some(src));
    }

    /// Room for one more pinned app (pins must fit at PIN_MIN_SIZE).
    pub fn can_pin(&self, key: &str) -> bool {
        if self.pinned().iter().any(|k| k == key) || self.span() <= 0.0 {
            return true;
        }
        let fixed = self.stacks.tiles().len() + 1; // stacks + Trash
        (self.pinned().len() + 1 + fixed) as f64 <= cells_room(self.span(), PIN_MIN_SIZE, self.recent_sep.get_visible(), pad_side()).floor()
    }

    /// Resize the Dock (divider drag, settings). chosen=false: the fit
    /// (the size shown changes, the size chosen stays).
    pub fn set_icon_size(&self, size: f64, save: bool, chosen: bool) {
        let mut size = (size.round() as i64).clamp(MIN_SIZE, MAX_SIZE);
        if chosen {
            self.user_size.set(size);
            size = size.min(self.fit());
        }
        if size == self.icon_size() {
            if chosen && save {
                self.save_cfg();
            }
            return;
        }
        {
            let mut c = self.cfg.borrow_mut();
            c.insert("icon_size".into(), json!(size));
            let mag = geti(&c, "magnified_size").max(size);
            c.insert("magnified_size".into(), json!(mag));
        }
        load_css(&self.cfg.borrow()); // dot gaps scale with the size
        let big = max_icon(&self.cfg.borrow()) as i32;
        for tile in self.all_tiles() {
            tile.icon.set_scale_hint(size as i32, big);
            tile.icon.set_size(size as f64);
        }
        size_line(&self.sep, self.vertical, size);
        size_line(&self.recent_sep, self.vertical, size);
        self.update_thickness();
        self.plate.queue_draw();
        self.geometry_changed();
        if save {
            self.save_cfg();
        }
    }

    pub fn set_magnification(&self, on: bool, size: Option<i64>) {
        {
            let mut c = self.cfg.borrow_mut();
            c.insert("magnification".into(), json!(on));
            if let Some(s) = size {
                let v = s.min(MAX_SIZE).max(geti(&c, "icon_size"));
                c.insert("magnified_size".into(), json!(v));
            }
        }
        self.save_cfg();
        self.mag_strength.set(0.0);
        self.apply_magnification();
        self.hint_sizes();
        self.geometry_changed();
    }

    fn hint_sizes(&self) {
        let (base, big) = (self.icon_size() as i32, max_icon(&self.cfg.borrow()) as i32);
        for t in self.all_tiles() {
            t.icon.set_scale_hint(base, big);
        }
    }

    /// Change a Dock setting; position/recents changes rebuild the Dock.
    pub fn set_option(&self, key: &str, value: Value) {
        self.cfg.borrow_mut().insert(key.into(), value);
        self.save_cfg();
        if ["position", "show_recents"].contains(&key) {
            if let Some(cb) = self.on_rebuild.borrow().clone() {
                glib::idle_add_local_once(move || cb());
            }
        }
        self.geometry_changed();
    }

    // -- magnification ---------------------------------------------------------------------------

    /// macOS wave: icons near the pointer grow, neighbours make room; it
    /// eases in on enter and out on leave.
    fn setup_magnification(self: &Rc<Self>) {
        let motion = gtk::EventControllerMotion::new();
        let w = self.weak();
        motion.connect_enter(move |_, x, y| {
            if let Some(d) = w.upgrade() {
                d.mag_move(x, y);
                d.mag_animate(1.0, MAG_IN_MS);
            }
        });
        let w = self.weak();
        motion.connect_motion(move |_, x, y| {
            if let Some(d) = w.upgrade() {
                d.mag_move(x, y);
            }
        });
        let w = self.weak();
        motion.connect_leave(move |_| {
            if let Some(d) = w.upgrade() {
                d.mag_animate(0.0, MAG_OUT_MS);
            }
        });
        self.plate.add_controller(motion);
    }

    pub fn mag_move(&self, x: f64, y: f64) {
        if !self.cfg_bool("magnification") || self.drag.borrow().is_some() {
            return;
        }
        let p = self.plate.parent().and_then(|parent| self.plate.compute_point(&parent, &graphene::Point::new(x as f32, y as f32)));
        self.mag_pos.set(p.map(|p| if self.vertical { p.y() } else { p.x() } as f64));
        // a mouse reports up to 1000 moves a second: the wave is worked
        // out once per frame, with the latest position
        if self.mag_tick.borrow().is_none() {
            let w = self.weak();
            let id = self.plate.add_tick_callback(move |_, _| {
                if let Some(d) = w.upgrade() {
                    d.mag_tick.replace(None);
                    d.apply_magnification();
                }
                glib::ControlFlow::Break
            });
            self.mag_tick.replace(Some(id));
        }
    }

    /// For --magnify: the wave at `pos`, fully grown.
    pub fn magnify_at(&self, pos: f64) {
        self.cfg.borrow_mut().insert("magnification".into(), json!(true));
        self.mag_pos.set(Some(pos));
        self.mag_strength.set(1.0);
        self.hint_sizes();
        self.apply_magnification();
    }

    fn mag_animate(&self, to: f64, ms: u32) {
        if !self.cfg_bool("magnification") {
            return;
        }
        if let Some(a) = self.mag_anim.borrow_mut().take() {
            a.pause();
        }
        let w = self.weak();
        let anim = adw::TimedAnimation::new(&self.plate, self.mag_strength.get(), to, sonata_ui::tokens::ms(ms as f64), adw::CallbackAnimationTarget::new(move |v| {
            if let Some(d) = w.upgrade() {
                d.mag_strength.set(v);
                d.apply_magnification();
            }
        }));
        anim.set_easing(adw::Easing::EaseOutCubic);
        anim.play();
        self.mag_anim.replace(Some(anim));
    }

    pub fn apply_magnification(&self) {
        let base = self.icon_size() as f64;
        let tiles = self.all_tiles();
        let s = self.mag_strength.get();
        let pos = self.mag_pos.get();
        let (true, true, Some(pos)) = (self.cfg_bool("magnification"), s > 0.0, pos) else {
            for t in &tiles {
                t.icon.set_size(base);
            }
            return;
        };
        let first_extra = self.first_extra();
        let after_divider = self.stacks.tiles().first().cloned().unwrap_or_else(|| self.trash.clone());
        let recent = self.recent_sep.get_visible();
        let lines: Vec<f64> = tiles
            .iter()
            .map(|t| {
                let mut l = 0.0;
                if recent && first_extra.as_ref().is_some_and(|f| Rc::ptr_eq(f, t)) {
                    l += DIVIDER_W as f64;
                }
                if Rc::ptr_eq(&after_divider, t) {
                    l += DIVIDER_W as f64;
                }
                l
            })
            .collect();
        let cell = base + 2.0 * TILE_PAD as f64;
        let length = 2.0 * pad_side().round() + tiles.len() as f64 * cell + lines.iter().sum::<f64>();
        let span = self.plate.parent().map(|p| if self.vertical { p.height() } else { p.width() } as f64).unwrap_or(length);
        let sizes = wave(base, self.cfg_int("magnified_size") as f64, s, pos, span, &lines, pad_side());
        for (t, size) in tiles.iter().zip(sizes) {
            t.icon.set_size(size);
        }
    }

    // -- session, badges ---------------------------------------------------------------------------

    /// The last session's apps (open_apps.reopen_after_login); one with a
    /// tile here opens as if clicked (it bounces until its window shows).
    fn reopen_last_session(&self) {
        let mut running: Vec<String> = self.windows.borrow().keys().cloned().collect();
        running.extend(self.starting.borrow().keys().cloned());
        if let Some(m) = &self.manager {
            running.extend(m.toplevels().iter().map(|t| sonata_desktop::appmatch::match_app_id(&t.app_id).unwrap_or_else(|| t.app_id.clone())));
        }
        let w = self.weak();
        let launch: sonata_desktop::open_apps::Launcher = Rc::new(move |info: &gio::DesktopAppInfo| {
            let did = info.id().map(|s| s.to_string()).unwrap_or_default();
            let tile = w.upgrade().and_then(|d| {
                let t = d.tiles.borrow();
                t.get(&did).or_else(|| t.get(sonata_desktop::open_apps::bare(&did))).cloned()
            });
            match (w.upgrade(), tile) {
                (Some(d), Some(t)) => d.launch(&t), // a Dock app: bounces like a click
                _ => {
                    let _ = launch_info(info, &[], None);
                }
            }
        });
        let n = sonata_desktop::open_apps::reopen_after_login(&running, Some(launch));
        if n > 0 {
            println!("sonata2-dock: reopened {n} apps from the last session");
        }
    }

    /// Badges and attention from apps (com.canonical.Unity.LauncherEntry:
    /// Telegram, Thunderbird, Discord, Signal...): the count as an accent
    /// badge, "urgent" as the attention bounce -- macOS Dock behaviour.
    fn launcher_entries(&self) {
        let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return };
        let w = self.weak();
        #[allow(deprecated)]
        let sub = bus.signal_subscribe(None, Some("com.canonical.Unity.LauncherEntry"), Some("Update"), None, None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, params| {
            if let Some(d) = w.upgrade() {
                d.launcher_update(params);
            }
        });
        self.launcher_sub.replace(Some((bus, sub)));
    }

    fn launcher_update(&self, params: &glib::Variant) {
        let Some((uri, props)) = params.get::<(String, HashMap<String, glib::Variant>)>() else { return };
        let did = uri.replace("application://", "");
        let key = did.strip_suffix(".desktop").unwrap_or(&did).to_string();
        let num = |v: &glib::Variant| v.get::<i64>().or_else(|| v.get::<i32>().map(i64::from)).or_else(|| v.get::<u32>().map(i64::from)).or_else(|| v.get::<u64>().map(|x| x as i64));
        let count = props.get("count").and_then(num);
        let visible = props.get("count-visible").and_then(|v| v.get::<bool>());
        if count.is_some() || visible.is_some() {
            let show = visible.unwrap_or_else(|| self.badges.borrow().get(&key).is_some_and(|s| !s.is_empty()));
            let text = if show { sonata_desktop::badges::label(count.unwrap_or(0)) } else { String::new() };
            self.badges.borrow_mut().insert(key.clone(), text);
        }
        self.refresh_badges();
        let tile = {
            let tiles = self.tiles.borrow();
            tiles.get(&key).cloned().or_else(|| tiles.iter().find(|(k, _)| k.to_lowercase() == key.to_lowercase()).map(|(_, t)| t.clone()))
        };
        if let (Some(t), Some(true)) = (tile, props.get("urgent").and_then(|v| v.get::<bool>())) {
            t.bounce(3 * BOUNCE_MS);
        }
    }

    fn notes_changed(&self) {
        self.notes.replace(sonata_desktop::badges::load(None));
        self.refresh_badges();
    }

    fn badge_cfg_changed(&self) {
        self.badge_cfg.replace(Value::Object(sonata_config::load("notifications", &json!({}))));
        self.refresh_badges();
    }

    /// The app's own count, else its web page's, else its notifications
    /// still in Notification Center -- none when badges are off.
    fn badge_text(&self, key: &str, note_counts: &HashMap<String, i64>) -> String {
        let name = self.tiles.borrow().get(key).map(|t| t.name()).unwrap_or_default();
        if !sonata_desktop::badges::allowed(&self.badge_cfg.borrow(), key, &name) {
            return String::new();
        }
        let own = {
            let b = self.badges.borrow();
            b.get(key).cloned().or_else(|| b.iter().find(|(k, _)| k.to_lowercase() == key.to_lowercase()).map(|(_, v)| v.clone()))
        };
        if let Some(own) = own {
            return own; // the app sends counts: its word goes (0 too)
        }
        let t = sonata_desktop::badges::label(self.title_counts.borrow().get(key).copied().unwrap_or(0));
        if !t.is_empty() {
            return t;
        }
        sonata_desktop::badges::label(note_counts.get(key).copied().unwrap_or(0))
    }

    pub fn refresh_badges(&self) {
        let (server, notes) = self.notes.borrow().clone();
        let seen = SEEN.with(|s| {
            let mut s = s.borrow_mut();
            if s.0 != server {
                // another menu bar: its ids start again
                *s = (server, HashMap::new());
            }
            s.1.clone()
        });
        let names: Vec<(String, String)> = self.tiles.borrow().iter().map(|(k, t)| (k.clone(), t.name())).collect();
        let counts = sonata_desktop::badges::note_counts(&notes, names.iter().map(|(k, n)| (k, n)), &seen);
        let tiles: Vec<(String, Rc<Tile>)> = self.tiles.borrow().iter().map(|(k, t)| (k.clone(), t.clone())).collect();
        for (key, tile) in tiles {
            tile.icon.set_badge(&self.badge_text(&key, &counts));
        }
    }

    pub fn all_tiles(&self) -> Vec<Rc<Tile>> {
        let mut out = self.app_tiles();
        out.extend(self.stacks.tiles());
        out.push(self.trash.clone());
        out
    }

    // -- tiles and sections ------------------------------------------------------------------------

    pub fn add_tile(&self, key: &str, name: &str, gicon: Option<gio::Icon>, info: Option<gio::DesktopAppInfo>, icon: Option<Icon>) -> Rc<Tile> {
        let folder = dock_folder::is_folder(key);
        let (w, k) = (self.weak(), key.to_string());
        let click: TileFn = Rc::new(move |t| {
            if let Some(d) = w.upgrade() {
                d.clicked(&k, t);
            }
        });
        let (w, k) = (self.weak(), key.to_string());
        let menu: TileFn = Rc::new(move |t| {
            if let Some(d) = w.upgrade() {
                if folder {
                    dock_folder::folder_menu(&d, t);
                } else {
                    dock_menu::app_menu(&d, &k, t);
                }
            }
        });
        let tile = Tile::new(self, name, gicon, click, info, Some(menu), icon);
        tile.key.replace(Some(key.to_string()));
        self.tiles.borrow_mut().insert(key.to_string(), tile.clone());
        // a badge that arrived before the tile
        let seen = SEEN.with(|s| s.borrow().1.clone());
        let notes = self.notes.borrow().1.clone();
        let one = [(key.to_string(), name.to_string())];
        let counts = sonata_desktop::badges::note_counts(&notes, one.iter().map(|(k, n)| (k, n)), &seen);
        tile.icon.set_badge(&self.badge_text(key, &counts));
        self.plate.insert_child_after(&tile.button, self.sep.prev_sibling().as_ref()); // before the divider
        let src = gtk::DragSource::new();
        src.set_actions(gdk::DragAction::MOVE);
        let (w, k) = (self.weak(), key.to_string());
        src.connect_prepare(move |_, _, _| w.upgrade().map(|d| gdk::ContentProvider::for_value(&d.drag_text(&k).to_value())));
        let (w, t) = (self.weak(), Rc::downgrade(&tile));
        src.connect_drag_begin(move |_, drag| {
            if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                d.drag_begin(drag, &t);
            }
        });
        let (w, t) = (self.weak(), Rc::downgrade(&tile));
        src.connect_drag_cancel(move |_, _, reason| match (w.upgrade(), t.upgrade()) {
            (Some(d), Some(t)) => d.drag_cancel(reason, &t),
            _ => false,
        });
        let (w, t) = (self.weak(), Rc::downgrade(&tile));
        src.connect_drag_end(move |_, _, delete| {
            if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                d.drag_end(delete, &t);
            }
        });
        tile.button.add_controller(src);
        // middle-click: another window of the app (New Window), or open it
        let middle = gtk::GestureClick::builder().button(gdk::BUTTON_MIDDLE).build();
        let (w, t, k) = (self.weak(), Rc::downgrade(&tile), key.to_string());
        middle.connect_released(move |g, _, _, _| {
            g.set_state(gtk::EventSequenceState::Claimed);
            if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                d.middle_click(&k, &t);
            }
        });
        tile.button.add_controller(middle);
        if folder {
            return tile;
        }
        tile.icon.set_locked(sonata_desktop::applock::locked(key));
        if let Icon::App(i) = &tile.icon {
            i.set_sandboxed(sonata_desktop::sandbox::running(key));
        }
        dock_drop::attach_app(&self.rc(), &tile);
        dock_preview::attach(&tile, &self.rc()); // windows: previews on hover
        tile
    }

    pub fn refresh_sandboxes(&self) {
        for (key, tile) in self.tiles.borrow().iter() {
            if let Icon::App(i) = &tile.icon {
                i.set_sandboxed(sonata_desktop::sandbox::running(key));
            }
        }
    }

    /// Locked apps' padlocks (applock.json changed).
    pub fn refresh_locks(&self) {
        for (key, tile) in self.tiles.borrow().iter() {
            if let Icon::App(i) = &tile.icon {
                i.set_locked(sonata_desktop::applock::locked(key));
            }
        }
    }

    fn middle_click(&self, key: &str, tile: &Rc<Tile>) {
        if tile.info.is_none() {
            return;
        }
        if self.windows.borrow().get(key).is_some_and(|w| !w.is_empty()) {
            dock_menu::new_window(tile);
            tile.bounce(BOUNCE_MS); // feedback: something is on its way
        } else {
            self.clicked(key, tile);
        }
    }

    /// A tile for an app with a desktop entry, or an installed Steam game
    /// (steam_app_N: no entry; name and icon from Steam). False: unknown.
    pub fn add_known_tile(&self, key: &str) -> bool {
        if self.tiles.borrow().contains_key(key) {
            return true;
        }
        if dock_folder::is_folder(key) {
            let Some(f) = self.folder(key) else { return false };
            let icon = dock_folder::FolderIcon::new(&folder_apps(&f), self.icon_size() as i32, folder_locked(&f), false, &[]);
            self.add_tile(key, &folder_name(&f), None, None, Some(Icon::Folder(icon)));
            return true;
        }
        if let Some(info) = sonata_ui::apps::lookup(key) {
            self.add_tile(key, &sonata_ui::apps::display_name(&info), Some(sonata_ui::icons::app_icon(info.upcast_ref())), Some(info), None);
            return true;
        }
        if sonata_desktop::steamgames::appid(key).and_then(|a| sonata_desktop::steamgames::name(&a)).is_some() {
            if let Some((name, gicon)) = sonata_desktop::steamgames::shown(key, key) {
                self.add_tile(key, &name, Some(gicon), None, None);
                return true;
            }
        }
        false
    }

    fn known(&self, key: &str) -> bool {
        if dock_folder::is_folder(key) {
            return self.folder(key).is_some();
        }
        sonata_ui::apps::lookup(key).is_some() || sonata_desktop::steamgames::appid(key).and_then(|a| sonata_desktop::steamgames::name(&a)).is_some()
    }

    /// Pinned or shown apps whose entry is gone.
    pub fn missing(&self) -> Vec<String> {
        sonata_ui::apps::scan();
        let pins = self.pinned();
        let mut keys = pins.clone();
        keys.extend(self.tiles.borrow().keys().cloned());
        let mut seen = HashSet::new();
        let windows = self.windows.borrow();
        keys.into_iter().filter(|k| seen.insert(k.clone())).filter(|k| !PERMANENT.contains(&k.as_str()) && !self.known(k) && (pins.contains(k) || !windows.contains_key(k))).collect()
    }

    /// An app's entry vanished: gone for good only if it's still gone a
    /// while later. Vini: an app being updated left the Dock -- the package
    /// manager removes the old entry and writes the new one a moment later.
    fn missing_soon(&self) {
        if self.missing().is_empty() || self.gone_src.borrow().is_some() {
            return;
        }
        let w = self.weak();
        let src = glib::timeout_add_local(Duration::from_millis(GONE_CONFIRM_MS), move || {
            let Some(d) = w.upgrade() else { return glib::ControlFlow::Break };
            if package_manager_busy() {
                return glib::ControlFlow::Continue; // still updating: look again later
            }
            d.gone_src.replace(None);
            d.forget_missing();
            glib::ControlFlow::Break
        });
        self.gone_src.replace(Some(src));
    }

    /// Apps uninstalled: their icons leave the Dock instead of leaving an
    /// empty slot.
    pub fn forget_missing(&self) {
        sonata_ui::apps::scan();
        for key in self.pinned().into_iter().filter(|k| !PERMANENT.contains(&k.as_str()) && !self.known(k)) {
            self.set_pinned(&key, false);
        }
        let pins = self.pinned();
        let gone: Vec<String> = {
            let windows = self.windows.borrow();
            self.tiles.borrow().keys().filter(|k| !pins.contains(k) && !windows.contains_key(*k) && !PERMANENT.contains(&k.as_str())).cloned().collect()
        };
        for key in gone.into_iter().filter(|k| !self.known(k)) {
            self.remove_tile(&key);
        }
    }

    /// The icon goes and its place closes up smoothly (macOS).
    pub fn remove_tile(&self, key: &str) {
        let Some(tile) = self.tiles.borrow_mut().remove(key) else { return };
        tile.label.pop.unparent();
        if let Some(p) = tile.folder_pop.borrow_mut().take() {
            p.pop.unparent(); // a folder's kept panel
        }
        self.close_up(&tile.widget(), true);
    }

    /// `w` leaves the Dock and its place closes up (apps, stacks): an
    /// empty slot of the icon's size takes its place and shrinks to nothing.
    pub fn close_up(&self, w: &gtk::Widget, relayout: bool) {
        let prev = w.prev_sibling();
        let cell = if w.is_mapped() { if self.vertical { w.height() } else { w.width() } } else { 0 };
        self.plate.remove(w);
        if relayout {
            self.relayout();
        }
        let Some(prev) = prev.filter(|p| p.parent().as_ref() == Some(self.plate.upcast_ref())) else { return };
        if cell <= 0 || !self.plate.is_mapped() {
            return;
        }
        let slot = gtk::Box::builder().can_target(false).css_classes(["dock-closing-slot"]).build();
        self.plate.insert_child_after(&slot, Some(&prev));
        let vertical = self.vertical;
        let size = move |slot: &gtk::Box, v: f64| {
            let n = (cell as f64 * v).round().max(0.0) as i32;
            if vertical {
                slot.set_size_request(-1, n);
            } else {
                slot.set_size_request(n, -1);
            }
        };
        size(&slot, 1.0);
        let s2 = slot.clone();
        let anim = adw::TimedAnimation::new(&slot, 1.0, 0.0, sonata_ui::tokens::ms(CLOSE_UP_MS as f64), adw::CallbackAnimationTarget::new(move |v| size(&s2, v)));
        anim.set_easing(adw::Easing::EaseOutCubic);
        let (plate, s3) = (self.plate.clone(), slot.clone());
        anim.connect_done(move |_| {
            if s3.parent().as_ref() == Some(plate.upcast_ref()) {
                plate.remove(&s3);
            }
        });
        self.keep_anim(anim.clone());
        anim.play();
    }

    fn keep_anim(&self, a: adw::TimedAnimation) {
        let mut s = self.slots.borrow_mut();
        s.retain(|x| x.state() != adw::AnimationState::Finished);
        s.push(a);
    }

    /// A new icon (an app opened, a folder dropped): its place opens, the
    /// neighbours sliding aside, then it fades in (Vini: it just popped in).
    pub fn open_up(&self, tile: &Rc<Tile>) {
        if !self.plate.is_mapped() {
            return;
        }
        let mut others = self.app_tiles();
        others.extend(self.stacks.tiles());
        let cell = others
            .iter()
            .find(|t| !Rc::ptr_eq(t, tile) && t.button.is_mapped())
            .map(|t| if self.vertical { t.button.height() } else { t.button.width() })
            .unwrap_or((self.icon_size() + 2 * TILE_PAD) as i32);
        let slot = gtk::Box::builder().can_target(false).css_classes(["dock-closing-slot"]).build();
        self.plate.insert_child_after(&slot, tile.button.prev_sibling().as_ref());
        tile.button.set_visible(false);
        let vertical = self.vertical;
        let size = move |slot: &gtk::Box, v: f64| {
            let n = (cell as f64 * v).round().max(0.0) as i32;
            if vertical {
                slot.set_size_request(-1, n);
            } else {
                slot.set_size_request(n, -1);
            }
        };
        size(&slot, 0.0);
        let s2 = slot.clone();
        let grow = adw::TimedAnimation::new(&slot, 0.0, 1.0, sonata_ui::tokens::ms(OPEN_UP_MS as f64), adw::CallbackAnimationTarget::new(move |v| size(&s2, v)));
        grow.set_easing(adw::Easing::EaseOutCubic);
        let (plate, s3, t) = (self.plate.clone(), slot.clone(), Rc::downgrade(tile));
        grow.connect_done(move |_| {
            if s3.parent().as_ref() == Some(plate.upcast_ref()) {
                plate.remove(&s3);
            }
            let Some(tile) = t.upgrade() else { return };
            tile.button.set_opacity(0.0);
            tile.button.set_visible(true);
            let b = tile.button.clone();
            let fade = adw::TimedAnimation::new(&tile.button, 0.0, 1.0, sonata_ui::tokens::ms(OPEN_UP_MS as f64), adw::CallbackAnimationTarget::new(move |v| b.set_opacity(v)));
            let b = tile.button.clone();
            fade.connect_done(move |_| b.set_opacity(1.0));
            fade.play();
            tile.fade.replace(Some(fade)); // kept alive while it runs
        });
        self.keep_anim(grow.clone());
        grow.play();
    }

    /// App tiles in Dock order (pinned, then recent/running).
    pub fn app_tiles(&self) -> Vec<Rc<Tile>> {
        let tiles = self.tiles.borrow();
        let mut out = Vec::new();
        let mut w = self.plate.first_child();
        while let Some(x) = w {
            if x == *self.sep.upcast_ref::<gtk::Widget>() {
                break;
            }
            if let Some(t) = tiles.values().find(|t| t.button.upcast_ref::<gtk::Widget>() == &x) {
                out.push(t.clone());
            }
            w = x.next_sibling();
        }
        out
    }

    fn extras(&self) -> Vec<Rc<Tile>> {
        let pins = self.pinned();
        self.app_tiles().into_iter().filter(|t| t.key().is_some_and(|k| !pins.contains(&k))).collect()
    }

    fn first_extra(&self) -> Option<Rc<Tile>> {
        self.extras().into_iter().next()
    }

    /// Children order: spacer, pinned (config order), recents bar,
    /// recent/running (current order), divider...
    pub fn relayout(&self) {
        let mut prev = self.plate.first_child();
        for key in self.pinned() {
            let t = self.tiles.borrow().get(&key).cloned();
            if let Some(t) = t {
                self.plate.reorder_child_after(&t.button, prev.as_ref());
                prev = Some(t.widget());
            }
        }
        let extras = self.extras();
        self.plate.reorder_child_after(&self.recent_sep, prev.as_ref());
        let mut prev: gtk::Widget = self.recent_sep.clone().upcast();
        for t in &extras {
            self.plate.reorder_child_after(&t.button, Some(&prev));
            prev = t.widget();
        }
        self.recent_sep.set_visible(!extras.is_empty() && self.cfg_bool("show_recents"));
        // an app being dragged in keeps its slot
        let gap = self.gap.borrow().clone();
        if let (Some(gap), GapSlot::App(slot)) = (gap, self.gap_slot.get()) {
            if gap.parent().as_ref() == Some(self.plate.upcast_ref()) {
                let tiles = self.app_tiles();
                let anchor = if slot > 0 && slot <= tiles.len() { Some(tiles[slot - 1].widget()) } else { self.plate.first_child() };
                self.plate.reorder_child_after(&gap, anchor.as_ref());
            }
        }
        self.refit_soon(); // apps opened/closed: shrink or grow back
    }

    /// Pinned order = the tiles' order. A pinned app without a tile right
    /// now (not found yet) keeps its place: the Dock's order "shuffled"
    /// after a log-in when it was dropped.
    pub fn save_order(&self) {
        let keys: Vec<String> = self.app_tiles().iter().filter_map(|t| t.key()).collect();
        let merged = merge_order(&self.pinned(), &keys);
        self.set_pins(merged);
        self.save_cfg();
        self.relayout();
    }

    /// Index among app tiles where something dropped at (x, y) goes.
    pub fn slot_at(&self, x: f64, y: f64, exclude: Option<&Rc<Tile>>) -> usize {
        let mut slot = 0;
        for t in self.app_tiles() {
            if exclude.is_some_and(|e| Rc::ptr_eq(e, &t)) {
                continue;
            }
            let Some(b) = t.button.compute_bounds(&self.plate) else { continue };
            let centre = if self.vertical { b.y() + b.height() / 2.0 } else { b.x() + b.width() / 2.0 } as f64;
            if centre < if self.vertical { y } else { x } {
                slot += 1;
            }
        }
        slot
    }

    fn move_to_slot(&self, tile: &Rc<Tile>, slot: usize) {
        if let Some(d) = self.drag.borrow_mut().as_mut() {
            d.bounds = None;
        }
        let others: Vec<Rc<Tile>> = self.app_tiles().into_iter().filter(|t| !Rc::ptr_eq(t, tile)).collect();
        let anchor = if slot > 0 && slot <= others.len() { Some(others[slot - 1].widget()) } else { self.plate.first_child() };
        if anchor.as_ref() != Some(&tile.widget()) {
            let before = glide_record(&self.plate, &others.iter().map(|t| t.widget()).collect::<Vec<_>>());
            self.plate.reorder_child_after(&tile.button, anchor.as_ref());
            glide_play(&self.plate, before, GLIDE_MS);
        }
    }

    // -- an app dragged in from elsewhere (Launchpad, Files): the others make room --

    /// Leaving one drop area for the next (plate -> icon) isn't leaving the
    /// Dock: close the gap only if nothing asks for it again right away.
    pub fn hide_drop_gap_soon(&self) {
        if self.gap_hide.borrow().is_some() {
            return;
        }
        let w = self.weak();
        let src = glib::timeout_add_local_once(Duration::from_millis(120), move || {
            if let Some(d) = w.upgrade() {
                d.gap_hide.replace(None);
                d.hide_drop_gap();
            }
        });
        self.gap_hide.replace(Some(src));
    }

    fn cancel_gap_hide(&self) {
        if let Some(s) = self.gap_hide.borrow_mut().take() {
            sonata_ui::cancel(s);
        }
    }

    /// The empty slot a drag opens (made once, an icon's size).
    fn drop_gap(&self) -> gtk::Box {
        if let Some(g) = self.gap.borrow().clone() {
            return g;
        }
        let tiles = self.app_tiles();
        let cell = tiles.first().map(|t| if self.vertical { t.button.height() } else { t.button.width() }).filter(|c| *c > 0).unwrap_or((self.icon_size() + 2 * TILE_PAD) as i32);
        let gap = gtk::Box::builder().can_target(false).build();
        if self.vertical {
            gap.set_size_request(-1, cell);
        } else {
            gap.set_size_request(cell, -1);
        }
        self.gap.replace(Some(gap.clone()));
        gap
    }

    /// Open an empty slot where the dragged app would land (macOS).
    pub fn show_drop_gap(&self, x: f64, y: f64) {
        self.cancel_gap_hide();
        let gap = self.drop_gap();
        let tiles = self.app_tiles();
        let slot = self.slot_at(x, y, None);
        if self.gap_slot.get() == GapSlot::App(slot) {
            return;
        }
        let anchor = if slot > 0 { Some(tiles[slot - 1].widget()) } else { self.plate.first_child() }; // (the leading spacer)
        let before = glide_record(&self.plate, &tiles.iter().map(|t| t.widget()).collect::<Vec<_>>());
        if gap.parent().is_none() {
            self.plate.insert_child_after(&gap, anchor.as_ref());
        } else {
            self.plate.reorder_child_after(&gap, anchor.as_ref());
        }
        self.gap_slot.set(GapSlot::App(slot));
        glide_play(&self.plate, before, GLIDE_MS);
    }

    /// Open an empty slot where a dragged folder lands: after the Dock's
    /// folders, before the Trash (Vini: a folder dragged from Files did
    /// nothing -- no sign it would be kept).
    pub fn show_stack_gap(&self) {
        self.cancel_gap_hide();
        let gap = self.drop_gap();
        if self.gap_slot.get() == GapSlot::Stack {
            return;
        }
        let before = glide_record(&self.plate, &self.all_tiles().iter().map(|t| t.widget()).collect::<Vec<_>>());
        let anchor: gtk::Widget = self.stacks.tiles().last().map(|t| t.widget()).unwrap_or_else(|| self.sep.clone().upcast());
        if gap.parent().is_none() {
            self.plate.insert_child_after(&gap, Some(&anchor));
        } else {
            self.plate.reorder_child_after(&gap, Some(&anchor));
        }
        self.gap_slot.set(GapSlot::Stack);
        glide_play(&self.plate, before, GLIDE_MS);
    }

    /// Close it again; returns where it was (None: none, or the stacks').
    pub fn hide_drop_gap(&self) -> Option<usize> {
        self.cancel_gap_hide();
        let gap = self.gap.borrow().clone()?;
        gap.parent()?;
        let slot = self.gap_slot.get();
        let before = glide_record(&self.plate, &self.all_tiles().iter().map(|t| t.widget()).collect::<Vec<_>>());
        self.plate.remove(&gap);
        self.gap_slot.set(GapSlot::None);
        glide_play(&self.plate, before, GLIDE_MS);
        match slot {
            GapSlot::App(s) => Some(s),
            _ => None,
        }
    }

    /// Pin app `key` before tile `before`, or at (x, y).
    pub fn pin_at(&self, key: &str, before: Option<&Rc<Tile>>, at: Option<(f64, f64)>) {
        if !self.can_pin(key) {
            println!("sonata2-dock: the Dock is full; {key} not kept");
            return;
        }
        let existing = self.tiles.borrow().get(key).cloned();
        if let (true, Some(t)) = (self.pinned().iter().any(|k| k == key), existing.as_ref()) {
            // already kept: it stays where it is (Vini: dragging it in from
            // Launchpad moved it); a bounce shows where it is
            t.bounce(BOUNCE_MS);
            return;
        }
        let tile = match existing {
            Some(t) => t,
            None => {
                let Some(info) = sonata_ui::apps::lookup(key) else { return };
                let t = self.add_tile(key, &sonata_ui::apps::display_name(&info), Some(sonata_ui::icons::app_icon(info.upcast_ref())), Some(info), None);
                t.set_running(self.windows.borrow().get(key).map(Vec::len).unwrap_or(0));
                t
            }
        };
        let others: Vec<Rc<Tile>> = self.app_tiles().into_iter().filter(|t| !Rc::ptr_eq(t, &tile)).collect();
        let slot = match (before.and_then(|b| others.iter().position(|t| Rc::ptr_eq(t, b))), at) {
            (Some(i), _) => i,
            (None, Some((x, y))) => self.slot_at(x, y, Some(&tile)),
            (None, None) => self.pinned().len(),
        };
        self.move_to_slot(&tile, slot);
        if !self.pinned().iter().any(|k| k == key) {
            let mut p = self.pinned();
            p.push(key.into());
            self.set_pins(p);
        }
        self.save_order();
    }

    /// Keep in Dock on/off. Unpinning a running app keeps its icon until
    /// it quits (in the recent/running section, like macOS).
    pub fn set_pinned(&self, key: &str, on: bool) {
        let mut pins = self.pinned();
        if !on && PERMANENT.contains(&key) {
            return;
        }
        if on && !pins.iter().any(|k| k == key) {
            if !self.can_pin(key) {
                return;
            }
            pins.push(key.into());
            self.set_pins(pins);
            self.save_order(); // takes its current position
        } else if !on && pins.iter().any(|k| k == key) {
            pins.retain(|k| k != key);
            self.set_pins(pins);
            if dock_folder::is_folder(key) {
                if let Some(Value::Object(f)) = self.cfg.borrow_mut().get_mut("folders") {
                    f.remove(dock_folder::folder_id(key));
                }
            }
            self.save_cfg();
            if !self.tiles.borrow().contains_key(key) {
                return; // pinned but never shown (not installed)
            }
            if !self.windows.borrow().contains_key(key) && !self.is_recent(key) {
                self.remove_tile(key);
            } else {
                self.relayout();
            }
        }
    }

    // -- folders -----------------------------------------------------------------------------------

    /// The folder's {"name", "apps"} (None: not a folder / gone).
    pub fn folder(&self, key: &str) -> Option<Map<String, Value>> {
        if !dock_folder::is_folder(key) {
            return None;
        }
        self.cfg.borrow().get("folders")?.get(dock_folder::folder_id(key))?.as_object().cloned()
    }

    fn with_folder(&self, key: &str, f: impl FnOnce(&mut Map<String, Value>)) -> bool {
        let mut c = self.cfg.borrow_mut();
        match c.get_mut("folders").and_then(|v| v.get_mut(dock_folder::folder_id(key))).and_then(Value::as_object_mut) {
            Some(m) => {
                f(m);
                true
            }
            None => false,
        }
    }

    fn folders(&self) -> Vec<(String, Map<String, Value>)> {
        self.cfg.borrow().get("folders").and_then(Value::as_object).map(|m| m.iter().filter_map(|(k, v)| Some((k.clone(), v.as_object()?.clone()))).collect()).unwrap_or_default()
    }

    /// The Dock icon of the folder `key` lives in (None: in none).
    pub fn folder_tile_of(&self, key: &str) -> Option<Rc<Tile>> {
        for (fid, f) in self.folders() {
            if folder_apps(&f).iter().any(|a| a == key) {
                return self.tiles.borrow().get(&format!("{}{fid}", dock_folder::PREFIX)).cloned();
            }
        }
        None
    }

    /// Open windows of the app -- of all its apps for a folder.
    pub fn window_count(&self, key: &str) -> usize {
        let w = self.windows.borrow();
        if let Some(f) = self.folder(key) {
            return folder_apps(&f).iter().map(|k| w.get(k).map(Vec::len).unwrap_or(0)).sum();
        }
        w.get(key).map(Vec::len).unwrap_or(0) + usize::from(key == LAUNCHPAD_KEY && self.launcher_open.get())
    }

    /// The Apps Menu opened or closed (Launchpad's process tells): the dot
    /// under Launchpad's icon, as under an app with a window.
    pub fn set_launcher_open(&self, on: bool) {
        self.launcher_open.set(on);
        let t = self.tiles.borrow().get(LAUNCHPAD_KEY).cloned();
        if let Some(t) = t {
            t.set_running(self.window_count(LAUNCHPAD_KEY));
        }
    }

    pub fn in_folder(&self, key: &str) -> bool {
        self.folders().iter().any(|(_, f)| folder_apps(f).iter().any(|a| a == key))
    }

    /// A folder of these pinned apps, where the first of them (or `at_key`) was.
    pub fn make_folder(&self, keys: &[String], name: Option<&str>, at_key: Option<&str>) -> Option<String> {
        let keys: Vec<String> = keys.iter().filter(|k| !dock_folder::is_folder(k) && !PERMANENT.contains(&k.as_str())).cloned().collect();
        if keys.is_empty() {
            return None;
        }
        let fid = {
            let mut c = self.cfg.borrow_mut();
            if !c.get("folders").is_some_and(Value::is_object) {
                c.insert("folders".into(), json!({}));
            }
            let folders = c["folders"].as_object_mut().unwrap();
            let fid = dock_folder::new_id(folders);
            let name = name.map(String::from).unwrap_or_else(|| dock_folder::default_name(&keys));
            folders.insert(fid.clone(), json!({"name": name, "apps": keys}));
            fid
        };
        let fkey = format!("{}{fid}", dock_folder::PREFIX);
        let mut pins = self.pinned();
        let at = match at_key.and_then(|a| pins.iter().position(|k| k == a)) {
            Some(i) => i,
            None => keys.iter().filter_map(|k| pins.iter().position(|p| p == k)).min().unwrap_or(pins.len()),
        };
        pins.insert(at, fkey.clone());
        self.set_pins(pins);
        for k in &keys {
            self.unpin_into_folder(k);
        }
        self.add_known_tile(&fkey);
        let t = self.tiles.borrow().get(&fkey).cloned();
        if let Some(t) = t {
            t.set_running(self.window_count(&fkey));
        }
        self.save_cfg();
        self.relayout();
        Some(fkey)
    }

    /// The app's own icon leaves the pinned row (it now lives in a folder);
    /// a running one too: its windows show on the folder (Vini).
    fn unpin_into_folder(&self, key: &str) {
        let pins: Vec<String> = self.pinned().into_iter().filter(|k| k != key).collect();
        self.set_pins(pins);
        let rec: Vec<String> = self.recent().into_iter().filter(|k| k != key).collect();
        self.cfg.borrow_mut().insert("recent".into(), json!(rec));
        if self.tiles.borrow().contains_key(key) {
            self.remove_tile(key);
        }
    }

    pub fn add_to_folder(&self, fkey: &str, key: &str) {
        if self.folder(fkey).is_none() || dock_folder::is_folder(key) || PERMANENT.contains(&key) {
            return;
        }
        self.with_folder(fkey, |f| {
            let mut apps = folder_apps(f);
            if !apps.iter().any(|a| a == key) {
                apps.push(key.into());
            }
            f.insert("apps".into(), json!(apps));
        });
        self.unpin_into_folder(key);
        self.folder_changed(fkey);
    }

    /// The app comes back to the Dock right after the folder (a folder left
    /// with one app is ungrouped, unless ungroup_last is false).
    pub fn remove_from_folder(&self, fkey: &str, key: &str, ungroup_last: bool) {
        let Some(f) = self.folder(fkey) else { return };
        if !folder_apps(&f).iter().any(|a| a == key) {
            return;
        }
        let mut left = 0;
        self.with_folder(fkey, |f| {
            let apps: Vec<String> = folder_apps(f).into_iter().filter(|a| a != key).collect();
            left = apps.len();
            f.insert("apps".into(), json!(apps));
        });
        let mut pins = self.pinned();
        if !pins.iter().any(|k| k == key) {
            let at = pins.iter().position(|k| k == fkey).map(|i| i + 1).unwrap_or(pins.len());
            pins.insert(at, key.into());
            self.set_pins(pins);
            self.add_known_tile(key);
            let t = self.tiles.borrow().get(key).cloned();
            if let Some(t) = t {
                t.set_running(self.window_count(key));
            }
        }
        if left <= 1 && ungroup_last {
            self.ungroup(fkey);
            return;
        }
        self.folder_changed(fkey);
    }

    /// Out of the folder: the app's own icon comes back to the Dock and is
    /// dragged like any other. Let go anywhere else, or the drag cancelled,
    /// it goes back into its folder where it was (Vini).
    pub fn folder_app_drag_begin(&self, fkey: &str, key: &str, drag: &gdk::Drag) -> bool {
        let Some(f) = self.folder(fkey) else { return false };
        let apps = folder_apps(&f);
        let Some(index) = apps.iter().position(|a| a == key) else { return false };
        if self.drag.borrow().is_some() {
            return false;
        }
        self.remove_from_folder(fkey, key, false);
        let Some(tile) = self.tiles.borrow().get(key).cloned() else { return false };
        self.drag_begin(drag, &tile);
        if let Some(d) = self.drag.borrow_mut().as_mut() {
            d.left = true;
            d.from_folder = Some(fkey.into());
            d.folder_index = index;
        }
        tile.button.set_visible(false); // its slot opens where the pointer reaches the Dock
        true
    }

    pub fn folder_app_drag_end(&self, fkey: &str, key: &str) {
        let ours = self.drag.borrow().as_ref().is_some_and(|d| d.from_folder.as_deref() == Some(fkey));
        if !ours {
            return; // (its begin was refused)
        }
        let (dropped, index) = self.drag.borrow().as_ref().map(|d| (d.dropped, d.folder_index)).unwrap_or((false, 0));
        let tile = self.tiles.borrow().get(key).cloned();
        match tile {
            Some(t) => self.drag_end(false, &t), // never removed from the Dock: it left a folder
            None => {
                self.drag.replace(None);
            }
        }
        if !dropped {
            // not dropped on the Dock: back into its folder
            self.back_into_folder(fkey, key, index);
            return;
        }
        let (w, fkey) = (self.weak(), fkey.to_string());
        glib::idle_add_local_once(move || {
            if let Some(d) = w.upgrade() {
                if d.folder(&fkey).is_some_and(|f| folder_apps(&f).len() <= 1) {
                    d.ungroup(&fkey);
                }
            }
        });
    }

    fn back_into_folder(&self, fkey: &str, key: &str, index: usize) {
        if self.folder(fkey).is_none() {
            self.save_order();
            return;
        }
        self.with_folder(fkey, |f| {
            let mut apps = folder_apps(f);
            if !apps.iter().any(|a| a == key) {
                apps.insert(index.min(apps.len()), key.into());
            }
            f.insert("apps".into(), json!(apps));
        });
        self.unpin_into_folder(key);
        self.folder_changed(fkey);
        self.relayout();
    }

    /// The folder's apps go back to the Dock in its place, popping out of
    /// it one after the other (Vini: no animation was wrong).
    pub fn ungroup(&self, fkey: &str) {
        let Some(f) = self.folder(fkey) else { return };
        let mut pins = self.pinned();
        let Some(mut at) = pins.iter().position(|k| k == fkey) else { return };
        pins.remove(at);
        let mut came = Vec::new();
        for k in folder_apps(&f) {
            if pins.contains(&k) {
                continue;
            }
            pins.insert(at, k.clone());
            at += 1;
            self.set_pins(pins.clone());
            let fresh = !self.tiles.borrow().contains_key(&k);
            self.add_known_tile(&k);
            if fresh {
                if let Some(t) = self.tiles.borrow().get(&k).cloned() {
                    came.push(t);
                }
            }
        }
        self.set_pins(pins);
        if let Some(Value::Object(m)) = self.cfg.borrow_mut().get_mut("folders") {
            m.remove(dock_folder::folder_id(fkey));
        }
        if self.tiles.borrow().contains_key(fkey) {
            self.remove_tile(fkey);
        }
        self.save_cfg();
        self.relayout();
        for (i, t) in came.iter().enumerate() {
            self.pop_in(t, i as u64 * UNGROUP_STAGGER_MS);
        }
    }

    /// The icon grows out of a point and fades in, overshooting a little.
    pub fn pop_in(&self, tile: &Rc<Tile>, delay: u64) {
        if !self.plate.is_mapped() {
            return;
        }
        tile.icon.set_pop(0.4);
        tile.button.set_opacity(0.0);
        let t = Rc::downgrade(tile);
        let start = move || {
            let Some(tile) = t.upgrade() else { return };
            let t2 = Rc::downgrade(&tile);
            let anim = adw::TimedAnimation::new(&tile.button, 0.0, 1.0, sonata_ui::tokens::ms(UNGROUP_MS as f64), adw::CallbackAnimationTarget::new(move |v| {
                if let Some(t) = t2.upgrade() {
                    t.icon.set_pop(0.4 + 0.6 * v);
                    t.button.set_opacity((v * 1.6).clamp(0.0, 1.0));
                }
            }));
            anim.set_easing(adw::Easing::EaseOutBack);
            let t3 = Rc::downgrade(&tile);
            anim.connect_done(move |_| {
                if let Some(t) = t3.upgrade() {
                    t.icon.set_pop(1.0);
                    t.button.set_opacity(1.0);
                }
            });
            anim.play();
            tile.fade.replace(Some(anim)); // kept alive while it runs
        };
        if delay > 0 {
            glib::timeout_add_local_once(Duration::from_millis(delay), start);
        } else {
            start();
        }
    }

    fn folder_changed(&self, fkey: &str) {
        let (f, tile) = (self.folder(fkey), self.tiles.borrow().get(fkey).cloned());
        if let (Some(f), Some(tile)) = (f, tile) {
            if let Icon::Folder(icon) = &tile.icon {
                icon.set_apps(&folder_apps(&f));
                icon.set_locked(folder_locked(&f));
            }
            tile.set_name(&folder_name(&f));
            tile.set_running(self.window_count(fkey));
        }
        self.save_cfg();
        self.relayout();
    }

    pub fn rename_folder(&self, fkey: &str, name: &str) {
        let name = name.trim();
        if name.is_empty() || !self.with_folder(fkey, |f| {
            f.insert("name".into(), json!(name));
        }) {
            return;
        }
        self.folder_changed(fkey);
    }

    /// Lock: what's inside shows only after the password (asked by the menu
    /// before unlocking -- see dock_folder::folder_menu).
    pub fn set_folder_locked(&self, fkey: &str, on: bool) {
        if !self.with_folder(fkey, |f| {
            if on {
                f.insert("locked".into(), json!(true));
            } else {
                f.remove("locked");
            }
        }) {
            return;
        }
        self.folder_changed(fkey);
    }

    /// What a dragged icon carries: the desktop id; a folder carries its
    /// name and apps (Launchpad takes it) -- not a locked one's.
    fn drag_text(&self, key: &str) -> String {
        let Some(f) = self.folder(key).filter(|f| !folder_locked(f)) else { return key.to_string() };
        let mut link = f.get("link").and_then(Value::as_str).unwrap_or("").to_string();
        if link.is_empty() {
            // Launchpad's copy stays the same folder (folder_link)
            link = sonata_desktop::folder_link::new_link();
            let l = link.clone();
            self.with_folder(key, |f| {
                f.insert("link".into(), json!(l));
            });
            self.save_cfg();
        }
        sonata_desktop::launchpad_model::encode_folder(&folder_name(&f), &folder_apps(&f), &link)
    }

    /// A folder dragged in (from Launchpad), placed where dropped. Returns
    /// its key (None: no installed apps).
    pub fn add_folder(&self, name: &str, app_ids: &[String], before: Option<&Rc<Tile>>, at: Option<(f64, f64)>, link: &str) -> Option<String> {
        let mut keys: Vec<String> = Vec::new();
        for a in app_ids {
            if !keys.contains(a) && !PERMANENT.contains(&a.as_str()) && sonata_ui::apps::lookup(a).is_some() {
                keys.push(a.clone());
            }
        }
        if keys.is_empty() {
            return None;
        }
        let fkey = self.make_folder(&keys, Some(name), None)?;
        if !link.is_empty() && !self.folders().iter().any(|(_, f)| f.get("link").and_then(Value::as_str) == Some(link)) {
            // Launchpad's: the same folder from now on
            self.with_folder(&fkey, |f| {
                f.insert("link".into(), json!(link));
            });
            self.save_cfg();
        }
        let tile = self.tiles.borrow().get(&fkey).cloned();
        if let Some(tile) = tile {
            let others: Vec<Rc<Tile>> = self.app_tiles().into_iter().filter(|t| !Rc::ptr_eq(t, &tile)).collect();
            let slot = match (before.and_then(|b| others.iter().position(|t| Rc::ptr_eq(t, b))), at) {
                (Some(i), _) => i,
                (None, Some((x, y))) => self.slot_at(x, y, Some(&tile)),
                (None, None) => others.len(),
            };
            self.move_to_slot(&tile, slot);
            self.save_order();
        }
        Some(fkey)
    }

    /// Open (or bring forward) an app from a folder.
    pub fn open_app(&self, key: &str, near: Option<&Rc<Tile>>) {
        let tile = self.tiles.borrow().get(key).cloned();
        if let Some(t) = tile {
            self.clicked(key, &t);
            return;
        }
        let running = self.windows.borrow().get(key).is_some_and(|w| !w.is_empty());
        if let (true, Some(near)) = (running, near) {
            self.clicked(key, near); // running (from its folder): forward
            return;
        }
        let Some(info) = sonata_ui::apps::lookup(key) else { return };
        if let Some(near) = near.filter(|_| self.cfg_bool("bounce")) {
            near.bounce(BOUNCE_MS);
        }
        let ctx = launch_context(near.map(|t| t.widget()).as_ref().unwrap_or(self.plate.upcast_ref()));
        if let Err(e) = launch_info(&info, &[], Some(ctx)) {
            println!("sonata2-dock: cannot launch {}: {}", info.id().unwrap_or_default(), e.message());
        }
    }

    // -- recents -------------------------------------------------------------------------------------

    fn is_recent(&self, key: &str) -> bool {
        self.cfg_bool("show_recents") && self.recent().iter().any(|k| k == key)
    }

    /// An unpinned app was used: most recent first, MAX_RECENTS kept.
    fn note_recent(&self, key: &str) {
        if self.pinned().iter().any(|k| k == key) || sonata_ui::apps::lookup(key).is_none() || self.in_folder(key) {
            return;
        }
        let mut rec: Vec<String> = self.recent().into_iter().filter(|k| k != key).collect();
        rec.insert(0, key.into());
        let dropped = rec.split_off(MAX_RECENTS.min(rec.len()));
        self.cfg.borrow_mut().insert("recent".into(), json!(rec));
        self.save_cfg();
        let pins = self.pinned();
        for k in dropped {
            if self.tiles.borrow().contains_key(&k) && !self.windows.borrow().contains_key(&k) && !pins.contains(&k) {
                self.remove_tile(&k);
            }
        }
    }

    // -- drag to reorder -------------------------------------------------------------------------------

    fn drag_begin(&self, drag: &gdk::Drag, tile: &Rc<Tile>) {
        let index = self.app_tiles().iter().position(|t| Rc::ptr_eq(t, tile)).unwrap_or(0);
        let gen = self.drag_gen.get() + 1;
        self.drag_gen.set(gen);
        self.drag.replace(Some(Drag { key: tile.key().unwrap_or_default(), index, gen, ..Default::default() }));
        tile.label.popdown();
        let size = self.icon_size() as i32;
        let pic: Option<gdk::Paintable> = match &tile.icon {
            Icon::Folder(f) => f.texture(size).map(|t| t.upcast()),
            Icon::App(_) => tile.gicon.borrow().as_ref().and_then(|g| sonata_ui::icons::paintable(&self.plate, g, size)),
        };
        // the item as macOS shows it: the icon itself, a little translucent,
        // held steady under the pointer (ui/drag.py)
        let icon = gtk::DragIcon::for_drag(drag);
        match pic {
            Some(p) => {
                let pic = gtk::Picture::for_paintable(&p);
                pic.set_size_request(size, size);
                pic.set_can_shrink(true);
                pic.set_opacity(0.78);
                icon.set_child(Some(&pic));
                drag.set_hotspot(size / 2, size / 2);
            }
            // never GTK's text drag icon (the drag's content)
            None => icon.set_child(Some(&gtk::Box::new(gtk::Orientation::Horizontal, 0))),
        }
        tile.button.add_css_class("dragging");
        self.mag_animate(0.0, MAG_OUT_MS);
    }

    fn drag_motion(&self, x: f64, y: f64) -> gdk::DragAction {
        let key = {
            let mut d = self.drag.borrow_mut();
            let Some(d) = d.as_mut() else { return gdk::DragAction::empty() };
            d.left = false;
            d.x = Some(x);
            d.y = Some(y);
            d.key.clone()
        };
        let Some(tile) = self.tiles.borrow().get(&key).cloned() else { return gdk::DragAction::MOVE };
        if !tile.button.get_visible() {
            self.set_tile_shown(&tile, true); // back over the Dock: its slot opens again
        }
        if self.over_trash(x, y) && Dock::can_uninstall(&tile) {
            // over the Trash: let go to uninstall it (macOS); the icon keeps its place meanwhile
            self.hold_over(None);
            let index = {
                let mut d = self.drag.borrow_mut();
                let d = d.as_mut().unwrap();
                d.trash = true;
                d.index
            };
            self.trash.button.add_css_class(dock_drop::HOVER);
            if self.app_tiles().iter().position(|t| Rc::ptr_eq(t, &tile)) != Some(index) {
                self.move_to_slot(&tile, index);
            }
            return gdk::DragAction::MOVE;
        }
        if let Some(d) = self.drag.borrow_mut().as_mut() {
            d.trash = false;
        }
        self.trash.button.remove_css_class(dock_drop::HOVER);
        if let Some(over) = self.folder_candidate(&tile, x, y) {
            self.hold_over(Some(over)); // over another app's middle: no reordering
            return gdk::DragAction::MOVE;
        }
        self.hold_over(None);
        let slot = self.slot_at(x, y, Some(&tile)); // other icons whose centre is before the pointer
        if self.app_tiles().iter().position(|t| Rc::ptr_eq(t, &tile)) != Some(slot) {
            self.move_to_slot(&tile, slot);
        }
        gdk::DragAction::MOVE
    }

    fn over_trash(&self, x: f64, y: f64) -> bool {
        self.trash.button.compute_bounds(&self.plate).is_some_and(|b| (b.x() as f64..=(b.x() + b.width()) as f64).contains(&x) && (b.y() as f64..=(b.y() + b.height()) as f64).contains(&y))
    }

    /// A Dock icon dropped on the Trash is uninstalled (asked first): an
    /// installed app -- not a folder, a Steam game or Sonata's own apps.
    pub fn can_uninstall(tile: &Tile) -> bool {
        let key = tile.key().unwrap_or_default();
        let Some(info) = &tile.info else { return false };
        let id = info.id().map(|s| s.to_string()).unwrap_or_default();
        // (a web app is "sonata2-…" too: deleted from Launchpad or by a drop
        // on the Trash, not from its Dock icon -- as dock.py)
        !dock_folder::is_folder(&key) && !PERMANENT.contains(&key.as_str()) && !is_protected(&key) && !is_protected(&id)
    }

    /// Dropped on the Trash (or its menu's Uninstall…): asked, then
    /// uninstalled; it leaves the Dock only once that's done (Vini).
    pub fn ask_uninstall(&self, tile: &Rc<Tile>) {
        let Some(info) = tile.info.clone() else { return };
        let (w, key) = (self.weak(), tile.key().unwrap_or_default());
        uninstall_ui::ask(&info, None, Some(Rc::new(move |ok| {
            if let (true, Some(d)) = (ok, w.upgrade()) {
                d.set_pinned(&key, false);
            }
        })));
    }

    /// The pinned tile whose middle is under the pointer, if the dragged
    /// app may make a folder with it (or go into it).
    fn folder_candidate(&self, tile: &Rc<Tile>, x: f64, y: f64) -> Option<Rc<Tile>> {
        let key = tile.key().unwrap_or_default();
        if dock_folder::is_folder(&key) || PERMANENT.contains(&key.as_str()) || tile.info.is_none() {
            return None;
        }
        for (t, (bx, by, bw, bh)) in self.drop_targets(tile) {
            let (pos, start, size) = if self.vertical { (y, by, bh) } else { (x, bx, bw) };
            let (across, a0, alen) = if self.vertical { (x, bx, bw) } else { (y, by, bh) };
            if (pos - (start + size / 2.0)).abs() <= size * FOLDER_ZONE && a0 <= across && across <= a0 + alen {
                return Some(t);
            }
        }
        None
    }

    /// Tiles a dragged app can make a folder with; measured once per layout.
    fn drop_targets(&self, tile: &Rc<Tile>) -> Vec<(Rc<Tile>, (f64, f64, f64, f64))> {
        if let Some(b) = self.drag.borrow().as_ref().and_then(|d| d.bounds.clone()) {
            return b;
        }
        let pins = self.pinned();
        let mut out = Vec::new();
        for t in self.app_tiles() {
            let k = t.key().unwrap_or_default();
            if Rc::ptr_eq(&t, tile) || !pins.contains(&k) || PERMANENT.contains(&k.as_str()) {
                continue;
            }
            if !(dock_folder::is_folder(&k) || t.info.is_some()) {
                continue;
            }
            if let Some(b) = t.button.compute_bounds(&self.plate) {
                out.push((t, (b.x() as f64, b.y() as f64, b.width() as f64, b.height() as f64)));
            }
        }
        if let Some(d) = self.drag.borrow_mut().as_mut() {
            d.bounds = Some(out.clone());
        }
        out
    }

    /// Start (or keep) the hold timer over `target`; None clears it.
    fn hold_over(&self, target: Option<Rc<Tile>>) {
        let mut db = self.drag.borrow_mut();
        let Some(d) = db.as_mut() else { return };
        let same = match (&d.over, &target) {
            (Some(a), Some(b)) => Rc::ptr_eq(a, b),
            (None, None) => true,
            _ => false,
        };
        if same {
            return;
        }
        if let Some(s) = d.hold_src.take() {
            sonata_ui::cancel(s);
        }
        if let Some(old) = d.target.take() {
            old.button.remove_css_class("folder-target");
        }
        d.over = target.clone();
        let Some(target) = target else { return };
        let (w, gen, t) = (self.weak(), d.gen, Rc::downgrade(&target));
        d.hold_src = Some(glib::timeout_add_local_once(Duration::from_millis(FOLDER_HOLD_MS), move || {
            let (Some(dock), Some(t)) = (w.upgrade(), t.upgrade()) else { return };
            let mut db = dock.drag.borrow_mut();
            let Some(d) = db.as_mut() else { return };
            d.hold_src = None;
            if d.gen == gen && d.over.as_ref().is_some_and(|o| Rc::ptr_eq(o, &t)) {
                t.button.add_css_class("folder-target");
                d.target = Some(t);
            }
        }));
    }

    fn drop_into_folder(&self, key: &str, target: &Rc<Tile>) {
        target.button.remove_css_class("folder-target");
        let tk = target.key().unwrap_or_default();
        if dock_folder::is_folder(&tk) {
            self.add_to_folder(&tk, key);
        } else {
            self.make_folder(&[tk.clone(), key.to_string()], None, Some(&tk));
        }
    }

    fn drag_leave(&self) {
        self.trash.button.remove_css_class(dock_drop::HOVER);
        if self.drag.borrow().is_none() {
            return;
        }
        self.hold_over(None);
        let key = {
            let mut d = self.drag.borrow_mut();
            let d = d.as_mut().unwrap();
            d.trash = false;
            d.left = true;
            d.key.clone()
        };
        let tile = self.tiles.borrow().get(&key).cloned();
        if let (Some(t), false) = (tile, PERMANENT.contains(&key.as_str())) {
            self.set_tile_shown(&t, false); // dragged out: the others close up (macOS)
        }
    }

    fn set_tile_shown(&self, tile: &Rc<Tile>, shown: bool) {
        let others: Vec<gtk::Widget> = self.app_tiles().iter().filter(|t| !Rc::ptr_eq(t, tile)).map(|t| t.widget()).collect();
        let before = glide_record(&self.plate, &others);
        tile.button.set_visible(shown);
        glide_play(&self.plate, before, GLIDE_MS);
    }

    fn drag_drop(&self, x: f64, y: f64) -> bool {
        let (key, target, trash) = {
            let mut d = self.drag.borrow_mut();
            let Some(d) = d.as_mut() else { return false };
            d.dropped = true;
            (d.key.clone(), d.target.clone(), d.trash)
        };
        self.hold_over(None);
        if trash {
            // on the Trash: uninstall (asked first)
            self.trash.button.remove_css_class(dock_drop::HOVER);
            let tile = self.tiles.borrow().get(&key).cloned();
            if let Some(t) = tile {
                self.settle(&t, x, y);
                self.ask_uninstall(&t);
            }
            return true;
        }
        if let Some(target) = target.filter(|t| t.key().is_some_and(|k| self.pinned().contains(&k))) {
            self.drop_into_folder(&key, &target);
            return true;
        }
        if !self.pinned().contains(&key) && self.can_pin(&key) {
            let mut p = self.pinned();
            p.push(key.clone()); // dragging a running app into place pins it
            self.set_pins(p);
        }
        self.save_order();
        let tile = self.tiles.borrow().get(&key).cloned();
        if let Some(t) = tile {
            self.settle(&t, x, y);
        }
        true
    }

    /// The dropped icon glides from under the pointer into its slot (it
    /// used to blink in place there as the drag icon vanished).
    fn settle(&self, tile: &Rc<Tile>, x: f64, y: f64) {
        if !tile.button.is_mapped() {
            return;
        }
        let (w, h) = (tile.button.width() as f64, tile.button.height() as f64);
        tile.button.remove_css_class("dragging"); // shown now: no empty frame between the two
        glide_play(&self.plate, vec![(tile.widget(), x - w / 2.0, y - h / 2.0, tile.button.parent())], SETTLE_MS);
    }

    fn drag_cancel(&self, reason: gdk::DragCancelReason, tile: &Rc<Tile>) -> bool {
        let key = tile.key().unwrap_or_default();
        let info = self.drag.borrow().as_ref().map(|d| (d.left, d.index, d.x, d.y));
        let Some((left, index, x, y)) = info else { return false };
        if reason == gdk::DragCancelReason::NoTarget && left && !PERMANENT.contains(&key.as_str()) {
            self.set_pinned(&key, false); // dragged out of the Dock: remove
            self.poof(x, y, true); // ...in a puff of smoke (macOS)
            return true; // no snap-back animation
        }
        // Esc / refused: put it back
        tile.button.set_visible(true);
        self.move_to_slot(tile, index);
        false
    }

    /// The puff where an icon was (a folder removed from its menu).
    pub fn poof_at_tile(&self, tile: &Rc<Tile>) {
        let b = if tile.button.parent().as_ref() == Some(self.plate.upcast_ref()) { tile.button.compute_bounds(&self.plate) } else { None };
        match b {
            Some(b) => self.poof(Some((b.x() + b.width() / 2.0) as f64), Some((b.y() + b.height() / 2.0) as f64), false),
            None => self.poof(None, None, false),
        }
    }

    /// (x, y) on the display for the puff: the last place along the Dock,
    /// just off its plate on the screen side. The surface spans its whole
    /// edge, so the window's corner is the display's corner along that edge.
    fn poof_spot(&self, x: Option<f64>, y: Option<f64>, mon_w: f64, mon_h: f64, size: f64) -> (f64, f64) {
        let native = self.plate.native();
        let (nw, nh) = native.as_ref().map(|n| (n.width() as f64, n.height() as f64)).unwrap_or((self.plate.width() as f64, self.plate.height() as f64));
        let (ox, oy) = match self.edge.as_str() {
            "left" => (0.0, 0.0),
            "right" => (mon_w - nw, 0.0),
            _ => (0.0, mon_h - nh),
        };
        let along = if self.vertical { y } else { x }.unwrap_or_else(|| if self.vertical { self.plate.height() } else { self.plate.width() } as f64 / 2.0);
        let pt = if self.vertical { graphene::Point::new(0.0, along as f32) } else { graphene::Point::new(along as f32, 0.0) };
        let (dx, dy) = native.as_ref().and_then(|n| self.plate.compute_point(n, &pt)).map(|p| (p.x() as f64, p.y() as f64)).unwrap_or((0.0, 0.0));
        let (x0, y0, pw, _) = self.plate_rect();
        match self.edge.as_str() {
            "bottom" => (ox + dx, oy + dy + y0 - size / 2.0),
            "left" => (ox + dx + x0 + pw + size / 2.0, oy + dy),
            _ => (ox + dx + x0 - size / 2.0, oy + dy),
        }
    }

    fn poof(&self, x: Option<f64>, y: Option<f64>, at_pointer: bool) {
        let Some(app) = self.app.borrow().clone() else {
            println!("sonata2-dock: poof: no application");
            return;
        };
        let mon = self.monitor();
        let fallback = mon.map(|m| {
            let g = m.geometry();
            let (px, py) = self.poof_spot(x, y, g.width() as f64, g.height() as f64, poof::SIZE as f64);
            (Some(m), px, py)
        });
        let shown = if at_pointer {
            poof::at_pointer(&app, fallback)
        } else if let Some((m, px, py)) = fallback {
            poof::show(&app, m.as_ref(), px, py);
            true
        } else {
            false
        };
        println!("sonata2-dock: poof {}", if shown { "shown" } else { "not shown (no display found)" });
    }

    /// The display this Dock is on: the one its window was put on (layer
    /// shell). GTK's monitor "at" the surface could name the other display
    /// (Vini: windows on the external screen flew into the laptop's Dock).
    pub fn monitor(&self) -> Option<gdk::Monitor> {
        if let Some(m) = self.shown_on.borrow().clone().filter(|m| m.is_valid()) {
            return Some(m);
        }
        let surface = self.plate.native()?.surface()?;
        self.plate.display().monitor_at_surface(&surface)
    }

    fn drag_end(&self, delete: bool, tile: &Rc<Tile>) {
        tile.button.remove_css_class("dragging");
        self.hold_over(None);
        let d = self.drag.borrow_mut().take();
        let key = tile.key().unwrap_or_default();
        // moved somewhere else that took it (Launchpad), or let go away from
        // the Dock and taken as a copy (the desktop took a folder's text):
        // out of the Dock (macOS)
        if let Some(d) = &d {
            if !d.dropped && (delete || (d.left && d.from_folder.is_none())) && !PERMANENT.contains(&key.as_str()) {
                self.set_pinned(&key, false);
                if d.left {
                    // dropped onto the desktop or another app: the puff too
                    self.poof(d.x, d.y, true);
                }
            }
        }
        // still in the Dock (dropped back, cancelled, or a running app that keeps its icon)
        let still = self.tiles.borrow().get(&key).is_some_and(|t| Rc::ptr_eq(t, tile));
        if still && !tile.button.get_visible() {
            self.set_tile_shown(tile, true);
        }
    }

    // -- running apps --------------------------------------------------------------------------------

    fn schedule_sync(&self) {
        // coalesce bursts of window events into one update
        if self.sync_src.borrow().is_some() {
            return;
        }
        let w = self.weak();
        let src = glib::idle_add_local_once(move || {
            if let Some(d) = w.upgrade() {
                d.sync_src.replace(None);
                d.sync();
            }
        });
        self.sync_src.replace(Some(src));
    }

    /// The windows now -- fresh states (minimized, in front) for `key`.
    pub fn wins(&self, key: &str) -> Vec<Toplevel> {
        let ws = self.windows.borrow().get(key).cloned().unwrap_or_default();
        match &self.manager {
            Some(m) => ws.iter().filter_map(|t| m.get(t.id)).collect(),
            None => ws,
        }
    }

    fn sync(&self) {
        let Some(m) = self.manager.clone() else { return };
        // Every event runs this, pure title changes too (a browser's tab,
        // a terminal's prompt: several a second). What depends on titles
        // alone is the dynamic names and the title badges; regrouping, the
        // dots and the relayout only when windows or their states changed.
        let tops = m.toplevels();
        let mut sig: Vec<(i64, String, bool, bool, bool)> = tops.iter().map(|t| (t.id, t.app_id.clone(), t.minimized, t.activated, t.maximized)).collect();
        sig.sort();
        let mut groups: HashMap<String, Vec<Toplevel>> = HashMap::new();
        for t in &tops {
            let key = sonata_desktop::appmatch::match_app_id(&t.app_id).unwrap_or_else(|| if t.app_id.is_empty() { "?".into() } else { t.app_id.clone() });
            groups.entry(key).or_default().push(t.clone());
        }
        if self.sync_sig.borrow().as_ref() == Some(&sig) {
            self.windows.replace(groups.clone()); // (titles)
            self.sync_titles(&groups, false);
            return;
        }
        self.sync_sig.replace(Some(sig));
        let started: Vec<String> = groups.keys().filter(|k| !self.windows.borrow().contains_key(*k)).cloned().collect();
        let changed_apps = {
            let w = self.windows.borrow();
            groups.len() != w.len() || groups.keys().any(|k| !w.contains_key(k))
        };
        if changed_apps {
            // for "Reopen Apps" after a crash (one session, one list: on
            // trial the Python Dock keeps it)
            let mut ids: Vec<String> = groups.keys().cloned().collect();
            ids.sort();
            sonata_desktop::open_apps::note(&ids);
        }
        self.windows.replace(groups.clone());
        for key in &started {
            self.note_recent(key);
        }
        let pins = self.pinned();
        let gone: Vec<String> = self.tiles.borrow().keys().filter(|k| !pins.contains(k)).cloned().collect();
        for key in gone {
            if (!groups.contains_key(&key) && !self.is_recent(&key)) || self.folder_tile_of(&key).is_some() {
                self.remove_tile(&key); // unpinned app quit (or it lives in a folder)
            }
        }
        let before: HashSet<String> = self.tiles.borrow().keys().cloned().collect();
        for (key, wins) in &groups {
            if self.tiles.borrow().contains_key(key) || self.folder_tile_of(key).is_some() {
                continue; // (Vini: it left its folder)
            }
            if !self.add_known_tile(key) {
                // no desktop entry: a Windows program (Proton, Wine), any
                // other app -- named by windowapps (logged: a window that
                // should have matched an app is easy to spot)
                println!("sonata2-dock: no app for window app_id {key:?}");
                let titles: Vec<(&str, bool)> = wins.iter().map(|t| (t.title.as_str(), t.activated)).collect();
                let (name, gicon, dynamic) = sonata_desktop::windowapps::describe(key, &sonata_desktop::windowapps::window_title(&titles));
                self.add_tile(key, &name, Some(gicon), None, None).dynamic.set(dynamic);
            }
        }
        self.sync_titles(&groups, true); // (who is in front: its badge is seen)
        let tiles: Vec<(String, Rc<Tile>)> = self.tiles.borrow().iter().map(|(k, t)| (k.clone(), t.clone())).collect();
        for (key, tile) in &tiles {
            tile.set_running(self.window_count(key));
            if groups.get(key).is_some_and(|w| !w.is_empty()) {
                self.starting.borrow_mut().remove(key); // its window showed: the launch went well
                self.retries.borrow_mut().remove(key);
            }
            if let Some(p) = tile.folder_pop.borrow().clone() {
                dock_folder::show_running(self, &p);
            }
        }
        self.relayout();
        // an app opened: its place opens smoothly, as it closes up when it
        // quits (Vini) -- not for the windows already there at the start
        if self.windows_seen.get() {
            let fresh: Vec<Rc<Tile>> = self.tiles.borrow().iter().filter(|(k, _)| !before.contains(*k)).map(|(_, t)| t.clone()).collect();
            for t in fresh {
                self.open_up(&t);
            }
        }
        self.windows_seen.set(true);
        self.rects_soon();
    }

    /// What follows the windows' titles: dynamic names, title badges.
    pub fn sync_titles(&self, groups: &HashMap<String, Vec<Toplevel>>, badges_too: bool) {
        let tiles: Vec<(String, Rc<Tile>)> = self.tiles.borrow().iter().map(|(k, t)| (k.clone(), t.clone())).collect();
        for (key, tile) in &tiles {
            if let (true, Some(wins)) = (tile.dynamic.get(), groups.get(key)) {
                let titles: Vec<(&str, bool)> = wins.iter().map(|t| (t.title.as_str(), t.activated)).collect();
                let name = sonata_desktop::windowapps::describe(key, &sonata_desktop::windowapps::window_title(&titles)).0;
                if name != tile.name() {
                    tile.set_name(&name);
                }
            }
        }
        let pages = sonata_desktop::badges::page_titles(None);
        let mut counts = HashMap::new();
        for (k, wins) in groups {
            if wins.iter().any(|t| sonata_desktop::badges::counted_title(&t.app_id, k)) {
                let pairs: Vec<(&str, &str)> = wins.iter().map(|t| (t.app_id.as_str(), t.title.as_str())).collect();
                counts.insert(k.clone(), sonata_desktop::badges::title_count(&sonata_desktop::badges::window_titles(&pairs, Some(&pages))));
            }
        }
        if badges_too || counts != *self.title_counts.borrow() {
            // (a title change is usually not a count's)
            self.title_counts.replace(counts);
            self.refresh_badges();
        }
    }

    fn ipc_event(&self, msg: &Value) {
        match dock_menu::scale_event(msg) {
            dock_menu::ScaleEvent::Menu => {
                // a window right-clicked in Mission Control: only the first Dock answers
                if docks().first().is_some_and(|d| std::ptr::eq(&**d, self)) {
                    dock_menu::scale_menu(self, msg);
                }
            }
            dock_menu::ScaleEvent::Closed => dock_menu::close_menu_at(), // its menu goes with Mission Control
            dock_menu::ScaleEvent::Other => {}
        }
    }

    // -- minimize targets (wlr.rs) ----------------------------------------------------------

    /// Minimize targets again once the icons settle (not while magnified:
    /// a zoomed icon is bigger and elsewhere than where it comes back to).
    pub fn rects_soon(&self) {
        if !crate::wlr::active() {
            return;
        }
        if let Some(src) = self.rects_src.take() {
            sonata_ui::cancel(src);
        }
        let w = self.weak();
        let src = glib::timeout_add_local(Duration::from_millis(150), move || {
            let Some(d) = w.upgrade() else { return glib::ControlFlow::Break };
            if d.mag_strength.get() > 0.0 {
                return glib::ControlFlow::Continue; // zoomed icons: wait until they settle
            }
            d.rects_src.replace(None);
            d.update_rectangles();
            glib::ControlFlow::Break
        });
        self.rects_src.replace(Some(src));
    }

    /// The icon's place in the Dock's surface. The rectangle is relative to
    /// the surface, and Wayfire adds only the Dock's place *on its display*
    /// (foreign-toplevel.cpp) -- the display's own coordinates, the ones the
    /// genie runs in: nothing to take out (taking the display's layout
    /// origin out aimed every genie that far off).
    fn icon_rect(&self, tile: &Tile) -> Option<(i32, i32, i32, i32)> {
        let native = self.plate.native()?;
        let b = tile.button.compute_bounds(native.upcast_ref::<gtk::Widget>())?;
        Some((b.x().round() as i32, b.y().round() as i32, b.width().round() as i32, b.height().round() as i32))
    }

    /// Tell the compositor where each window minimizes to (its Dock icon;
    /// an app in a folder: the folder).
    pub fn update_rectangles(&self) {
        if !crate::wlr::active() {
            return;
        }
        if self.mag_strength.get() > 0.0 {
            self.rects_soon();
            return;
        }
        let Some(surface) = self.plate.native().and_then(|n| n.surface()) else { return };
        let mine = self.monitor().and_then(|m| crate::wlr::output_of(&m));
        let icon = |app_id: &str| {
            let key = sonata_desktop::appmatch::match_app_id(app_id).unwrap_or_else(|| app_id.to_string());
            let tile = self.tiles.borrow().get(&key).cloned().or_else(|| self.folder_tile_of(&key))?;
            self.icon_rect(&tile)
        };
        for (id, rect) in crate::wlr::plan(&crate::wlr::handles(), mine, docks().len() > 1, &icon) {
            crate::wlr::set_rectangle(id, &surface, rect);
        }
    }

    /// These windows minimize to / come back from `tile` of this Dock.
    fn aim_at(&self, tile: &Tile, wins: &[Toplevel]) {
        if !crate::wlr::active() || wins.is_empty() {
            return;
        }
        let (Some(surface), Some(rect)) = (self.plate.native().and_then(|n| n.surface()), self.icon_rect(tile)) else { return };
        let pairs: HashSet<(String, String)> = wins.iter().map(|t| (t.app_id.clone(), t.title.clone())).collect();
        for h in crate::wlr::handles() {
            if pairs.contains(&(h.app_id.clone(), h.title.clone())) {
                crate::wlr::set_rectangle(h.id, &surface, rect);
            }
        }
    }

    /// The windows of `wins` on this Dock's display (all of them when
    /// Wayfire can't tell, or with a single Dock).
    fn here(&self, wins: &[Toplevel]) -> Vec<Toplevel> {
        if docks().len() <= 1 {
            return wins.to_vec();
        }
        let mine = self.monitor().map(|m| sonata_shell::layer::connector(&m)).unwrap_or_default();
        let views = sonata_backend::wfipc::call("window-rules/list-views", json!({}));
        let (here, placed) = windows_here(views.as_ref(), &mine);
        if placed.is_none() {
            return wins.to_vec();
        }
        wins.iter().filter(|t| here.contains(&(t.app_id.clone(), t.title.clone()))).cloned().collect()
    }

    /// Minimized windows of an app that are on another display move to
    /// this Dock's (Wayfire keeps their place on the screen). True when
    /// any moved.
    fn bring_here(&self, wins: &[Toplevel]) -> bool {
        let mine = self.monitor().map(|m| sonata_shell::layer::connector(&m)).unwrap_or_default();
        if mine.is_empty() {
            return false;
        }
        let (Some(Value::Array(outputs)), Some(Value::Array(views))) = (sonata_backend::wfipc::call("window-rules/list-outputs", json!({})), sonata_backend::wfipc::call("window-rules/list-views", json!({}))) else { return false };
        let Some(out_id) = outputs.iter().find(|o| o["name"] == mine.as_str()).and_then(|o| o.get("id").cloned()) else { return false };
        let wanted: HashSet<(String, String)> = wins.iter().map(|t| (t.app_id.clone(), t.title.clone())).collect();
        let mut moved = false;
        for v in &views {
            if !matches!(v.get("type").and_then(Value::as_str), None | Some("toplevel")) {
                continue;
            }
            let pair = (v.get("app-id").and_then(Value::as_str).unwrap_or("").to_string(), v.get("title").and_then(Value::as_str).unwrap_or("").to_string());
            if !wanted.contains(&pair) {
                continue;
            }
            let out = v.get("output-name").and_then(Value::as_str).unwrap_or("");
            if !out.is_empty() && out != mine && v.get("minimized").and_then(Value::as_bool).unwrap_or(true) {
                sonata_backend::wfipc::call("window-rules/configure-view", json!({"id": v["id"], "output_id": out_id}));
                moved = true;
            }
        }
        moved
    }

    pub fn clicked(&self, key: &str, tile: &Rc<Tile>) {
        if dock_folder::is_folder(key) {
            dock_folder::open_panel(self, tile, None, false, false);
            return;
        }
        let wins = self.wins(key);
        let over_launchpad = self.above.get() && key != LAUNCHPAD_KEY;
        if over_launchpad {
            close_launchpad(); // macOS: Launchpad goes, the app comes forward
        }
        if !wins.is_empty() {
            let Some(m) = self.manager.clone() else { return };
            let shown: Vec<Toplevel> = wins.iter().filter(|t| !t.minimized).cloned().collect();
            if self.cfg_bool("click_minimizes") && shown.iter().any(|t| t.activated) && !over_launchpad {
                // the app in front: clicking its icon minimizes its windows (Vini)
                for d in docks() {
                    if !std::ptr::eq(&*d, self) {
                        d.update_rectangles(); // aimed from the display each window is on now
                    }
                }
                let here = self.here(&shown);
                // this Dock is magnified under the pointer, so its general update
                // would wait until after the minimize and the windows flew to the
                // old target, the other display's icon (Vini): the clicked icon now
                self.aim_at(tile, &here);
                // every shown window goes, on every display (only the aim is
                // this display's): with a Dock on each display, the app's
                // windows on the other one stayed up
                for t in &shown {
                    m.minimize(t);
                }
                return;
            }
            // macOS: bring all of the app's windows forward; if every window
            // is minimized, restore them. The newest window ends up focused.
            // Restored from a Dock on another display: they come to this one
            if shown.is_empty() && docks().len() > 1 && self.bring_here(&wins) {
                // aimed at this icon directly: the general update waits while the
                // Dock is magnified, and the old aim came out of the other
                // display's icon (Vini)
                self.aim_at(tile, &wins);
                glib::timeout_add_local_once(Duration::from_millis(BRING_MS), move || {
                    for t in &wins {
                        m.activate(t);
                    }
                });
                return;
            }
            for t in if shown.is_empty() { &wins } else { &shown } {
                m.activate(t);
            }
        } else if tile.info.is_some() {
            self.launch(tile);
        } else if let Some(aid) = sonata_desktop::steamgames::appid(key) {
            // a Steam game kept in the Dock: start it again
            self.launch_feedback(tile);
            if !sonata_desktop::steamgames::launch(&aid) {
                tile.stop_bounce();
            }
        }
    }

    /// Bounces until the first window maps (set_running), LAUNCH_MAX_MS at most.
    pub fn launch_feedback(&self, tile: &Rc<Tile>) {
        let key = tile.key().unwrap_or_default();
        if !NO_BOUNCE.contains(&key.as_str()) && self.cfg_bool("bounce") {
            tile.bounce(LAUNCH_MAX_MS);
        }
    }

    /// Open the app. Clicked again while the last launch never showed a
    /// window (Vini: Spotify started with Chrome stayed running, its window
    /// never shown): that launch is stopped and the app opened afresh.
    pub fn launch(&self, tile: &Rc<Tile>) {
        let Some(info) = tile.info.clone() else { return };
        let key = tile.key().unwrap_or_default();
        let id = info.id().map(|s| s.to_string()).unwrap_or_default();
        if key == LAUNCHPAD_KEY && !tile.spawn.get() {
            // resident: told over D-Bus (its launcher started a whole Sonata
            // process, ~0.5 s before Launchpad began to open); not running: launched
            let (w, t) = (self.weak(), Rc::downgrade(tile));
            launchpad_action("toggle", Some(Box::new(move || {
                if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                    t.spawn.set(true);
                    d.launch(&t);
                    t.spawn.set(false);
                }
            })));
            return;
        }
        let since = self.starting.borrow().get(&key).copied();
        if let Some(since) = since {
            let has = self.windows.borrow().get(&key).is_some_and(|w| !w.is_empty());
            if !NO_BOUNCE.contains(&key.as_str()) && stuck(since.elapsed().as_secs_f64(), has) && !scope_has_window(sonata_desktop::appscope::launched(&id).as_deref()) && sonata_desktop::appscope::stop(&id) {
                println!("sonata2-dock: {key} never showed a window: stopped, opened again");
                self.starting.borrow_mut().remove(&key);
                self.launch_feedback(tile);
                let (w, t) = (self.weak(), Rc::downgrade(tile));
                glib::timeout_add_local_once(Duration::from_millis(RELAUNCH_MS), move || {
                    if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                        d.launch(&t);
                    }
                });
                return;
            }
        }
        self.starting.borrow_mut().insert(key.clone(), Instant::now());
        self.launch_feedback(tile);
        if let Err(e) = launch_info(&info, &[], Some(launch_context(&tile.button))) {
            tile.stop_bounce();
            println!("sonata2-dock: cannot launch {id}: {}", e.message());
            self.retries.borrow_mut().remove(&key);
            sonata_desktop::launchwatch::failed(Some(info.upcast_ref()), e.message());
            return;
        }
        self.watch_launch(tile);
    }

    /// No window of it a while after the launch: its processes killed and
    /// it opened again (launchwatch.RETRIES times), then a notification (Vini).
    fn watch_launch(&self, tile: &Rc<Tile>) {
        let key = tile.key().unwrap_or_default();
        if NO_BOUNCE.contains(&key.as_str()) {
            return;
        }
        let Some(info) = tile.info.clone() else { return };
        let started = self.starting.borrow().get(&key).copied();
        let wait = sonata_desktop::launchwatch::wait_s(Some(info.upcast_ref()));
        let (w, t) = (self.weak(), Rc::downgrade(tile));
        glib::timeout_add_seconds_local_once(wait, move || {
            let (Some(d), Some(tile)) = (w.upgrade(), t.upgrade()) else { return };
            if d.starting.borrow().get(&key).copied() != started || d.windows.borrow().get(&key).is_some_and(|w| !w.is_empty()) {
                return; // it showed a window, or was clicked again since
            }
            let app_id = info.id().map(|s| s.to_string()).unwrap_or_default();
            if scope_has_window(sonata_desktop::appscope::launched(&app_id).as_deref()) || class_has_window(&info) {
                d.starting.borrow_mut().remove(&key); // its window is under another icon: it's open
                d.retries.borrow_mut().remove(&key);
                return;
            }
            let tries = d.retries.borrow().get(&key).copied().unwrap_or(0) + 1;
            d.starting.borrow_mut().remove(&key);
            sonata_desktop::appscope::kill(&app_id, Some(info.upcast_ref()), std::path::Path::new("/proc")); // forced closed, then (maybe) again (Vini)
            if tries > sonata_desktop::launchwatch::RETRIES {
                d.retries.borrow_mut().remove(&key);
                tile.stop_bounce();
                let why = sonata_i18n::fmt(&tr!("It was closed and opened again {n} times and still didn't show a window."), &[("n", &sonata_desktop::launchwatch::RETRIES.to_string())]);
                sonata_desktop::launchwatch::failed(Some(info.upcast_ref()), &why);
                return;
            }
            d.retries.borrow_mut().insert(key.clone(), tries);
            println!("sonata2-dock: {key} showed no window in {wait} s: killed, opening it again ({tries}/{})", sonata_desktop::launchwatch::RETRIES);
            let (w, t) = (d.weak(), Rc::downgrade(&tile));
            glib::timeout_add_local_once(Duration::from_millis(RELAUNCH_MS), move || {
                if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                    d.launch(&t);
                }
            });
        });
    }

    // -- Trash ---------------------------------------------------------------------------------------

    fn update_trash(&self) {
        // what the Trash shows, not leftovers
        let full = !sonata_files::ops::trash_items().is_empty();
        self.trash.set_gicon(gio::ThemedIcon::new(if full { "user-trash-full" } else { "user-trash" }).upcast());
    }

    /// Every app's icon again (its choice or shape changed in Settings).
    pub fn refresh_icons(&self) {
        let tiles: Vec<(String, Rc<Tile>)> = self.tiles.borrow().iter().map(|(k, t)| (k.clone(), t.clone())).collect();
        for (key, tile) in tiles {
            match (&tile.icon, &tile.info) {
                (Icon::Folder(f), _) => {
                    if let Some(fd) = self.folder(&key) {
                        f.set_apps(&folder_apps(&fd)); // its minis, and its frame's shape
                    }
                }
                (_, Some(info)) => tile.set_gicon(sonata_ui::icons::app_icon(info.upcast_ref())),
                _ => {}
            }
        }
    }

    /// Light/Dark: redraw every icon (the Trash and Launchpad have one
    /// version per appearance) and the Dock's own drawing.
    fn appearance_changed(&self) {
        // once it has settled, every widget anew (dock.py): a count badge
        // kept its Light Mode colour in Dark Mode until clicked (Vini)
        let plate = self.plate.downgrade();
        sonata_ui::theme::settled(&self.plate, 150, move || {
            if let Some(p) = plate.upgrade() {
                sonata_ui::theme::refresh_tree(&p);
            }
        });
        let pad = pad_side().round() as i32;
        for b in [self.plate.first_child(), self.plate.last_child()].into_iter().flatten() {
            if b.has_css_class("dock-end") {
                if self.vertical {
                    b.set_size_request(-1, pad);
                } else {
                    b.set_size_request(pad, -1);
                }
            }
        }
        for t in self.all_tiles() {
            t.icon.widget().queue_draw();
        }
        self.plate.queue_draw();
    }

    fn watch_trash(&self) {
        self.update_trash();
        let path = sonata_files::ops::home_trash().join("files");
        let _ = std::fs::create_dir_all(&path);
        if let Ok(mon) = gio::File::for_path(&path).monitor_directory(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE) {
            let w = self.weak();
            mon.connect_changed(move |_, _, _, _| {
                if let Some(d) = w.upgrade() {
                    d.update_trash();
                }
            });
            self.monitors.borrow_mut().push(mon);
        }
    }

    fn menu_closed(&self) {
        let cb = self.on_menu_closed.borrow().clone();
        if let Some(cb) = cb {
            cb();
        }
    }
}

thread_local! {
    static IDS: Cell<u64> = const { Cell::new(1) };
}

fn next_id() -> u64 {
    IDS.with(|i| {
        let n = i.get();
        i.set(n + 1);
        n
    })
}

/// Every Dock's minimize targets again (windows came, went or moved display).
pub fn rects_all_soon() {
    for d in docks() {
        d.rects_soon();
    }
}

pub fn is_protected(id: &str) -> bool {
    // apps.PROTECTED: Sonata's own apps are never uninstalled
    id.starts_with("io.github.vinioliveiras.sonata2.") || id.starts_with("sonata2-")
}

pub fn folder_apps(f: &Map<String, Value>) -> Vec<String> {
    list(f, "apps")
}

pub fn folder_name(f: &Map<String, Value>) -> String {
    gets(f, "name")
}

pub fn folder_locked(f: &Map<String, Value>) -> bool {
    f.get("locked").is_some_and(truthy)
}

// -- CSS, config ---------------------------------------------------------------------------------

pub const CSS: &str = r#"
window.sonata-dock, window.sonata-dock > contents { background: none; box-shadow: none; }
/* no GTK drop-target outline (the dashed frame while dragging) */
window.sonata-dock *:drop(active) { box-shadow: none; outline: none; border-color: transparent; }
.dock-tile, .dock-tile:hover, .dock-tile:active, .dock-tile:focus {
  padding: 0 %(tile_pad)dpx; margin: 0; min-width: 0; min-height: 0;
  border: none; border-radius: 0; background: none; box-shadow: none; outline: none;
  /* Adwaita's buttons animate "all": on the first Light/Dark switch its new
     style sheet made every tile's padding glide -- the Dock stretched sideways */
  transition: none;
}
.edge-left .dock-tile, .edge-right .dock-tile { padding: %(tile_pad)dpx 0; }
.dock-icon { transition: filter %(t_press)s ease-out, transform 160ms cubic-bezier(0.2, 0.8, 0.2, 1); }
/* an app held over another: they'll make a folder (Launchpad) */
.dock-tile.folder-target .dock-icon { transform: scale(1.18); filter: brightness(0.85); }
.dock-tile:active .dock-icon, .dock-tile.drop-hover .dock-icon { filter: brightness(0.62); }
.dock-tile.dragging { opacity: 0; }   /* keeps its gap while being dragged */
.dock-dot { min-width: %(dot)dpx; min-height: %(dot)dpx; border-radius: 99px;
            background-color: %(indicator)s; opacity: 0; transition: opacity %(t_fast)s; }
.edge-bottom .dock-dots { margin: %(dot_in)dpx 0 %(dot_out)dpx 0; }
.edge-left .dock-dots { margin: 0 %(dot_in)dpx 0 %(dot_out)dpx; }
.edge-right .dock-dots { margin: 0 %(dot_out)dpx 0 %(dot_in)dpx; }
.dock-dot.on { opacity: %(dot_on)s; }
.dock-divider > box, .dock-recent-sep > box { background-color: %(separator)s; }
.edge-bottom .dock-divider, .edge-bottom .dock-recent-sep { padding: 0 5px; margin-bottom: %(row)dpx; }
.edge-bottom .dock-divider > box, .edge-bottom .dock-recent-sep > box { min-width: 1px; }
.edge-left .dock-divider, .edge-left .dock-recent-sep { padding: 5px 0; margin-left: %(row)dpx; }
.edge-right .dock-divider, .edge-right .dock-recent-sep { padding: 5px 0; margin-right: %(row)dpx; }
.edge-left .dock-divider > box, .edge-right .dock-divider > box,
.edge-left .dock-recent-sep > box, .edge-right .dock-recent-sep > box { min-height: 1px; }

/* a ball under gravity (macOS): a parabola, quick off the Dock, slowing
   to the top, falling faster back down (GTK ignores per-keyframe curves:
   the arc is sampled, played linearly) */
@keyframes dock-bounce-up {
  0%% { transform: none; }
  10%% { transform: translateY(-6.48px); }
  20%% { transform: translateY(-11.52px); }
  30%% { transform: translateY(-15.12px); }
  40%% { transform: translateY(-17.28px); }
  50%% { transform: translateY(-18.0px); }
  60%% { transform: translateY(-17.28px); }
  70%% { transform: translateY(-15.12px); }
  80%% { transform: translateY(-11.52px); }
  90%% { transform: translateY(-6.48px); }
  100%% { transform: none; } }
@keyframes dock-bounce-right {
  0%% { transform: none; }
  10%% { transform: translateX(6.48px); }
  20%% { transform: translateX(11.52px); }
  30%% { transform: translateX(15.12px); }
  40%% { transform: translateX(17.28px); }
  50%% { transform: translateX(18.0px); }
  60%% { transform: translateX(17.28px); }
  70%% { transform: translateX(15.12px); }
  80%% { transform: translateX(11.52px); }
  90%% { transform: translateX(6.48px); }
  100%% { transform: none; } }
@keyframes dock-bounce-left {
  0%% { transform: none; }
  10%% { transform: translateX(-6.48px); }
  20%% { transform: translateX(-11.52px); }
  30%% { transform: translateX(-15.12px); }
  40%% { transform: translateX(-17.28px); }
  50%% { transform: translateX(-18.0px); }
  60%% { transform: translateX(-17.28px); }
  70%% { transform: translateX(-15.12px); }
  80%% { transform: translateX(-11.52px); }
  90%% { transform: translateX(-6.48px); }
  100%% { transform: none; } }
.edge-bottom .dock-tile.launching .dock-icon { animation: dock-bounce-up %(bounce_ms)dms linear infinite; }
.edge-left .dock-tile.launching .dock-icon { animation: dock-bounce-right %(bounce_ms)dms linear infinite; }
.edge-right .dock-tile.launching .dock-icon { animation: dock-bounce-left %(bounce_ms)dms linear infinite; }
"#;

/// dock.py's CSS (computed in Python, so not exported) and the CSS of the
/// modules it uses.
pub fn load_css(cfg: &Map<String, Value>) {
    let size = geti(cfg, "icon_size");
    let (inner, outer) = dot_gaps(size);
    let local: sonata_ui::tokens::Values = [
        ("tile_pad", TILE_PAD.to_string()),
        ("row", dot_row(size).to_string()),
        ("bounce_ms", BOUNCE_MS.to_string()),
        ("dot", DOT.to_string()),
        ("dot_in", inner.to_string()),
        ("dot_out", outer.to_string()),
        ("dot_on", if getb(cfg, "indicators") { "1" } else { "0" }.to_string()),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect();
    sonata_ui::register_with("dock", CSS, local);
    sonata_ui::register("hover-label", LABEL_CSS);
    for m in ["shell/dock_folder.py", "shell/dock_stack.py", "shell/dock_preview.py", "shell/poof.py"] {
        sonata_ui::css::register_module(m);
    }
}

pub fn default_pins() -> Vec<String> {
    const SLOTS: [&[&str]; 10] = [
        &["io.github.vinioliveiras.sonata2.files", "org.gnome.Nautilus", "nautilus", "org.kde.dolphin", "nemo", "thunar", "pcmanfm-qt", "pcmanfm"],
        &["@browser"],
        &["org.gnome.Evolution", "org.mozilla.Thunderbird", "thunderbird", "geary", "org.gnome.Geary"],
        &["org.gnome.Calendar", "org.kde.merkuro.calendar"],
        &["org.gnome.Music", "rhythmbox", "org.gnome.Rhythmbox3", "elisa", "org.kde.elisa", "spotify"],
        &["io.github.vinioliveiras.sonata2.preview", "org.gnome.Loupe", "org.gnome.eog", "eog", "org.kde.gwenview", "gwenview"],
        &["io.github.vinioliveiras.sonata2.textedit", "org.gnome.TextEditor", "org.gnome.gedit", "org.kde.kate", "mousepad", "code", "code-oss"],
        &["steam"],
        &["io.github.vinioliveiras.sonata2.terminal", "org.gnome.Console", "org.gnome.Terminal", "com.mitchellh.ghostty", "kitty", "Alacritty", "foot", "org.kde.konsole", "xfce4-terminal"],
        &["io.github.vinioliveiras.sonata2.feedback"], // last: Feedbacker (can be removed)
    ];
    let mut pins: Vec<String> = Vec::new();
    for slot in SLOTS {
        for cand in slot {
            let did = if *cand == "@browser" { gio::AppInfo::default_for_uri_scheme("https").and_then(|i| i.id()).map(|s| s.to_string()).unwrap_or_default() } else { cand.to_string() };
            if !did.is_empty() && sonata_ui::apps::lookup(&did).is_some() {
                let did = did.strip_suffix(".desktop").unwrap_or(&did).to_string();
                if !pins.contains(&did) {
                    pins.push(did);
                }
                break;
            }
        }
    }
    pins
}

pub fn load_config() -> Map<String, Value> {
    let mut cfg = sonata_config::load("dock", &defaults());
    if fix_config(&mut cfg, default_pins, || json!(dock_stack::default_stacks()), |k| sonata_ui::apps::lookup(k).is_some()) {
        let _ = sonata_config::save("dock", &cfg);
    }
    cfg
}

/// Shadow room along the edge; the plate floats edge_gap px off the edge.
pub fn apply_margins(dock: &Dock) {
    let gap = dock.cfg_int("edge_gap") as i32;
    let p = &dock.plate;
    match dock.edge.as_str() {
        "bottom" => {
            p.set_margin_start(SHADOW as i32);
            p.set_margin_end(SHADOW as i32);
            p.set_margin_bottom(gap);
        }
        e => {
            p.set_margin_top(SHADOW as i32);
            p.set_margin_bottom(SHADOW as i32);
            if e == "left" {
                p.set_margin_start(gap);
            } else {
                p.set_margin_end(gap);
            }
        }
    }
}

// -- the window --------------------------------------------------------------------------------

/// Layer surface spanning the Dock's screen edge, thick enough for
/// magnified icons; only the Dock itself receives clicks (input region).
/// Auto-hide: the Dock slides out; a TRIGGER px strip at the edge (along
/// the Dock) brings it back after autohide_delay_ms.
pub struct DockWindow {
    me: Weak<DockWindow>,
    pub win: gtk::ApplicationWindow,
    pub cfg: Cfg,
    pub manager: Option<Rc<Manager>>,
    pub dock: RefCell<Option<Rc<Dock>>>,
    pub layer: Cell<bool>,
    monitor: Option<gdk::Monitor>,
    hidden: Cell<bool>,
    hide_anim: RefCell<Option<adw::TimedAnimation>>,
    timer: RefCell<Option<glib::SourceId>>,
    inside: Cell<bool>,
    above: Cell<bool>,
    last_exclusive: Cell<Option<i32>>,
    last_wh: Cell<(i32, i32)>,
    input_src: RefCell<Option<glib::SourceId>>,
    monitors: RefCell<Vec<gio::FileMonitor>>,
    namespace: String,
    exclusive_ok: bool,
}

pub const REBUILD_KEYS: [&str; 2] = ["position", "show_recents"];
pub const LIVE_KEYS: [&str; 13] = ["icon_size", "magnification", "magnified_size", "position", "autohide", "autohide_delay_ms", "show_recents", "glass", "edge_gap", "window_gap", "indicators", "bounce", "click_minimizes"];

/// The live keys dock.json changed (the chosen size compared, not the fit).
pub fn changed_keys(new: &Map<String, Value>, cur: &Map<String, Value>, user_size: i64) -> Vec<&'static str> {
    LIVE_KEYS
        .iter()
        .copied()
        .filter(|k| {
            let c = if *k == "icon_size" { json!(user_size) } else { cur.get(*k).cloned().unwrap_or(Value::Null) };
            new.get(*k).cloned().unwrap_or(Value::Null) != c
        })
        .collect()
}

impl DockWindow {
    /// monitor=None: the main display, following it; else that display
    /// (the extra Docks of "Show the Dock on every display").
    pub fn new(app: &gtk::Application, cfg: Cfg, manager: Option<Rc<Manager>>, monitor: Option<gdk::Monitor>) -> Rc<DockWindow> {
        // resizable: lets layer-shell stretch the surface along the edge
        let win = gtk::ApplicationWindow::builder().application(app).title("Dock").css_classes(["sonata-dock"]).decorated(false).resizable(true).build();
        // On trial the Python Dock owns the edge: this one never reserves
        // the space again and takes a namespace of its own (Wayfire's rules
        // and the plugin key on "sonata2-dock").
        let dw = Rc::new_cyclic(|me| DockWindow {
            me: me.clone(),
            win,
            cfg,
            manager,
            dock: RefCell::default(),
            layer: Cell::new(false),
            monitor,
            hidden: Cell::new(false),
            hide_anim: RefCell::default(),
            timer: RefCell::default(),
            inside: Cell::new(false),
            above: Cell::new(false),
            last_exclusive: Cell::new(None),
            last_wh: Cell::new((0, 0)),
            input_src: RefCell::default(),
            monitors: RefCell::default(),
            namespace: "sonata2-dock".into(),
            exclusive_ok: true,
        });
        let motion = gtk::EventControllerMotion::new();
        let w = dw.me.clone();
        motion.connect_enter(move |_, _, _| {
            if let Some(d) = w.upgrade() {
                d.pointer(true);
            }
        });
        let w = dw.me.clone();
        motion.connect_leave(move |_| {
            if let Some(d) = w.upgrade() {
                d.pointer(false);
            }
        });
        dw.win.add_controller(motion);
        // right-click on the Dock itself -- the strip under it, its plate
        // between the icons -- like Windows' taskbar (Vini)
        let area = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
        let w = dw.me.clone();
        area.connect_pressed(move |g, _, x, y| {
            if let Some(d) = w.upgrade() {
                d.area_pressed(g, x, y);
            }
        });
        dw.win.add_controller(area);
        let mut mons = sonata_config::watch("dock", weak_do!(dw.me, d => d.config_changed()));
        // Settings > App Icons: new icons / shapes, live
        mons.extend(sonata_config::watch("icons", weak_do!(dw.me, d => { if let Some(x) = d.dock() { x.refresh_icons() } })));
        mons.extend(sonata_config::watch("applock", weak_do!(dw.me, d => { if let Some(x) = d.dock() { x.refresh_locks() } })));
        dw.monitors.replace(mons);
        sonata_desktop::sandbox::add_listener(weak_do!(dw.me, d => { if let Some(x) = d.dock() { x.refresh_sandboxes() } }));
        let w = dw.me.clone();
        dw.win.connect_realize(move |win| {
            let Some(surface) = win.surface() else { return };
            let w = w.clone();
            surface.connect_layout(move |_, width, height| {
                if let Some(d) = w.upgrade() {
                    d.surface_resized(width, height);
                }
            });
        });
        let w = dw.me.clone();
        dw.win.connect_destroy(move |_| {
            if let Some(d) = w.upgrade() {
                d.forget();
            }
        });
        dw.rebuild();
        if sonata_shell::intro::entering() && !getb(&dw.cfg.borrow(), "autohide") {
            // login: out of sight until the welcome screen fades, then it
            // slides in from its edge like a hidden Dock showing
            if let Some(d) = dw.dock() {
                d.hide_amount.set(1.0);
                d.plate.queue_draw();
            }
            intro_wait(weak_do!(dw.me, d => d.slide(false)), Some("dock"));
        }
        // restart: the old Dock slides away (SIGUSR1's default would end
        // the process on the spot, the Dock just gone)
        leave_on_signal(weak_do!(dw.me, d => d.slide(true)));
        dw
    }

    pub fn dock(&self) -> Option<Rc<Dock>> {
        self.dock.borrow().clone()
    }

    fn forget(&self) {
        for m in self.monitors.borrow_mut().drain(..) {
            m.cancel();
        }
        if let Some(d) = self.dock() {
            d.detach();
        }
    }

    pub fn destroy(&self) {
        self.forget();
        self.win.destroy();
    }

    pub fn rebuild(&self) {
        if let Some(d) = self.dock() {
            d.detach();
        }
        let position = gets(&self.cfg.borrow(), "position");
        let edge = if EDGES.contains(&position.as_str()) { position } else { "bottom".to_string() };
        let dock = Dock::new(self.cfg.clone(), self.manager.clone());
        dock.app.replace(self.win.application());
        dock.on_rebuild.replace(Some(Rc::new(weak_do!(self.me, w => w.rebuild()))));
        dock.on_geometry.borrow_mut().push(Rc::new(weak_do!(self.me, w => w.geometry())));
        dock.on_menu_closed.replace(Some(Rc::new(weak_do!(self.me, w => w.pointer(w.inside.get())))));
        apply_margins(&dock);
        self.win.set_child(Some(&dock.plate));
        dock.span_host.replace(Some(self.win.clone().upcast()));
        self.dock.replace(Some(dock.clone()));
        let win: &gtk::Window = self.win.upcast_ref();
        if !self.layer.get() {
            let on = sonata_shell::layer::anchor_edge(win, &self.namespace, &edge, self.exclusive());
            self.layer.set(on);
            if on {
                // on the main display; follows it
                if let Some(m) = self.monitor.clone().or_else(sonata_shell::layer::main_monitor) {
                    sonata_shell::layer::set_monitor(win, &m);
                    dock.shown_on.replace(Some(m));
                }
            }
        } else {
            sonata_shell::layer::set_edge(win, &edge, self.exclusive());
            dock.shown_on.replace(self.monitor.clone().or_else(sonata_shell::layer::main_monitor));
        }
        self.hidden.set(false);
        dock.hide_amount.set(0.0);
        self.geometry();
        if getb(&self.cfg.borrow(), "autohide") {
            let w = self.me.clone();
            glib::timeout_add_local_once(Duration::from_millis(600), move || {
                if let Some(d) = w.upgrade() {
                    d.pointer(d.inside.get());
                }
            });
        }
    }

    /// The main display changed (Settings, or it was unplugged).
    pub fn move_to(&self, monitor: &gdk::Monitor) {
        let here = self.dock().and_then(|d| d.shown_on.borrow().clone());
        if self.monitor.is_some() || !self.layer.get() || here.as_ref() == Some(monitor) {
            return;
        }
        let visible = self.win.is_visible();
        self.win.set_visible(false);
        sonata_shell::layer::set_monitor(self.win.upcast_ref(), monitor);
        if let Some(d) = self.dock() {
            d.shown_on.replace(Some(monitor.clone()));
        }
        self.win.set_visible(visible);
    }

    /// dock.json changed (Settings app): apply appearance/behaviour keys.
    /// The Dock's own writes (pins, recents) don't touch these, so no loop.
    fn config_changed(&self) {
        let new = sonata_config::load("dock", &defaults());
        let d = self.dock();
        // folders first: another display's Dock made / changed one. Without
        // this the pins below saw "folder:N" as unknown, dropped it and took
        // its apps out (Vini: making a folder made the apps vanish)
        if let (Some(d), Some(Value::Object(folders))) = (&d, new.get("folders")) {
            if self.cfg.borrow().get("folders") != Some(&Value::Object(folders.clone())) {
                self.cfg.borrow_mut().insert("folders".into(), Value::Object(folders.clone()));
                let tiles: Vec<(String, Rc<Tile>)> = d.tiles.borrow().iter().map(|(k, t)| (k.clone(), t.clone())).collect();
                for (key, tile) in tiles {
                    if let (Some(f), Icon::Folder(icon)) = (d.folder(&key), &tile.icon) {
                        icon.set_apps(&folder_apps(&f));
                        icon.set_locked(folder_locked(&f));
                        tile.set_name(&folder_name(&f));
                    }
                }
            }
        }
        // "Keep in Dock" from Launchpad writes pinned: add/remove those tiles.
        // Nothing written back from here -- two Docks answering each other's
        // saves lost a new folder
        let new_pins = list(&new, "pinned");
        if let Some(d) = &d {
            if new.get("pinned").is_some_and(|p| !p.is_null()) && new_pins != d.pinned() {
                let gone: Vec<String> = d.pinned().into_iter().filter(|k| !new_pins.contains(k)).collect();
                for key in &new_pins {
                    if !d.tiles.borrow().contains_key(key) {
                        d.add_known_tile(key);
                    }
                }
                d.set_pins(new_pins.clone());
                for key in gone {
                    let has = d.tiles.borrow().contains_key(&key);
                    if has && !PERMANENT.contains(&key.as_str()) && !d.windows.borrow().contains_key(&key) && !d.is_recent(&key) {
                        d.remove_tile(&key);
                    }
                }
                d.relayout();
            }
            // Settings > Dock's folders (a folder removed by mistake brought back: Vini)
            if let Some(Value::Array(specs)) = new.get("stacks") {
                let paths = |a: &[Value]| a.iter().map(|x| x.get("path").cloned()).collect::<Vec<_>>();
                let cur = self.cfg.borrow().get("stacks").and_then(Value::as_array).cloned().unwrap_or_default();
                if paths(specs) != paths(&cur) {
                    d.stacks.sync(specs);
                    d.relayout();
                }
            }
        }
        let user = d.as_ref().map(|d| d.user_size.get()).unwrap_or_else(|| geti(&self.cfg.borrow(), "icon_size"));
        let changed = changed_keys(&new, &self.cfg.borrow(), user);
        if changed.is_empty() {
            return;
        }
        for k in &changed {
            if *k != "icon_size" {
                self.cfg.borrow_mut().insert(k.to_string(), new.get(*k).cloned().unwrap_or(Value::Null));
            }
        }
        if changed.contains(&"icon_size") {
            match &d {
                Some(d) => d.set_icon_size(geti(&new, "icon_size") as f64, false, true), // the chosen size; the fit still applies
                None => {
                    self.cfg.borrow_mut().insert("icon_size".into(), new["icon_size"].clone());
                }
            }
        }
        load_css(&self.cfg.borrow());
        if d.is_none() || changed.iter().any(|k| REBUILD_KEYS.contains(k)) {
            self.rebuild();
            return;
        }
        // everything else applies to the live Dock (it shares the cfg): no
        // rebuild, so dragging a Settings slider stays smooth
        let d = d.unwrap();
        d.mag_strength.set(0.0);
        d.hint_sizes();
        d.apply_magnification();
        d.update_thickness();
        d.plate.queue_resize();
        d.plate.queue_draw();
        self.geometry();
    }

    /// Maximized windows stop above the Dock by the same space the Dock
    /// keeps from the screen edge (one gap, both sides of the plate).
    fn exclusive(&self) -> i32 {
        let c = self.cfg.borrow();
        if getb(&c, "autohide") || !self.exclusive_ok {
            0
        } else {
            (plate_height(geti(&c, "icon_size")) + 2 * geti(&c, "edge_gap")) as i32
        }
    }

    fn thickness(&self) -> i32 {
        let c = self.cfg.borrow();
        let s = geti(&c, "icon_size");
        (SHADOW + PAD_TOP + max_icon(&c) + dot_row(s) + geti(&c, "edge_gap")) as i32
    }

    pub fn set_launcher_open(&self, on: bool) {
        if let Some(d) = self.dock() {
            d.set_launcher_open(on);
        }
    }

    /// Over Launchpad (OVERLAY) while it is open, TOP otherwise (fullscreen
    /// apps cover the Dock).
    pub fn set_above(&self, on: bool) {
        use gtk4_layer_shell::{Layer, LayerShell};
        self.above.set(on); // auto-hide waits while Launchpad is open
        if let Some(d) = self.dock() {
            d.above.set(on);
        }
        if self.layer.get() {
            if on && self.win.layer() == Some(Layer::Overlay) {
                // already there, but Launchpad was raised over it (Wayfire raises
                // the surface you press on): changing layer again puts the Dock on top
                self.win.set_layer(Layer::Top);
                let win = self.win.clone();
                glib::timeout_add_local_once(Duration::from_millis(16), move || win.set_layer(Layer::Overlay));
            } else {
                self.win.set_layer(if on { Layer::Overlay } else { Layer::Top });
            }
        }
        if on && self.hidden.get() {
            self.slide(false); // auto-hide: show it while Launchpad is open
        } else if !on && getb(&self.cfg.borrow(), "autohide") {
            self.pointer(self.inside.get());
        }
    }

    fn surface_resized(&self, w: i32, h: i32) {
        // the screen edge's length (login, display change): the Dock's own
        // allocation may not change when only this surface grows, so fit here
        if self.last_wh.get() != (w, h) {
            self.last_wh.set((w, h));
            if let Some(d) = self.dock() {
                d.refit_soon();
            }
        }
        self.input_soon();
    }

    fn input_soon(&self) {
        if self.input_src.borrow().is_some() {
            return;
        }
        let w = self.me.clone();
        let src = glib::idle_add_local_once(move || {
            if let Some(d) = w.upgrade() {
                d.input_src.replace(None);
                d.update_input();
            }
        });
        self.input_src.replace(Some(src));
    }

    fn geometry(&self) {
        // Fixed surface thickness = biggest possible Dock; changes only with
        // the Dock size/magnification setting, never while magnifying.
        let t = self.thickness();
        if ["left", "right"].contains(&gets(&self.cfg.borrow(), "position").as_str()) {
            self.win.set_size_request(t, -1);
        } else {
            self.win.set_size_request(-1, t);
        }
        if self.layer.get() {
            let ex = self.exclusive();
            if self.last_exclusive.get() != Some(ex) {
                // not every magnified frame: Wayfire re-lays out windows on it
                self.last_exclusive.set(Some(ex));
                use gtk4_layer_shell::LayerShell;
                self.win.set_exclusive_zone(ex);
            }
            self.input_soon();
        }
        let autohide = getb(&self.cfg.borrow(), "autohide");
        if !autohide && self.hidden.get() {
            self.slide(false);
        } else if autohide && !self.hidden.get() && !self.inside.get() {
            self.pointer(false);
        }
    }

    /// The Dock's own menu, unless an icon or the divider (menus of their
    /// own) is under the pointer.
    fn area_pressed(&self, g: &gtk::GestureClick, x: f64, y: f64) {
        let Some(d) = self.dock() else { return };
        if on_an_item(self.win.pick(x, y, gtk::PickFlags::DEFAULT)) {
            return;
        }
        g.set_state(gtk::EventSequenceState::Claimed);
        // anchored to the Dock (a menu on the window itself never showed),
        // at the pointer -- under the plate too, in the strip
        let at = self.win.compute_point(&d.plate, &graphene::Point::new(x as f32, y as f32)).map(|p| (p.x() as f64, p.y() as f64)).unwrap_or((x, y));
        dock_menu::area_menu(&d, self, at);
    }

    fn update_input(&self) {
        if !self.layer.get() {
            return;
        }
        let Some(d) = self.dock() else { return };
        let Some(b) = d.plate.compute_bounds(&self.win) else { return };
        let (mut x, mut y, mut w, mut h) = (b.x() as f64, b.y() as f64, b.width() as f64, b.height() as f64);
        let (ww, hh) = (self.win.width() as f64, self.win.height() as f64);
        if self.hidden.get() {
            // a thin strip at the screen edge, along the Dock
            (x, y, w, h) = match d.edge.as_str() {
                "left" => (0.0, y, TRIGGER, h),
                "right" => (ww - TRIGGER, y, TRIGGER, h),
                _ => (x, hh - TRIGGER, w, TRIGGER),
            };
        } else {
            // down to the screen edge: the gap under the Dock is still "the
            // Dock" (auto-hide would flicker there)
            match d.edge.as_str() {
                "left" => {
                    w += x;
                    x = 0.0;
                }
                "right" => w = ww - x,
                _ => h = hh - y,
            }
        }
        sonata_shell::layer::set_input_region(self.win.upcast_ref(), &[(x, y, w, h)]);
    }

    // -- auto-hide ---------------------------------------------------------------------------

    fn pointer(&self, inside: bool) {
        self.inside.set(inside);
        let (autohide, delay) = {
            let c = self.cfg.borrow();
            (getb(&c, "autohide"), geti(&c, "autohide_delay_ms"))
        };
        if !autohide {
            return;
        }
        if let Some(t) = self.timer.borrow_mut().take() {
            sonata_ui::cancel(t);
        }
        // a menu, a drag from the Dock, or Launchpad open (dragging an app
        // out of it moves the pointer "out" of the Dock): it stays
        let busy = menus_open() || self.dock().is_some_and(|d| d.drag.borrow().is_some()) || self.above.get();
        let w = self.me.clone();
        if inside && self.hidden.get() {
            let src = glib::timeout_add_local_once(Duration::from_millis(delay.max(0) as u64), move || {
                if let Some(d) = w.upgrade() {
                    d.timer.replace(None);
                    d.slide(false);
                }
            });
            self.timer.replace(Some(src));
        } else if !inside && !self.hidden.get() && !busy {
            let src = glib::timeout_add_local_once(Duration::from_millis(400), move || {
                if let Some(d) = w.upgrade() {
                    d.timer.replace(None);
                    d.slide(true);
                }
            });
            self.timer.replace(Some(src));
        }
    }

    fn slide(&self, hide: bool) {
        self.hidden.set(hide);
        let Some(dock) = self.dock() else { return };
        if let Some(a) = self.hide_anim.borrow_mut().take() {
            a.pause();
        }
        let w = Rc::downgrade(&dock);
        let anim = adw::TimedAnimation::new(&self.win, dock.hide_amount.get(), if hide { 1.0 } else { 0.0 }, sonata_ui::tokens::ms(HIDE_MS as f64), adw::CallbackAnimationTarget::new(move |v| {
            if let Some(d) = w.upgrade() {
                d.hide_amount.set(v);
                d.plate.queue_draw();
            }
        }));
        anim.set_easing(adw::Easing::EaseInOutCubic);
        anim.play();
        self.hide_anim.replace(Some(anim));
        self.update_input();
    }
}

/// never keep the Dock hidden longer than this (intro.TIMEOUT_S)
const INTRO_TIMEOUT_S: u32 = 12;
/// after a restart: from all ready to sliding in (first frames drawn)
const ARRIVE_MS: u64 = 120;
/// never wait longer than this for the other restarted one
const RESTART_TIMEOUT_S: u32 = 6;

/// callback() once the intro is over (right away without one), as
/// intro.wait. After a restart: once every restarted component is ready --
/// the Dock and the menu bar slide in together; `name` tells restart this
/// one is (without the ready file restart waited 5 s for it).
fn intro_wait(callback: impl Fn() + 'static, name: Option<&str>) {
    if sonata_shell::intro::pending() {
        when_gone(sonata_shell::intro::mark(), INTRO_TIMEOUT_S, Box::new(callback));
    } else if sonata_shell::intro::restarting() {
        if let Some(n) = name {
            announce_ready(n);
        }
        let cb = Rc::new(callback);
        when_gone(sonata_shell::intro::restart_mark(), RESTART_TIMEOUT_S, Box::new(move || {
            let cb = cb.clone();
            glib::timeout_add_local_once(Duration::from_millis(ARRIVE_MS), move || cb());
        }));
    } else {
        glib::idle_add_local_once(callback);
    }
}

/// This component is ready after a restart (intro.ready_file).
fn announce_ready(name: &str) {
    let _ = std::fs::File::create(sonata_shell::intro::ready_file(name));
}

/// callback() once `path` is deleted, or after timeout_s.
fn when_gone(path: std::path::PathBuf, timeout_s: u32, callback: Box<dyn Fn()>) {
    let cb: Rc<RefCell<Option<Box<dyn Fn()>>>> = Rc::new(RefCell::new(Some(callback)));
    let fire = {
        let cb = cb.clone();
        move || {
            if let Some(f) = cb.borrow_mut().take() {
                f();
            }
        }
    };
    let fire = Rc::new(fire);
    if !path.exists() {
        // removed before the monitor existed: no event will come
        glib::idle_add_local_once(move || fire());
        return;
    }
    if let Ok(mon) = gio::File::for_path(&path).monitor_file(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE) {
        let f = fire.clone();
        mon.connect_changed(move |_, _, _, ev| {
            if ev == gio::FileMonitorEvent::Deleted {
                f();
            }
        });
        // kept alive until it fires
        let keep = RefCell::new(Some(mon));
        let f = fire.clone();
        glib::timeout_add_seconds_local_once(timeout_s, move || {
            f();
            if let Some(m) = keep.borrow_mut().take() {
                m.cancel();
            }
        });
    } else {
        glib::timeout_add_seconds_local_once(timeout_s, move || fire());
    }
}

thread_local! {
    static LEAVING: RefCell<Vec<Rc<dyn Fn()>>> = const { RefCell::new(Vec::new()) };
}

/// callback() when `sonata2 restart` asks this component to go (SIGUSR1,
/// intro.leave_on_signal): it slides away before the new one comes.
fn leave_on_signal(callback: impl Fn() + 'static) {
    LEAVING.with(|l| {
        if l.borrow().is_empty() {
            glib::unix_signal_add_local(libc::SIGUSR1, || {
                let all: Vec<Rc<dyn Fn()>> = LEAVING.with(|l| l.borrow().clone());
                for cb in all {
                    cb();
                }
                glib::ControlFlow::Continue
            });
        }
        l.borrow_mut().push(Rc::new(callback));
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    /// test_merge_keeps_untiled_in_place: a pinned app without a tile
    /// (not found yet at login) kept its place -- it used to be dropped and
    /// came back at the end: the Dock's order "shuffled".
    #[test]
    fn test_merge_keeps_untiled_in_place() {
        assert_eq!(merge_order(&s(&["a", "b", "c", "d"]), &s(&["a", "c", "d"])), s(&["a", "b", "c", "d"]));
        assert_eq!(merge_order(&s(&["a", "b", "c"]), &s(&["c", "a"])), s(&["c", "b", "a"]));
        // dragged into place: new ones at the end
        assert_eq!(merge_order(&s(&["a", "b"]), &s(&["b", "a", "x"])), s(&["b", "a"]));
        assert_eq!(merge_order(&s(&["a", "a", "b"]), &s(&["a", "b"])), s(&["a", "b"]));
    }

    /// Apps opened from the Dock (and Launchpad, which shares launch_info)
    /// skipped Sonata's launch path: "Use Discrete Graphics", Steam's
    /// arguments and Settings > Apps' limits did nothing for them.
    #[test]
    fn test_dock_launch_skipped_sonatas_launch_path() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        static CALLS: AtomicUsize = AtomicUsize::new(0);
        fn fake(_: &gio::AppInfo, _: &[gio::File], _: Option<&gio::AppLaunchContext>) -> Result<(), glib::Error> {
            CALLS.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }
        sonata_ui::apps::set_launcher(fake);
        let kf = glib::KeyFile::new();
        kf.load_from_data("[Desktop Entry]\nType=Application\nName=X\nExec=true\n", glib::KeyFileFlags::NONE).unwrap();
        let info = gio::DesktopAppInfo::from_keyfile(&kf).unwrap();
        launch_info(&info, &[], None).unwrap();
        assert_eq!(CALLS.load(Ordering::SeqCst), 1);
    }

    /// Vini: after a Light/Dark switch a Dock count badge kept its Light
    /// Mode colour until clicked -- appearance_changed only redrew the
    /// icons and the plate, never the rest (dock.py: settled + refresh_tree).
    #[test]
    fn test_dock_badge_kept_light_look_after_dark_switch() {
        let src = include_str!("dock.rs");
        let f = &src[src.find("fn appearance_changed(&self)").unwrap()..];
        let f = &f[..f.find("\n    }\n").unwrap()];
        assert!(f.contains("sonata_ui::theme::settled(&self.plate, 150,"), "{f}");
        assert!(f.contains("sonata_ui::theme::refresh_tree(&p)"), "{f}");
    }

    /// With a Dock on every display, clicking the app in front minimized
    /// only its windows on the clicked display; the others stayed up
    /// (dock.py minimizes every shown window).
    #[test]
    fn test_click_left_other_displays_windows_up() {
        let src = include_str!("dock.rs");
        let f = &src[src.find("pub fn clicked(").unwrap()..];
        let f = &f[..f.find("\n    }\n").unwrap()];
        assert!(f.contains("for t in &shown {\n                    m.minimize(t);"));
    }

    /// `sonata2 restart` waited ~5 s for the Dock: it never left its ready
    /// file (intro.wait(name="dock")), and SIGUSR1 ended it instead of
    /// sliding it away.
    #[test]
    fn test_restart_waited_for_a_dock_never_ready() {
        let f = sonata_shell::intro::ready_file("dock");
        let _ = std::fs::remove_file(&f);
        announce_ready("dock");
        assert!(f.exists());
        let _ = std::fs::remove_file(&f);
        let src = include_str!("dock.rs");
        let new = &src[src.find("        dw.rebuild();").unwrap()..];
        let new = &new[..new.find("\n        dw\n").unwrap()];
        assert!(new.contains("Some(\"dock\"))"));
        assert!(new.contains("leave_on_signal("));
    }

    /// The Dock's radius was rounded half away from zero (18.5 -> 19)
    /// where Python's round gives 18, and appearance.json was read again on
    /// every frame of the magnification.
    #[test]
    fn test_dock_radius_rounded_unlike_python() {
        let m = |v: Value| v.as_object().unwrap().clone();
        assert_eq!(radius_of(&m(json!({"radius": {"dock": 18.5}}))), 18.0);
        assert_eq!(radius_of(&m(json!({"radius": {"dock": 99}}))), 32.0);
        assert_eq!(radius_of(&m(json!({"radius": {"dock": true}}))), RADIUS_DEFAULT);
        assert_eq!(radius_of(&Map::new()), RADIUS_DEFAULT);
        assert_eq!(dock_radius(), dock_radius());
    }

    #[test]
    fn plate_metrics() {
        assert_eq!(dot_gaps(48), (0, 4)); // 4 px of artwork margin at 48 px
        assert_eq!(dot_gaps(24), (2, 4));
        assert_eq!(plate_height(48), 5 + 48 + 8);
        let mut cfg = defaults().as_object().unwrap().clone();
        assert_eq!(reserved(&cfg), 4 + 10 + 48 + 8);
        cfg.insert("autohide".into(), json!(true));
        assert_eq!(reserved(&cfg), 0);
        assert_eq!(max_icon(&cfg), 48);
        cfg.insert("magnification".into(), json!(true));
        assert_eq!(max_icon(&cfg), 80);
        assert_eq!(corner_extra(26.0, 18.0), 4.0);
        assert_eq!(corner_extra(10.0, 18.0), 0.0);
    }

    #[test]
    fn a_full_dock_shrinks_its_icons() {
        // room for 10 icons of 48 + 4 along 560 px; 20 icons: half the size
        assert_eq!(fit_size(0.0, 10, 48, false, 4.0), 48); // no edge (a preview)
        assert_eq!(fit_size(1000.0, 10, 48, false, 4.0), 48);
        let small = fit_size(600.0, 20, 48, false, 4.0);
        assert!(small < 48 && small >= MIN_SIZE, "{small}");
        assert_eq!(fit_size(100.0, 50, 48, true, 4.0), MIN_SIZE);
        assert!(cells_room(1000.0, 36.0, false, 4.0) > 24.0);
    }

    /// test_magnification_wave: the icon under the pointer grows the most,
    /// its neighbours less, the far ones not at all.
    #[test]
    fn test_magnification_wave() {
        let lines = vec![0.0; 9];
        let cell = 52.0;
        let span = 2.0 * 4.0 + 9.0 * cell;
        let centre_of_4 = 4.0 + 4.0 * cell + cell / 2.0;
        let sizes = wave(48.0, 80.0, 1.0, centre_of_4, span, &lines, 4.0);
        assert!((sizes[4] - 80.0).abs() < 1e-9);
        assert!(sizes[3] < sizes[4] && sizes[3] > 48.0 && (sizes[3] - sizes[5]).abs() < 1e-9);
        assert_eq!(sizes[0], 48.0);
        let half = wave(48.0, 80.0, 0.5, centre_of_4, span, &lines, 4.0);
        assert!((half[4] - 64.0).abs() < 1e-9);
    }

    #[test]
    fn badges_pop_only_when_they_grow() {
        assert_eq!(badge_n("99+"), 100);
        assert_eq!(badge_n("7"), 7);
        assert_eq!(badge_n(""), 0);
    }

    /// test_stuck_launch: clicked again 4..90 s after a launch that never
    /// showed a window: that copy is stopped and the app opened afresh.
    #[test]
    fn test_stuck_launch_is_restarted_only_in_its_window() {
        assert!(!stuck(1.0, false)); // a slow start: still starting
        assert!(stuck(10.0, false));
        assert!(!stuck(10.0, true));
        assert!(!stuck(200.0, false)); // long since started: never stopped
    }

    #[test]
    fn config_filled_the_first_time() {
        let mut cfg = defaults().as_object().unwrap().clone();
        assert!(fix_config(&mut cfg, || s(&["files", "firefox"]), || json!([]), |_| true));
        assert_eq!(list(&cfg, "pinned"), s(&["sonata2-launchpad", "files", "firefox"]));
        assert!(!fix_config(&mut cfg, || panic!(), || panic!(), |_| true));
        // Files taken out stays out; Launchpad comes back first
        cfg.insert("pinned".into(), json!(["firefox"]));
        assert!(fix_config(&mut cfg, || panic!(), || panic!(), |_| true));
        assert_eq!(list(&cfg, "pinned"), s(&["sonata2-launchpad", "firefox"]));
    }

    /// test_files_and_launchpad_stay: Launchpad can't leave the Dock.
    #[test]
    fn test_launchpad_is_permanent() {
        assert!(PERMANENT.contains(&LAUNCHPAD_KEY));
        assert!(!PERMANENT.contains(&FILES_KEY)); // Files can leave (Vini)
    }

    #[test]
    fn show_desktop_hides_then_brings_back_what_it_hid() {
        let (min, act, hidden) = show_desktop_plan(&[(1, false), (2, true), (3, false)], &[]);
        assert_eq!((min, act, hidden.clone()), (vec![1, 3], vec![], vec![1, 3]));
        // all hidden now: Super+D again restores them, the front one last
        let (min, act, hidden) = show_desktop_plan(&[(1, true), (2, true), (3, true)], &hidden);
        assert_eq!((min, act, hidden), (vec![], vec![3, 1], vec![]));
        assert_eq!(show_desktop_plan(&[], &[]), (vec![], vec![], vec![]));
    }

    /// test_only_windows_on_this_display / test_ipc_silent_aims_nothing.
    #[test]
    fn test_only_windows_on_this_display() {
        let views = json!([
            {"app-id": "a", "title": "A", "output-name": "eDP-1", "type": "toplevel"},
            {"app-id": "b", "title": "B", "output-name": "HDMI-A-1", "type": "toplevel"},
            {"app-id": "c", "title": "C", "output-name": "", "type": "toplevel"},
            {"app-id": "d", "title": "D", "output-name": "eDP-1", "type": "panel"}
        ]);
        let (mine, all) = windows_here(Some(&views), "eDP-1");
        assert_eq!(mine, [("a".to_string(), "A".to_string())].into_iter().collect());
        assert_eq!(all.unwrap().len(), 2);
        assert!(windows_here(None, "eDP-1").1.is_none());
        assert!(windows_here(Some(&views), "").1.is_none());
    }

    #[test]
    fn live_keys_compare_the_chosen_size() {
        let cur = defaults().as_object().unwrap().clone();
        let mut new = cur.clone();
        assert!(changed_keys(&new, &cur, 48).is_empty());
        // the fit shrank the shown size: not a change of the chosen one
        assert!(changed_keys(&new, &cur, 48).is_empty());
        new.insert("icon_size".into(), json!(60));
        new.insert("autohide".into(), json!(true));
        assert_eq!(changed_keys(&new, &cur, 48), vec!["icon_size", "autohide"]);
        // pins and recents are the Dock's own writes: never "live" keys
        new.insert("pinned".into(), json!(["x"]));
        assert_eq!(changed_keys(&new, &cur, 60), vec!["autohide"]);
    }

    #[test]
    fn sonatas_own_apps_are_never_uninstalled() {
        assert!(is_protected("io.github.vinioliveiras.sonata2.files"));
        assert!(is_protected("sonata2-launchpad"));
        assert!(!is_protected("firefox"));
    }
}
