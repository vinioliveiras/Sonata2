//! gtk4-layer-shell helpers (shell/layer.py). The library must be loaded
//! before libwayland-client, so the process is started again once with
//! LD_PRELOAD (ensure_preload). Without layer-shell (no Wayland, GNOME,
//! previews) surfaces fall back to plain windows.

use gtk::prelude::*;
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use std::sync::atomic::{AtomicBool, Ordering};

const PRELOAD_FLAG: &str = "SONATA2_PRELOADED";
const ORIG_PRELOAD: &str = "SONATA2_ORIG_LD_PRELOAD";
const LIB: &str = "libgtk4-layer-shell.so.0";

fn lib_installed() -> bool {
    ["/usr/lib", "/usr/lib64", "/usr/local/lib", "/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu"].iter().any(|d| std::path::Path::new(d).join(LIB).exists())
}

/// Start this process again with libgtk4-layer-shell preloaded. After
/// that the environment is put back, so apps started from the shell don't
/// inherit the preload or the flag (an inherited flag would skip the
/// preload there and turn every shell surface into a plain window).
pub fn ensure_preload() {
    if std::env::var_os(PRELOAD_FLAG).is_some() {
        std::env::remove_var(PRELOAD_FLAG);
        match std::env::var(ORIG_PRELOAD) {
            Ok(orig) if !orig.is_empty() => std::env::set_var("LD_PRELOAD", orig),
            _ => std::env::remove_var("LD_PRELOAD"),
        }
        std::env::remove_var(ORIG_PRELOAD);
        return;
    }
    if std::env::var_os("WAYLAND_DISPLAY").is_none() || !lib_installed() {
        return;
    }
    let orig = std::env::var("LD_PRELOAD").unwrap_or_default();
    let preload = [LIB, orig.as_str()].iter().filter(|s| !s.is_empty()).copied().collect::<Vec<_>>().join(" ");
    let Ok(exe) = std::env::current_exe() else { return };
    use std::os::unix::process::CommandExt;
    let err = std::process::Command::new(exe).args(std::env::args_os().skip(1)).env(PRELOAD_FLAG, "1").env(ORIG_PRELOAD, &orig).env("LD_PRELOAD", preload).exec();
    eprintln!("sonata2-rs: layer-shell preload: {err}");
}

static REPORTED: AtomicBool = AtomicBool::new(false);

/// Say once, on stderr, why shell surfaces fall back to plain windows.
fn why(reason: &str) {
    if !REPORTED.swap(true, Ordering::Relaxed) {
        let env = |k: &str| std::env::var(k).unwrap_or_default();
        eprintln!("sonata2-rs: layer-shell unavailable ({reason}); using plain windows [WAYLAND_DISPLAY={}, GDK_BACKEND={}, LD_PRELOAD={}]", env("WAYLAND_DISPLAY"), env("GDK_BACKEND"), env("LD_PRELOAD"));
    }
}

/// Layer surfaces can be made on this display.
pub fn available() -> bool {
    let Some(display) = gtk::gdk::Display::default() else { return false };
    if !display.backend().is_wayland() {
        why("the display is not Wayland"); // X11 / previews
        return false;
    }
    if !gtk4_layer_shell::is_supported() {
        why("compositor/preload: is_supported() is false");
        return false;
    }
    true
}

fn anchor_all(win: &gtk::Window) {
    for e in [Edge::Top, Edge::Bottom, Edge::Left, Edge::Right] {
        win.set_anchor(e, true);
    }
}

/// Full-screen surface above everything (Launchpad, the welcome screen)
/// that takes the keyboard; ignores other surfaces' exclusive zones.
/// False = a plain window.
pub fn overlay_fullscreen(win: &gtk::Window, namespace: &str) -> bool {
    if !available() {
        return false;
    }
    win.init_layer_shell();
    win.set_namespace(Some(namespace));
    win.set_layer(Layer::Overlay);
    anchor_all(win);
    win.set_exclusive_zone(-1);
    win.set_keyboard_mode(KeyboardMode::Exclusive);
    true
}

/// The whole display, under the windows (the wallpaper).
pub fn background(win: &gtk::Window, namespace: &str, keyboard: bool) -> bool {
    if !available() {
        return false;
    }
    win.init_layer_shell();
    win.set_namespace(Some(namespace));
    win.set_layer(Layer::Background);
    anchor_all(win);
    win.set_exclusive_zone(-1);
    win.set_keyboard_mode(if keyboard { KeyboardMode::OnDemand } else { KeyboardMode::None });
    true
}

/// A layer surface spanning the whole `edge` ("bottom", "left", "right"),
/// so its content can grow (magnification) without resizing the surface.
pub fn anchor_edge(win: &gtk::Window, namespace: &str, edge: &str, exclusive: i32) -> bool {
    if !available() {
        return false;
    }
    win.init_layer_shell();
    win.set_namespace(Some(namespace));
    win.set_layer(Layer::Top);
    win.set_keyboard_mode(KeyboardMode::None);
    set_edge(win, edge, exclusive);
    true
}

