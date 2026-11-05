//! Launchpad, macOS Big Sur (sonata2/shell/launchpad.py, launchpad_window.py):
//! a full-screen app grid over the dimmed desktop, or the Apps Menu layout
//! (a glass panel; Settings > Appearance > Apps style).
//!
//! - Pages (Adw.Carousel: swipe, scroll, arrow keys, page dots), as many
//!   rows as the display has room for.
//! - Search at the top; Return opens the first result.
//! - Folders: hold an app over another to make one (named from the apps'
//!   category); click to open, rename in place, drag apps out; Lock Folder;
//!   the Hidden folder (behind the password).
//! - Drag to reorder (after a short pause), hold at the screen side to turn
//!   pages, drag onto the Dock to pin (an app) or copy (a folder).
//! - Jiggle mode (press and hold, or hold Alt): a delete badge -> Uninstall.
//! - Opens / closes with the Big Sur zoom + fade; a click on empty space,
//!   Esc or launching an app closes it.
//!
//! `sonata2-rs launchpad [--preview] [--search TEXT] [--folder] [--jiggle]
//! [--background] [--dark|--light]`, as `sonata2 launchpad`
//! (__main__.run_launchpad): single instance and resident; running it again
//! (or the app's "toggle" action over D-Bus) toggles it, "close" closes it.
//!
//! On trial (sonata_config::side_by_side) it runs next to the Python
//! Launchpad: its own app id (`io.github.vinioliveiras.sonata2rs.launchpad`)
//! and layer namespace (`sonata2rs-launchpad`); launchpad.json (and the
//! Dock's pins and linked folders it edits) is read from ~/.config/sonata2
//! and written only to ~/.config/sonata2-rs; it asks the Dock of its own
//! build (sonata_dock::app_id) to sit above it. `--preview` is a normal
//! window (PREVIEW_SIZE, default 1280x800) over the sample wallpaper,
//! without layer-shell, Wayfire or a word to the Dock: the way to try it.
//!
//! At login (--background, not a preview) the hidden window is drawn once
//! invisibly (prewarm, layer.prewarm) and every page's icons are decoded
//! (warm_icons). Re-ordered icons glide to their place (glide.rs); a
//! dragged icon hangs translucent under the pointer (ui.drag). New / Edit
//! Web App and Settings open the Rust ones in the Rust session and the
//! Python Sonata's on trial (sonata_dock's spawn_sonata).

pub mod glide;
pub mod logic;
pub mod menu;
pub mod pad;
pub mod tiles;
pub mod zoom;

use gtk::prelude::*;
use gtk::{gdk, gio, glib};
use pad::{Mode, Pad};
use std::cell::RefCell;
use std::collections::HashSet;
use std::rc::Rc;
use std::time::Duration;

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.launchpad"
    } else {
        "io.github.vinioliveiras.sonata2.launchpad"
    }
}

fn register_css() {
    use sonata_ui::tokens::ms;
    sonata_ui::css::register_module("ui/edit.py"); // the jiggle and the delete badge
    sonata_ui::css::register_module_with("shell/launchpad.py", &[("fold_in", ms(logic::FOLD_IN_MS).to_string()), ("fold_out", ms(logic::FOLD_OUT_MS).to_string())]);
    sonata_ui::css::register_module_with("shell/launchpad_window.py", &[
        ("open_ms", ms(logic::MENU_OPEN_MS).to_string()),
        ("close_ms", ms(logic::MENU_CLOSE_MS).to_string()),
        ("fade_ms", ms(logic::MENU_FADE_MS).to_string()),
    ]);
}

fn later(ms: u64, f: impl FnOnce() + 'static) {
    glib::timeout_add_local_once(Duration::from_millis(ms), f);
}

/// macOS: an app opened any other way (Dock, a shortcut, Spotlight)
/// closes Launchpad -- a new window, or another one activated; so do
/// Mission Control and all Spaces (Wayfire's scale and expo).
fn follow_windows(pad: &Rc<Pad>) {
    let mgr = sonata_dock::toplevels::manager();
    let ids = |m: &sonata_dock::toplevels::Manager| -> (HashSet<i64>, HashSet<i64>) {
        let tops = m.toplevels();
        (tops.iter().map(|t| t.id).collect(), tops.iter().filter(|t| t.activated).map(|t| t.id).collect())
    };
    let seen = Rc::new(RefCell::new(ids(&mgr)));
    let (w, m2) = (Rc::downgrade(pad), Rc::downgrade(&mgr));
    mgr.add_listener(Rc::new(move || {
        let (Some(p), Some(m)) = (w.upgrade(), m2.upgrade()) else { return };
        let (wins, active) = ids(&m);
        let fresh = {
            let s = seen.borrow();
            wins.difference(&s.0).next().is_some() || active.difference(&s.1).next().is_some()
        };
        seen.replace((wins, active));
        if fresh && p.win.is_visible() && (p.mode.get() == Mode::Menu || p.bin.progress() > 0.5) {
            p.close_launchpad(None);
        }
    }));
    let w = Rc::downgrade(pad);
    mgr.add_event_listener(Rc::new(move |ev| {
        if let Some(p) = w.upgrade().filter(|_| ev.get("event").and_then(|e| e.as_str()) == Some("plugin-activation-state-changed")) {
            p.plugin_changed(ev);
        }
    }));
}

