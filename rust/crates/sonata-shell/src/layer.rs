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

/// The displays connected now.
pub fn monitors() -> Vec<gtk::gdk::Monitor> {
    let Some(display) = gtk::gdk::Display::default() else { return Vec::new() };
    let list = display.monitors();
    (0..list.n_items()).filter_map(|i| list.item(i).and_downcast::<gtk::gdk::Monitor>()).collect()
}