pub fn set_edge(win: &gtk::Window, edge: &str, exclusive: i32) {
    let main = match edge {
        "left" => Edge::Left,
        "right" => Edge::Right,
        _ => Edge::Bottom,
    };
    let across = if main == Edge::Bottom { [Edge::Left, Edge::Right] } else { [Edge::Top, Edge::Bottom] };
    for e in [Edge::Top, Edge::Bottom, Edge::Left, Edge::Right] {
        win.set_anchor(e, e == main || across.contains(&e));
    }
    win.set_exclusive_zone(exclusive);
}

pub fn set_monitor(win: &gtk::Window, monitor: &gtk::gdk::Monitor) {
    if win.is_layer_window() {
        win.set_monitor(Some(monitor));
    }
}

pub fn set_keyboard(win: &gtk::Window, on: bool) {
    if win.is_layer_window() {
        win.set_keyboard_mode(if on { KeyboardMode::Exclusive } else { KeyboardMode::None });
    }
}

/// Only these (x, y, w, h) rectangles of `win` receive pointer input; the
/// rest of the transparent surface lets clicks through.
pub fn set_input_region(win: &gtk::Window, rects: &[(f64, f64, f64, f64)]) {
    let Some(surface) = win.surface() else { return };
    let region = gtk::cairo::Region::create();
    for (x, y, w, h) in rects {
        let _ = region.union_rectangle(&gtk::cairo::RectangleInt::new(*x as i32, *y as i32, *w as i32 + 1, *h as i32 + 1));
    }
    surface.set_input_region(&region);
}

// -- prewarm: a hidden surface drawn once, unseen (shell/layer.py's prewarm) ---------------------

type Restore = (gtk::glib::WeakRef<gtk::Window>, Option<KeyboardMode>, bool, Option<Box<dyn FnOnce()>>);

thread_local! {
    /// The windows being warmed up now: the keyboard mode each had, whether
    /// it took clicks (the OSD never does), and what puts its content back.
    static WARMING: std::cell::RefCell<Vec<Restore>> = const { std::cell::RefCell::new(Vec::new()) };
}

/// Draw the hidden `win` once, invisibly, `delay_ms` from now: its first
/// real opening then doesn't pay for building the widgets' render nodes,
/// loading icons and fonts and compiling the GPU shaders (Vini: the first
/// Alt+Tab, the first volume key and the first Notification Center after
/// login lagged). Mapped at 1 % opacity, taking no keyboard or clicks, and
/// hidden again after `frames` frames; before() / after() put the content
/// in its "open" state and back. Nothing happens if it opened meanwhile;
/// an opening asked for during the warm-up calls end_prewarm first.
pub fn prewarm(win: &gtk::Window, delay_ms: u64, frames: u32, before: Option<Box<dyn FnOnce()>>, after: Option<Box<dyn FnOnce()>>) {
    let win = win.clone();
    gtk::glib::timeout_add_local_once(std::time::Duration::from_millis(delay_ms), move || {
        if win.is_visible() || warming(&win) {
            return; // opened already: nothing to do
        }
        // the mode it had (the switcher's exclusive, the OSD's none,
        // Notification Center's on-demand) comes back, not one for all
        let keyboard = win.is_layer_window().then(|| win.keyboard_mode());
        if win.is_layer_window() {
            win.set_keyboard_mode(KeyboardMode::None);
        }
        if let Some(b) = before {
            b();
        }
        WARMING.with(|w| w.borrow_mut().push((win.downgrade(), keyboard, win.can_target(), after)));
        win.set_opacity(0.01);
        win.set_can_target(false);
        win.present();
        set_input_region(&win, &[]);
        let n = std::cell::Cell::new(0u32);
        win.add_tick_callback(move |w, _| {
            if !warming(w) {
                return gtk::glib::ControlFlow::Break; // opened for real meanwhile
            }
            n.set(n.get() + 1);
            if n.get() < frames.max(1) {
                return gtk::glib::ControlFlow::Continue;
            }
            end_prewarm(w);
            gtk::glib::ControlFlow::Break
        });
    });
}

/// `win` is being drawn for a warm-up now.
pub fn warming(win: &gtk::Window) -> bool {
    WARMING.with(|w| {
        let mut w = w.borrow_mut();
        w.retain(|(x, ..)| x.upgrade().is_some());
        w.iter().any(|(x, ..)| x.upgrade().as_ref() == Some(win))
    })
}

/// The warm-up of `win` over (if it is in one): hidden, opaque, taking
/// clicks as it did (on its whole surface) and its keyboard mode back,
/// its content put back. An opening asked for meanwhile calls this first, then opens
/// as ever (otherwise it would open at 1 % opacity, deaf to clicks).
pub fn end_prewarm(win: &gtk::Window) {
    let entry = WARMING.with(|w| {
        let mut w = w.borrow_mut();
        let i = w.iter().position(|(x, ..)| x.upgrade().as_ref() == Some(win))?;
        Some(w.remove(i))
    });
    let Some((_, keyboard, target, after)) = entry else { return };
    win.set_visible(false);
    win.set_opacity(1.0);
    win.set_can_target(target);
    if let Some(k) = keyboard {
        win.set_keyboard_mode(k);
    }
    if let Some(surface) = win.surface() {
        // the whole surface takes clicks again
        let all = gtk::cairo::Region::create_rectangle(&gtk::cairo::RectangleInt::new(0, 0, i32::MAX / 2, i32::MAX / 2));
        surface.set_input_region(&all);
    }
    if let Some(a) = after {
        a(); // (outside the list's borrow: it may open something)
    }
}