/// What layer.prewarm does for a surface (shell/layer.py): draw the hidden
/// window once, invisibly, shortly after start -- the first real open then
/// doesn't pay for building the widgets' render nodes, loading icons and
/// fonts and compiling the GPU shaders (the lag of the first Launchpad
/// animation after a restart). It is mapped at 1 % opacity, takes no
/// keyboard or clicks, and is hidden again after `frames` frames.
/// before / after put the content in its "open" state and back; step(t),
/// t from 0 to 1 over the frames, plays the opening animation too (the
/// in-between frames had never been drawn).
pub struct Prewarm {
    pub before: Box<dyn Fn()>,
    pub step: Option<Box<dyn Fn(f64)>>,
    pub after: Box<dyn Fn()>,
    pub frames: u32,
    pub delay_ms: u64,
}

pub const PREWARM_DELAY_MS: u64 = 1500;

/// step's t at frame n (1-based) of `frames`: 0 .. 1, the last frames at 1.
pub fn prewarm_t(n: u32, frames: u32) -> f64 {
    (n as f64 / frames.saturating_sub(1).max(1) as f64).min(1.0)
}

pub fn prewarm(win: &gtk::Window, w: Prewarm) {
    let win = win.clone();
    later(w.delay_ms, move || {
        if win.is_visible() {
            return; // opened already: nothing to do
        }
        sonata_shell::layer::set_keyboard(&win, false);
        (w.before)();
        win.set_opacity(0.01);
        win.set_can_target(false);
        win.present();
        sonata_shell::layer::set_input_region(&win, &[]);
        let n = std::cell::Cell::new(0u32);
        let w = Rc::new(w);
        win.add_tick_callback(move |win, _| {
            n.set(n.get() + 1);
            if let Some(step) = &w.step {
                step(prewarm_t(n.get(), w.frames));
            }
            if n.get() < w.frames {
                return glib::ControlFlow::Continue;
            }
            win.set_visible(false);
            win.set_opacity(1.0);
            win.set_can_target(true);
            sonata_shell::layer::set_keyboard(win.upcast_ref(), true);
            if let Some(surface) = win.surface() {
                // the whole surface takes clicks again
                let all = gtk::cairo::Region::create_rectangle(&gtk::cairo::RectangleInt::new(0, 0, i32::MAX / 2, i32::MAX / 2));
                surface.set_input_region(&all);
            }
            (w.after)();
            glib::ControlFlow::Break
        });
    });
}

/// The login's warm-up, in the layout that will open: the Apps Menu sized
/// and filled, or the full screen's opening animation frame by frame (its
/// zoom picture made then, kept for the first open while the grid stays).
fn warm_up(pad: &Rc<Pad>) {
    let win: gtk::Window = pad.win.clone().upcast();
    if logic::style() == "window" {
        pad.set_mode(Mode::Menu);
        let p = pad.clone();
        prewarm(&win, Prewarm {
            before: Box::new(move || {
                if let Some(mv) = p.menu.borrow().clone() {
                    mv.size(&p, None);
                    mv.build_tabs(&p);
                }
                p.render();
            }),
            step: None,
            after: Box::new(|| {}),
            frames: 3,
            delay_ms: PREWARM_DELAY_MS,
        });
        return;
    }
    let (p1, p2, p3) = (pad.clone(), pad.clone(), pad.clone());
    prewarm(&win, Prewarm {
        before: Box::new(move || p1.bin.set_progress(0.0)),
        step: Some(Box::new(move |t| {
            p2.bin.freeze(); // (once laid out; then the kept picture)
            p2.bin.set_progress(t);
        })),
        after: Box::new(move || {
            p3.bin.thaw();
            p3.bin.set_progress(0.0);
        }),
        frames: 14,
        delay_ms: PREWARM_DELAY_MS,
    });
}

/// Every app's tile made and its icon decoded ahead of time, page after
/// page (launchpad.warm_icons: the first open drew only the first page warm).
pub fn warm_icons(pad: &Rc<Pad>) {
    if pad.win.is_visible() {
        return;
    }
    let items: Vec<_> = pad.model.borrow().pages.iter().flatten().cloned().collect();
    let mut imgs = Vec::new();
    for item in &items {
        let tile = pad.item_widget(item);
        let mut stack: Vec<gtk::Widget> = vec![tile.button.clone().upcast()];
        while let Some(c) = stack.pop() {
            if let Ok(img) = c.clone().downcast::<gtk::Image>() {
                imgs.push(img);
            }
            let mut ch = c.first_child();
            while let Some(x) = ch {
                ch = x.next_sibling();
                stack.push(x);
            }
        }
    }
    warm_images(imgs);
}