/// The displays connected now.
pub fn monitors() -> Vec<gtk::gdk::Monitor> {
    let Some(display) = gtk::gdk::Display::default() else { return Vec::new() };
    let list = display.monitors();
    (0..list.n_items()).filter_map(|i| list.item(i).and_downcast::<gtk::gdk::Monitor>()).collect()
}

pub fn connector(m: &gtk::gdk::Monitor) -> String {
    m.connector().map(|c| c.to_string()).unwrap_or_default()
}

/// The main display (shell/monitors.py): the one chosen in Settings, else
/// an external monitor when one is connected (Vini), else the built-in panel.
pub fn pick_main(connectors: &[String], chosen: &str) -> Option<usize> {
    if connectors.is_empty() {
        return None;
    }
    if let Some(i) = connectors.iter().position(|c| !chosen.is_empty() && c == chosen) {
        return Some(i);
    }
    Some(connectors.iter().position(|c| !c.is_empty() && !["eDP", "LVDS", "DSI"].iter().any(|b| c.starts_with(b))).unwrap_or(0))
}

pub fn main_monitor() -> Option<gtk::gdk::Monitor> {
    let mons = monitors();
    let chosen = sonata_config::load("displays", &serde_json::json!({"main": ""})).get("main").and_then(|v| v.as_str()).unwrap_or("").to_string();
    pick_main(&mons.iter().map(connector).collect::<Vec<_>>(), &chosen).map(|i| mons[i].clone())
}

#[cfg(test)]
mod tests {
    use super::pick_main;

    /// The first Alt+Tab, the first volume key and the first Notification
    /// Center after login lagged: nothing drew them before (layer.prewarm
    /// had no Rust counterpart outside Spotlight and Launchpad). Drawn once
    /// unseen and put back whole; opened during its warm-up, it opens as
    /// ever instead of staying at 1 % opacity, deaf to clicks.
    #[test]
    fn test_first_alt_tab_after_login_lagged() {
        use gtk::prelude::*;
        use std::{cell::Cell, rc::Rc};
        if !crate::gtk_for_tests() {
            return; // (no display)
        }
        let win = gtk::Window::new();
        win.set_child(Some(&gtk::Label::new(Some("Switcher"))));
        let mapped = Rc::new(Cell::new(false));
        let m = mapped.clone();
        win.connect_map(move |_| m.set(true));
        let (opened, closed) = (Rc::new(Cell::new(false)), Rc::new(Cell::new(false)));
        let (o, c) = (opened.clone(), closed.clone());
        super::prewarm(&win, 0, 3, Some(Box::new(move || o.set(true))), Some(Box::new(move || c.set(true))));
        let ctx = gtk::glib::MainContext::default();
        let t = std::time::Instant::now();
        while !(mapped.get() && !win.is_visible()) && t.elapsed() < std::time::Duration::from_secs(5) {
            ctx.iteration(false);
        }
        assert!(mapped.get() && opened.get(), "never drawn before the first open");
        assert!(!win.is_visible() && !super::warming(&win), "still on screen after the warm-up");
        assert!(closed.get(), "its content never put back");
        assert!(win.opacity() == 1.0 && win.can_target());
        // asked to open during the warm-up: back whole at once -- and a
        // surface that never takes clicks (the OSD) still doesn't
        win.set_can_target(false);
        let after = Rc::new(Cell::new(false));
        let a = after.clone();
        super::prewarm(&win, 0, 1000, None, Some(Box::new(move || a.set(true))));
        let t = std::time::Instant::now();
        while !super::warming(&win) && t.elapsed() < std::time::Duration::from_secs(5) {
            ctx.iteration(false);
        }
        assert!(super::warming(&win) && win.opacity() < 0.1);
        super::end_prewarm(&win);
        assert!(!super::warming(&win) && after.get());
        assert!(win.opacity() == 1.0 && !win.can_target() && !win.is_visible(), "the OSD took the clicks under it");
        super::end_prewarm(&win); // (none running: nothing)
        win.destroy();
    }

    #[test]
    fn the_main_display() {
        let c = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(pick_main(&c(&["eDP-1", "HDMI-A-1"]), ""), Some(1)); // an external one first
        assert_eq!(pick_main(&c(&["eDP-1", "HDMI-A-1"]), "eDP-1"), Some(0)); // your choice
        assert_eq!(pick_main(&c(&["eDP-1"]), "DP-2"), Some(0)); // the chosen one unplugged
        assert_eq!(pick_main(&[], ""), None);
    }
}