/// Decode these images' pictures ahead of time, a few per idle moment
/// (icons.warm_images: an SVG app icon with soft shadows costs
/// milliseconds to draw the first time). Kept by the icon theme's / the
/// images' own caches.
pub fn warm_images(mut todo: Vec<gtk::Image>) {
    const CHUNK: usize = 6;
    let one = |img: &gtk::Image| {
        let size = if img.pixel_size() > 0 { img.pixel_size() } else { 48 };
        let scale = img.scale_factor().max(1);
        let theme = || gtk::IconTheme::for_display(&WidgetExt::display(img));
        let p: Option<gdk::Paintable> = match img.storage_type() {
            gtk::ImageType::Paintable => img.paintable(),
            gtk::ImageType::Gicon => img.gicon().map(|g| theme().lookup_by_gicon(&g, size, scale, gtk::TextDirection::None, gtk::IconLookupFlags::empty()).upcast()),
            gtk::ImageType::IconName => img.icon_name().map(|n| theme().lookup_icon(&n, &[], size, scale, gtk::TextDirection::None, gtk::IconLookupFlags::empty()).upcast()),
            _ => None,
        };
        if let Some(p) = p {
            p.snapshot(&gtk::Snapshot::new(), size as f64, size as f64);
        }
    };
    glib::idle_add_local(move || {
        for _ in 0..CHUNK {
            let Some(img) = todo.pop() else { return glib::ControlFlow::Break };
            one(&img);
        }
        if todo.is_empty() {
            glib::ControlFlow::Break
        } else {
            glib::ControlFlow::Continue
        }
    });
}

pub fn run(args: &[String]) -> glib::ExitCode {
    let a = Rc::new(logic::parse(args));
    if !a.preview {
        sonata_shell::layer::ensure_preload();
    }
    let app = gtk::Application::builder().application_id(app_id()).build();
    let state: Rc<RefCell<Option<Rc<Pad>>>> = Rc::default();
    let hold: RefCell<Option<gio::ApplicationHoldGuard>> = RefCell::default();
    app.connect_activate(move |app| {
        if let Some(p) = state.borrow().as_ref() {
            p.toggle(); // second launch: toggle
            return;
        }
        if let Some(f) = a.appearance {
            sonata_ui::force_appearance(f);
        }
        sonata_ui::setup();
        register_css();
        let pad = Pad::new(app, a.preview);
        hold.replace(Some(app.hold())); // stays resident while hidden: instant opening
        state.replace(Some(pad.clone()));
        // (the Dock, before asking to uninstall an app)
        let close = gio::SimpleAction::new("close", None);
        let w = Rc::downgrade(&pad);
        close.connect_activate(move |_, _| {
            if let Some(p) = w.upgrade().filter(|p| p.win.is_visible()) {
                p.close_launchpad(None);
            }
        });
        app.add_action(&close);
        // open / close from the Dock and `sonata2 launchpad` over D-Bus
        let toggle = gio::SimpleAction::new("toggle", None);
        let w = Rc::downgrade(&pad);
        toggle.connect_activate(move |_, _| {
            if let Some(p) = w.upgrade() {
                p.toggle();
            }
        });
        app.add_action(&toggle);
        if a.preview {
            let (w, h) = sonata_mission::preview::preview_size((1280, 800));
            pad.win.set_default_size(w, h);
            if let Some((_, blurred)) = sonata_mission::preview::wallpaper(w, h, sonata_ui::is_dark()) {
                pad.bin.set_backdrop(Some(blurred));
            }
        } else {
            follow_windows(&pad);
        }
        if a.background {
            pad.win.set_visible(false); // login: start resident, hidden
            if !a.preview {
                // drawn once invisibly: the first open is smooth
                warm_up(&pad);
                let p = pad.clone();
                later(4000, move || warm_icons(&p)); // every page's icons, not only the first
            }
            return;
        }
        pad.open_launchpad();
        if let Some(text) = a.search.clone() {
            let p = pad.clone();
            later(400, move || p.search.set_text(&text));
        }
        if a.folder {
            let p = pad.clone();
            later(500, move || {
                let first = p.model.borrow().pages.iter().flatten().find_map(|it| match it {
                    sonata_desktop::launchpad_model::Item::Folder(f) => Some(f.clone()),
                    _ => None,
                });
                if let Some(f) = first {
                    p.open_folder(&f);
                }
            });
        }
        if a.jiggle {
            let p = pad.clone();
            later(400, move || p.set_jiggle(true, true));
        }
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini: the first open still lagged -- the in-between frames had never
    /// been drawn. The warm-up's frames walk the whole opening, 0 to 1.
    #[test]
    fn test_warm_up_draws_the_whole_opening() {
        let ts: Vec<f64> = (1..=14).map(|n| prewarm_t(n, 14)).collect();
        assert!(ts.windows(2).all(|w| w[0] <= w[1]));
        assert!(ts[0] > 0.0 && ts[0] < 0.1);
        assert_eq!(ts[12], 1.0);
        assert_eq!(*ts.last().unwrap(), 1.0);
        assert_eq!(prewarm_t(1, 1), 1.0); // (one frame: drawn open)
    }
}
