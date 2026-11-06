//! One CSS provider per process, filled from the tokens for the current
//! appearance and refilled when it changes (sonata2/ui/theme.py). Light /
//! Dark comes from Sonata's own settings (system.json, as prefs.py keeps
//! it), the accent from appearance.json; both are followed live.
//!
//! The user's corner radii, background colours and window buttons (size,
//! side, order: their tl_* tokens) are in the tokens too (tokens::user_palette),
//! so a change in Settings > Appearance restyles every Sonata window;
//! on_buttons_change tells the windows that place buttons themselves.
//!
//! A change of look (Dark Mode, accent, corners, background, a glass
//! switch) cross-fades every Sonata window from a picture of its old look,
//! and what is painted with [`rgba`] blends the old and new colours
//! (theme.py's _start_fade).

use crate::glass as parts;
use crate::tokens::{self, Values};
use serde_json::json;
use std::cell::RefCell;

const PRIORITY: u32 = gtk::STYLE_PROVIDER_PRIORITY_USER + 10;

#[derive(Default)]
struct State {
    templates: Vec<(String, String, Values)>,
    provider: Option<gtk::CssProvider>,
    last_css: String,
    monitors: Vec<gtk::gio::FileMonitor>,
    forced: bool,
    listeners: Vec<Box<dyn Fn()>>,
    buttons_seen: Option<String>,
    buttons_listeners: Vec<std::rc::Rc<dyn Fn()>>,
    /// the tokens now (values() is read many times a frame by drawing code)
    cached: Option<Values>,
    /// the tokens before the last load: what a cross-fade starts from
    last_vals: Option<Values>,
    /// (old tokens, start in µs) while cross-fading
    fade: Option<(Values, i64)>,
    /// what the look was at the last change (theme._remember_appearance)
    look_seen: Option<String>,
    cached_dark: Option<bool>,
    dock_glass_seen: Option<String>,
}

thread_local! {
    static STATE: RefCell<State> = RefCell::new(State::default());
}

/// Add a component's CSS (`%(token)s` placeholders). Registering the same
/// `key` again replaces it; the same CSS again is nothing to do.
pub fn register(key: &str, template: &str) {
    register_with(key, template, Values::new());
}

/// [`register`] with extra placeholders for this template only.
pub fn register_with(key: &str, template: &str, local: Values) {
    let changed = STATE.with(|s| {
        let mut s = s.borrow_mut();
        if let Some(t) = s.templates.iter_mut().find(|t| t.0 == key) {
            if t.1 == template && t.2 == local {
                return false;
            }
            t.1 = template.to_string();
            t.2 = local;
        } else {
            s.templates.push((key.to_string(), template.to_string(), local));
        }
        s.provider.is_some()
    });
    if changed {
        load();
    }
}

pub fn is_dark() -> bool {
    use std::sync::atomic::Ordering;
    // libadwaita belongs to the main thread: asked from another (Settings'
    // display read, for the wallpaper's Light / Dark picture) it panicked and
    // took that thread down -- Vini: Settings > Displays came up empty, no
    // displays, no brightness. Elsewhere, the last answer the main thread had.
    if !gtk::is_initialized_main_thread() {
        return DARK.load(Ordering::Relaxed);
    }
    let dark = adw::StyleManager::default().is_dark();
    DARK.store(dark, Ordering::Relaxed);
    dark
}

/// The last is_dark() the main thread saw (for the other threads).
static DARK: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn accent() -> String {
    let a = sonata_config::load("appearance", &json!({"accent": "blue"}));
    a.get("accent").and_then(|v| v.as_str()).unwrap_or("blue").to_string()
}

fn appearance_flag(key: &str) -> bool {
    sonata_config::stored("appearance").get(key).and_then(|v| v.as_bool()).unwrap_or(false)
}

/// True when the compositor blurs Sonata windows (Sonata session or the
/// dev session: SONATA_GLASS=1) and Reduce transparency is off. Elsewhere
/// materials fall back to their solid tokens.
pub fn glass() -> bool {
    std::env::var("SONATA_GLASS").as_deref() == Ok("1") && !appearance_flag("reduce_transparency")
}

/// The current tokens (for code that needs a value, e.g. drawing), plus
/// the materials resolved for this compositor (theme.values). Read from
/// the settings once per change (they are followed once set up).
pub fn values() -> Values {
    if let Some(v) = STATE.with(|s| s.borrow().cached.clone()) {
        if STATE.with(|s| s.borrow().cached_dark) == Some(is_dark()) {
            return v;
        }
    }
    let v = values_now();
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        if s.provider.is_some() {
            s.cached = Some(v.clone());
            s.cached_dark = Some(is_dark());
        }
    });
    v
}

fn values_now() -> Values {
    let dark = is_dark();
    let mut v = tokens::user_palette(dark, &sonata_config::stored("appearance"));
    v.extend(tokens::accent_tokens(&accent(), dark));
    let parts = parts::settings();
    let ok = glass();
    for (token, item) in [("sidebar_material", "windows"), ("panel_material", "menus"), ("dock_material", "dock"),
                          ("bar_material", "menubar"), ("launchpad_material", "launchpad")] {
        let m = parts::material(item, &v, ok, &parts);
        v.insert(token.into(), m);
    }
    let windows = parts.iter().find(|(i, _)| *i == "windows").map(|(_, p)| *p);
    if let Some(w) = windows.filter(|w| ok && w.on && appearance_flag("glass_titlebars")) {
        // title bars and toolbars: the glass again
        for k in ["titlebar_glass", "titlebar_glass_inactive"] {
            if let (Some(a), Some(c)) = (w.alpha, v.get(k).cloned()) {
                v.insert(k.into(), parts::with_alpha(&c, a));
            }
        }
        for (bg, glass_k) in [("titlebar_bg", "titlebar_glass"), ("titlebar_bg_inactive", "titlebar_glass_inactive")] {
            if let Some(c) = v.get(glass_k).cloned() {
                v.insert(bg.into(), c);
            }
        }
    }
    v
}

/// Call `callback` after every restyle (appearance or accent changed).
pub fn on_change<F: Fn() + 'static>(callback: F) {
    STATE.with(|s| s.borrow_mut().listeners.push(Box::new(callback)));
}

// The window buttons (Settings > Appearance: Window buttons, Button order,
// Button colours) changed: windows already open follow at once (Vini:
// Settings itself, Files and the custom colours waited for the app to open
// again).

/// `callback()` when the buttons' side, order or look changes in
/// appearance.json. (A callback for a window holds it weakly.)
pub fn on_buttons_change<F: Fn() + 'static>(callback: F) {
    STATE.with(|s| s.borrow_mut().buttons_listeners.push(std::rc::Rc::new(callback)));
}

/// What the buttons look like in appearance.json (theme._buttons_sig).
fn buttons_sig(a: &serde_json::Map<String, serde_json::Value>) -> String {
    let get = |k: &str| a.get(k).map(|v| v.to_string()).unwrap_or_default();
    [get("buttons_side"), get("buttons_order"), get("buttons_style"), get("buttons_colors")].join("|")
}

fn buttons_changed() {
    let sig = buttons_sig(&sonata_config::stored("appearance"));
    let (first, same) = STATE.with(|s| {
        let mut s = s.borrow_mut();
        let first = s.buttons_seen.is_none();
        let same = s.buttons_seen.as_deref() == Some(sig.as_str());
        s.buttons_seen = Some(sig);
        (first, same)
    });
    if first || same {
        return; // (the first look: remembered, nothing to follow)
    }
    let listeners = STATE.with(|s| s.borrow().buttons_listeners.clone());
    for l in listeners {
        l();
    }
}

/// What, changed, deserves a cross-fade: the accent, the corners and the
/// buttons' size, the background colours, glass title bars, each part's
/// glass switch, Reduce transparency (theme._appearance_changed's "big").
/// A glass slider alone re-styles without one (it re-pictures every window).
fn look_sig() -> String {
    let a = sonata_config::stored("appearance");
    let r = tokens::user_radii(&a);
    let on: Vec<bool> = parts::settings().iter().map(|(_, p)| p.on).collect();
    format!("{}|{r:?}|{}|{:?}|{}|{}|{on:?}", accent(), crate::frame::button_size(&a), tokens::user_backgrounds(&a), appearance_flag("glass_titlebars"), appearance_flag("reduce_transparency"))
}

fn appearance_changed() {
    buttons_changed();
    let sig = look_sig();
    let big = STATE.with(|s| {
        let mut s = s.borrow_mut();
        let big = s.look_seen.as_deref().is_some_and(|seen| seen != sig);
        s.look_seen = Some(sig);
        big
    });
    load_fading(big);
}

fn dock_glass() -> String {
    sonata_config::stored("dock").get("glass").map(|v| v.to_string()).unwrap_or_default()
}

/// dock.json changed: only its old "glass" switch is the theme's -- every
/// write (an icon pinned or moved, the Dock resized, its recents) re-read
/// the look in every Sonata process.
fn dock_changed() {
    let g = dock_glass();
    let changed = STATE.with(|s| s.borrow_mut().dock_glass_seen.replace(g.clone()).is_some_and(|old| old != g));
    if changed {
        appearance_changed();
    }
}

fn load() {
    load_fading(false);
}

fn load_fading(fade: bool) {
    let (templates, provider) = STATE.with(|s| {
        let mut s = s.borrow_mut();
        s.cached = None;
        (s.templates.clone(), s.provider.clone())
    });
    let Some(provider) = provider else { return };
    let vals = values();
    let old = STATE.with(|s| s.borrow_mut().last_vals.replace(vals.clone()));
    if fade {
        if let Some(old) = old.clone() {
            start_fade(old);
        }
    }
    let css: Vec<String> = templates
        .iter()
        .map(|(_, t, local)| {
            let mut v = vals.clone();
            v.extend(local.iter().map(|(k, x)| (k.clone(), x.clone())));
            tokens::fill(t, &v)
        })
        .collect();
    let css = tokens::scale_durations(&css.join("\n"));
    let same = STATE.with(|s| {
        let mut s = s.borrow_mut();
        let same = s.last_css == css;
        s.last_css = css.clone();
        same
    });
    if same && old.as_ref() == Some(&vals) {
        return; // nothing changed: no re-parse, no redraw everywhere
    }
    if !same {
        provider.load_from_string(&css);
    }
    notify_listeners();
}

fn notify_listeners() {
    // (listeners run outside the borrow: they may register CSS themselves)
    let listeners = STATE.with(|s| std::mem::take(&mut s.borrow_mut().listeners));
    for l in &listeners {
        l();
    }
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        let added = std::mem::take(&mut s.listeners);
        s.listeners = listeners;
        s.listeners.extend(added);
    });
}

fn color_scheme_dark() -> bool {
    sonata_config::stored("system")
        .get("org.gnome.desktop.interface/color-scheme")
        .and_then(|v| v.as_str())
        == Some("prefer-dark")
}

fn follow_color_scheme() {
    if STATE.with(|s| s.borrow().forced) {
        return;
    }
    let scheme = if color_scheme_dark() { adw::ColorScheme::ForceDark } else { adw::ColorScheme::ForceLight };
    let sm = adw::StyleManager::default();
    if sm.color_scheme() != scheme {
        sm.set_color_scheme(scheme);
    }
}

/// A window made without the application (About This Computer, a
/// dialog's own window) is named after the program on Wayland, and every
/// component is the one `sonata2-rs`: the Dock took About This Computer
/// for the Assistant (Vini), the first entry running `sonata2-rs`. Each
/// process is its component's app id instead, as __main__.py does.
pub(crate) fn name_windows() {
    use gtk::{gio, glib, prelude::*};
    if let Some(id) = gio::Application::default().and_then(|a| a.application_id()) {
        glib::set_prgname(Some(id.as_str()));
    }
}

/// Install the provider once per process (idempotent), after the
/// application started (a display exists).
pub fn setup() {
    if STATE.with(|s| s.borrow().provider.is_some()) {
        return;
    }
    let Some(display) = gtk::gdk::Display::default() else {
        eprintln!("sonata-ui: no display");
        return;
    };
    name_windows();
    crate::icons::setup(); // Sonata's own icon theme, before any widget (as ui.setup does)
    let provider = gtk::CssProvider::new();
    gtk::style_context_add_provider_for_display(&display, &provider, PRIORITY);
    STATE.with(|s| s.borrow_mut().provider = Some(provider));
    crate::window::register_css();
    crate::controls::register_css();
    crate::menu::register_css();
    crate::dialog::register_css();
    crate::panel::register_css();
    crate::transition::register_css();
    is_dark(); // (kept for the other threads, and on every change below)
    adw::StyleManager::default().connect_dark_notify(|_| {
        is_dark();
        load_fading(true)
    });
    buttons_changed(); // what the buttons look like now
    // what the look is now, so the first appearance.json / dock.json write
    // compares with it (Vini: the Dock writing its recents cross-faded
    // every window when nothing had been seen yet)
    let (sig, dock) = (look_sig(), dock_glass());
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        s.look_seen = Some(sig);
        s.dock_glass_seen = Some(dock);
    });
    let mut monitors = sonata_config::watch("appearance", appearance_changed);
    monitors.extend(sonata_config::watch("dock", dock_changed)); // its old "glass" switch (glass.rs)
    monitors.extend(sonata_config::watch("system", follow_color_scheme));
    STATE.with(|s| s.borrow_mut().monitors = monitors);
    follow_color_scheme();
    load();
    sliders_ignore_wheel();
}

/// No slider moves under the mouse wheel (macOS): the wheel scrolls the
/// page instead. A slider's own scroll controller is taken off as it is
/// realized, so the event goes on to the scrolled window
/// (theme._sliders_ignore_wheel).
fn sliders_ignore_wheel() {
    use gtk::glib::translate::{from_glib_none, IntoGlib};
    use gtk::glib::gobject_ffi;
    use adw::prelude::*;
    unsafe extern "C" fn strip(_hint: *mut gobject_ffi::GSignalInvocationHint, n: u32, params: *const gobject_ffi::GValue, _data: gtk::glib::ffi::gpointer) -> gtk::glib::ffi::gboolean {
        if n == 0 || params.is_null() {
            return gtk::glib::ffi::GTRUE;
        }
        let obj = gobject_ffi::g_value_get_object(params);
        if obj.is_null() {
            return gtk::glib::ffi::GTRUE;
        }
        let w: gtk::glib::Object = from_glib_none(obj);
        // the hook sees every widget's "realize" (the signal is GtkWidget's): sliders only
        if let Some(range) = w.downcast_ref::<gtk::Range>() {
            strip_wheel(range);
        }
        gtk::glib::ffi::GTRUE // (keep the hook)
    }
    thread_local!(static DONE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) });
    if DONE.with(|d| d.replace(true)) {
        return;
    }
    unsafe {
        // (a class's signals exist once the class does: none may be made yet)
        let ty = gtk::Widget::static_type().into_glib();
        gobject_ffi::g_type_class_ref(ty);
        let id = gobject_ffi::g_signal_lookup(c"realize".as_ptr(), ty);
        if id != 0 {
            gobject_ffi::g_signal_add_emission_hook(id, 0, Some(strip), std::ptr::null_mut(), None);
        }
    }
}

/// `range` without its scroll controllers: the wheel goes on to the page.
pub fn strip_wheel(range: &impl gtk::prelude::IsA<gtk::Range>) {
    use adw::prelude::*;
    let ctrls = range.as_ref().observe_controllers();
    for i in (0..ctrls.n_items()).rev() {
        if let Some(c) = ctrls.item(i).and_downcast::<gtk::EventControllerScroll>() {
            range.as_ref().remove_controller(&c);
        }
    }
}

// -- cross-fade on a change of look -----------------------------------------------------------
/// How long a change of look cross-fades (theme.FADE_MS, as designed).
pub const FADE_MS: i64 = 320;
const NO_FADE: &str = "sonata-no-fade";
const FADE_OVERLAY: &str = "sonata-fade-overlay";
const FADE_TOO: &str = "sonata-fade-too";

/// No picture cross-fade over `win`: it blends its own colours (the Dock,
/// the menu bar) or fades its own pictures (the wallpaper). The fade puts
/// a window's content in an overlay, which re-lays it out.
pub fn no_fade(win: &impl gtk::prelude::IsA<gtk::Window>) {
    use adw::prelude::*;
    unsafe { win.as_ref().set_data(NO_FADE, true) };
}

/// A shell surface that cross-fades like an app window (Spotlight, the
/// app switcher, Notification Center: theme.py fades every window but the
/// Dock, the menu bar and the wallpaper). Undecorated windows only fade
/// when they say so here: the Dock and the menu bar don't call no_fade.
/// Its content goes in the fade's overlay now, before the pointer is ever
/// on it: put there during a fade, the content was taken out of the window
/// and put back under the pointer, and lost its hover until the pointer
/// moved (GTK sends no crossing events for a re-rooted widget).
pub fn fade_too(win: &impl gtk::prelude::IsA<gtk::Window>) {
    use adw::prelude::*;
    let win = win.as_ref();
    unsafe { win.set_data(FADE_TOO, true) };
    fade_overlay(win);
    // (content set later: in by the time the window is on screen)
    win.connect_realize(|w| {
        fade_overlay(w);
    });
}

fn faded(win: &gtk::Window) -> bool {
    use adw::prelude::*;
    unsafe { win.data::<bool>(NO_FADE).is_none() }
}

/// theme._start_fade's rule: every window on screen but those that fade
/// themselves (no_fade). Here undecorated windows -- the shell's surfaces
/// -- fade only when they opted in (fade_too), as the Dock and the menu
/// bar don't opt out.
fn fades(win: &gtk::Window) -> bool {
    use adw::prelude::*;
    let opted_in = win.is_decorated() || unsafe { win.data::<bool>(FADE_TOO).is_some() };
    win.is_visible() && win.is_mapped() && opted_in && faded(win)
}

fn content(win: &gtk::Window) -> Option<gtk::Widget> {
    use adw::prelude::*;
    if let Some(w) = win.downcast_ref::<adw::ApplicationWindow>() {
        return w.content();
    }
    if let Some(w) = win.downcast_ref::<adw::Window>() {
        return w.content();
    }
    win.child()
}

fn set_content(win: &gtk::Window, child: Option<&gtk::Widget>) {
    use adw::prelude::*;
    if let Some(w) = win.downcast_ref::<adw::ApplicationWindow>() {
        w.set_content(child);
    } else if let Some(w) = win.downcast_ref::<adw::Window>() {
        w.set_content(child);
    } else {
        win.set_child(child);
    }
}

/// Smoothstep: how far a fade is at `t` (0..1) of its time.
pub fn ease(t: f64) -> f64 {
    let t = t.clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// The window's fade overlay and the content in it, the content put in
/// it the first time. Once in, it stays: the window's content is then the
/// overlay itself, and was wrapped in one more overlay -- and taken out
/// and put back, losing hover and focus -- on every change of look.
fn fade_overlay(win: &gtk::Window) -> Option<(gtk::Overlay, gtk::Widget)> {
    use adw::prelude::*;
    let top = content(win)?;
    let kept = unsafe { win.data::<gtk::Overlay>(FADE_OVERLAY).map(|p| p.as_ref().clone()) };
    if let Some(o) = kept.filter(|o| o.upcast_ref::<gtk::Widget>() == &top) {
        let child = o.child()?;
        return Some((o, child));
    }
    // an alert in its own window: libadwaita aborts the app when a dialog
    // is put in an overlay (Dark Mode switched with an alert open took
    // Settings down). It keeps its plain switch.
    if is_dialog(&top) {
        return None;
    }
    // moving the content drops the focus, and GTK hands it on: a sidebar
    // list would select its next row. Kept.
    let focus = gtk::prelude::GtkWindowExt::focus(win);
    set_content(win, None);
    let o = gtk::Overlay::new();
    o.set_child(Some(&top));
    set_content(win, Some(o.upcast_ref()));
    unsafe { win.set_data(FADE_OVERLAY, o.clone()) };
    if let Some(f) = focus.filter(|f| f.root().as_ref().map(|r| r.upcast_ref::<gtk::Widget>()) == Some(win.upcast_ref())) {
        f.grab_focus();
    }
    Some((o, top))
}

fn is_dialog(w: &gtk::Widget) -> bool {
    use adw::prelude::*;
    w.is::<adw::Dialog>() || w.type_().name().starts_with("AdwDialog")
}

/// Cross-fade one window: a picture of how it looked, over it, fading out
/// while the new style shows underneath (GTK runs no CSS transitions for
/// a stylesheet change, so it is done with a picture).
fn fade_window(win: &gtk::Window) {
    use adw::prelude::*;
    let Some(top) = content(win).filter(|t| !is_dialog(t)) else { return };
    let child = unsafe { win.data::<gtk::Overlay>(FADE_OVERLAY).map(|p| p.as_ref().clone()) }.filter(|o| o.upcast_ref::<gtk::Widget>() == &top).and_then(|o| o.child()).unwrap_or(top);
    let (w, h) = (child.width(), child.height());
    let Some(renderer) = win.renderer() else { return };
    if w <= 0 || h <= 0 {
        return;
    }
    let snap = gtk::Snapshot::new();
    gtk::WidgetPaintable::new(Some(&child)).snapshot(&snap, w as f64, h as f64);
    let Some(node) = snap.to_node() else { return };
    let tex = renderer.render_texture(&node, Some(&gtk::graphene::Rect::new(0.0, 0.0, w as f32, h as f32)));
    let Some((over, _)) = fade_overlay(win) else { return };
    // the old look at its own size, pinned to the top left: if the layout
    // changes meanwhile it is covered, never stretched
    let pic = gtk::Picture::builder().paintable(&tex).can_target(false).can_shrink(false).content_fit(gtk::ContentFit::Fill).halign(gtk::Align::Start).valign(gtk::Align::Start).build();
    pic.set_size_request(w, h);
    over.add_overlay(&pic);
    let t0 = gtk::glib::monotonic_time();
    gtk::glib::timeout_add_local(std::time::Duration::from_millis(16), move || {
        let t = ((gtk::glib::monotonic_time() - t0) as f64 / (FADE_MS * 1000) as f64).min(1.0);
        pic.set_opacity(1.0 - ease(t));
        if t >= 1.0 {
            over.remove_overlay(&pic);
            return gtk::glib::ControlFlow::Break;
        }
        gtk::glib::ControlFlow::Continue
    });
}

fn start_fade(old: Values) {
    use adw::prelude::*;
    for w in gtk::Window::list_toplevels() {
        let Ok(w) = w.downcast::<gtk::Window>() else { continue };
        if fades(&w) {
            fade_window(&w);
        }
    }
    let t0 = gtk::glib::monotonic_time();
    STATE.with(|s| s.borrow_mut().fade = Some((old, t0)));
    gtk::glib::timeout_add_local(std::time::Duration::from_millis(16), move || {
        let done = gtk::glib::monotonic_time() - t0 >= FADE_MS * 1000;
        if done {
            STATE.with(|s| {
                let mut s = s.borrow_mut();
                if s.fade.as_ref().is_some_and(|f| f.1 == t0) {
                    s.fade = None;
                }
            });
        }
        notify_listeners(); // what paints with rgba() blends a step further
        if done { gtk::glib::ControlFlow::Break } else { gtk::glib::ControlFlow::Continue }
    });
}

/// Every widget under `root` drawn anew, and every named icon looked up
/// again. GTK keeps a widget's last picture until it asks to redraw: a
/// menu bar icon or a Dock badge kept its Light Mode look in Dark Mode
/// until clicked (Vini). Run once a change has settled ([`settled`]).
pub fn refresh_tree(root: &impl gtk::prelude::IsA<gtk::Widget>) {
    use adw::prelude::*;
    let mut stack = vec![root.as_ref().clone()];
    while let Some(w) = stack.pop() {
        if let Some(img) = w.downcast_ref::<gtk::Image>() {
            if img.storage_type() == gtk::ImageType::IconName {
                if let Some(name) = img.icon_name() {
                    img.set_icon_name(None);
                    img.set_icon_name(Some(&name));
                }
            }
        }
        w.queue_draw();
        let mut c = w.first_child();
        while let Some(x) = c {
            c = x.next_sibling();
            stack.push(x);
        }
    }
}

/// `callback()` once the look stops changing: `ms` after the last call
/// (an on_change listener is called on every frame of a cross-fade).
pub fn settled(owner: &impl gtk::prelude::IsA<gtk::glib::Object>, ms: u64, callback: impl Fn() + 'static) {
    use adw::prelude::*;
    const KEY: &str = "sonata-settle-src";
    let owner = owner.as_ref();
    if let Some(src) = unsafe { owner.steal_data::<gtk::glib::SourceId>(KEY) } {
        crate::cancel(src);
    }
    let weak = owner.downgrade();
    let src = gtk::glib::timeout_add_local_once(std::time::Duration::from_millis(ms), move || {
        if let Some(o) = weak.upgrade() {
            let _ = unsafe { o.steal_data::<gtk::glib::SourceId>(KEY) };
            callback();
        }
    });
    unsafe { owner.set_data(KEY, src) };
}

/// "light" / "dark" for this process (previews); anything else follows Sonata's setting.
pub fn force_appearance(appearance: &str) {
    let forced = appearance == "dark" || appearance == "light";
    STATE.with(|s| s.borrow_mut().forced = forced);
    let sm = adw::StyleManager::default();
    match appearance {
        "dark" => sm.set_color_scheme(adw::ColorScheme::ForceDark),
        "light" => sm.set_color_scheme(adw::ColorScheme::ForceLight),
        _ => follow_color_scheme(),
    }
}

/// A colour token as a GdkRGBA (for drawing in a snapshot or with cairo);
/// during a cross-fade, the blend of the previous and the new colour.
pub fn rgba(token: &str) -> gtk::gdk::RGBA {
    let parse = |c: Option<&String>| c.and_then(|c| gtk::gdk::RGBA::parse(c.as_str()).ok());
    let new = parse(values().get(token)).unwrap_or(gtk::gdk::RGBA::BLACK);
    let old = STATE.with(|s| s.borrow().fade.as_ref().map(|(old, t0)| (old.get(token).cloned(), *t0)));
    let Some((Some(old), t0)) = old else { return new };
    let Some(o) = parse(Some(&old)) else { return new };
    let t = ease((gtk::glib::monotonic_time() - t0) as f64 / (FADE_MS * 1000) as f64) as f32;
    let mix = |a: f32, b: f32| a + (b - a) * t;
    gtk::gdk::RGBA::new(mix(o.red(), new.red()), mix(o.green(), new.green()), mix(o.blue(), new.blue()), mix(o.alpha(), new.alpha()))
}

#[cfg(test)]
mod thread_tests {
    /// Vini: Settings > Displays came up empty -- its display read, on a
    /// worker thread, asked is_dark() for the wallpaper's picture, and
    /// libadwaita panicked off the main thread, taking the read with it.
    #[test]
    fn test_displays_empty_in_settings() {
        assert!(std::thread::spawn(super::is_dark).join().is_ok(), "is_dark() panicked off the main thread");
    }
}

#[cfg(test)]
mod name_tests {
    use gtk::{gio, glib, prelude::*};

    /// Vini: About This Computer opened as the Assistant in the Dock -- a
    /// window without the application was named `sonata2-rs`, which the
    /// Dock matched to the Assistant's entry. It is named after the
    /// component's app id now.
    #[test]
    fn test_about_opened_as_assistant() {
        let app = gio::Application::new(Some("io.github.vinioliveiras.sonata2.topbar"), gio::ApplicationFlags::NON_UNIQUE);
        app.set_default();
        glib::set_prgname(Some("sonata2-rs"));
        super::name_windows();
        assert_eq!(glib::prgname().as_deref(), Some("io.github.vinioliveiras.sonata2.topbar"));
    }
}

#[cfg(test)]
mod look_tests {
    use super::*;
    use adw::prelude::*;

    fn gtk_here() -> bool {
        !(gtk::is_initialized() && !gtk::is_initialized_main_thread()) && crate::gtk_for_tests() && adw::init().is_ok()
    }

    /// One GTK test (GTK lives on one thread): the toolkit's global parts.
    #[test]
    fn test_toolkit_global_parts() {
        if !gtk_here() {
            return; // (no display: a private Broadway one runs it)
        }
        setup();

        // Vini (Python, 2026-09-29): a slider moved under the mouse wheel
        // instead of the page scrolling; the Rust session had no hook
        let win = gtk::Window::new();
        let scale = gtk::Scale::with_range(gtk::Orientation::Horizontal, 0.0, 1.0, 0.1);
        win.set_child(Some(&scale));
        win.present();
        let ctrls = scale.observe_controllers();
        let scrolls = (0..ctrls.n_items()).filter(|i| ctrls.item(*i).and_downcast::<gtk::EventControllerScroll>().is_some()).count();
        assert!(scale.is_realized());
        assert_eq!(scrolls, 0, "test_slider_moved_under_the_wheel");

        // the Dock writing its recents to dock.json is no change of look:
        // no cross-fade of every window (theme._remember_appearance)
        sonata_config::update("dock", &json!({"recent": ["firefox"]})).unwrap();
        dock_changed();
        assert!(STATE.with(|s| s.borrow().fade.is_none()), "test_dock_recents_cross_faded_every_window");

        // an accent picked in Settings cross-faded nothing in the Rust
        // session (the fade wasn't ported): it does now
        let before = rgba("accent");
        sonata_config::update("appearance", &json!({"accent": "#ff0000"})).unwrap();
        appearance_changed();
        assert!(STATE.with(|s| s.borrow().fade.is_some()), "test_accent_change_did_not_cross_fade");
        let after = values().get("accent").cloned();
        assert_eq!(after.as_deref(), Some("#ff0000"));
        // at the start of the fade the colour is still (nearly) the old one
        let now = rgba("accent");
        assert!((now.red() - before.red()).abs() < 0.2 && now.green() < 0.9, "{now:?} from {before:?}");

        // menus (menu.py): a long row ends in "…" (Vini: a Chrome window named
        // after a whole YouTube title made the Window menu 830 px wide) --
        // GTK's own row labels weren't capped in Rust; an open menu counts
        // for the auto-hiding Dock and menu bar
        let anchor = gtk::Button::with_label("x");
        let bx = gtk::Box::new(gtk::Orientation::Vertical, 0);
        bx.append(&anchor);
        win.set_child(Some(&bx));
        let long = "A very long window title that goes on and on, like a whole video's name";
        let pop = crate::menu::popup(&anchor, vec![vec![crate::menu::Item::new(long, || {})]], crate::menu::Options::below());
        let mut labels = Vec::new();
        fn find(w: &gtk::Widget, out: &mut Vec<gtk::Label>) {
            if let Some(l) = w.downcast_ref::<gtk::Label>() {
                out.push(l.clone());
            }
            let mut c = w.first_child();
            while let Some(x) = c {
                find(&x, out);
                c = x.next_sibling();
            }
        }
        find(pop.upcast_ref(), &mut labels);
        let row = labels.iter().find(|l| l.label() == long).expect("the row's label");
        assert_eq!(row.ellipsize(), gtk::pango::EllipsizeMode::End, "test_long_menu_items_widened_the_menu");
        assert!(crate::menu::any_open());
        let closed = std::rc::Rc::new(std::cell::Cell::new(false));
        let c = closed.clone();
        crate::menu::on_closed(move || c.set(true));
        pop.popdown();
        assert!(!crate::menu::any_open());
        assert!(closed.get());
        win.destroy();

        // until laid out (a fade needs the window's content at its size)
        let spin = |w: &gtk::Button| {
            for _ in 0..500 {
                while gtk::glib::MainContext::default().iteration(false) {}
                if w.width() > 0 {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(2));
            }
        };
        let unrealized = |w: &gtk::Button| {
            let n = std::rc::Rc::new(std::cell::Cell::new(0));
            let c = n.clone();
            w.connect_unrealize(move |_| c.set(c.get() + 1));
            n
        };

        // every change of look wrapped an app window in one more overlay,
        // taking its content out and putting it back each time (its hover
        // and focus lost on every Dark Mode switch)
        let app_win = gtk::Window::new();
        let inner = gtk::Button::with_label("in");
        app_win.set_child(Some(&inner));
        app_win.present();
        spin(&inner);
        assert!(fades(&app_win), "an app window fades");
        fade_window(&app_win);
        assert!(app_win.child().is_some_and(|c| c.is::<gtk::Overlay>()), "faded");
        let out = unrealized(&inner);
        // (the next change of look: laid out again by then)
        fade_overlay(&app_win);
        let top = content(&app_win).and_downcast::<gtk::Overlay>().expect("in the fade's overlay");
        assert_eq!(top.child().as_ref(), Some(inner.upcast_ref()), "test_every_fade_wrapped_the_window_again");
        assert_eq!(out.get(), 0, "test_every_fade_wrapped_the_window_again: taken out again");
        app_win.destroy();

        // Spotlight, the app switcher and Notification Center cross-fade as
        // in Python; skipped in Rust, as the fade took their content out of
        // the window and put it back under the pointer, which lost its hover
        let shell = gtk::Window::builder().decorated(false).build();
        let row = gtk::Button::with_label("a result");
        shell.set_child(Some(&row));
        fade_too(&shell);
        let out = unrealized(&row);
        shell.present();
        spin(&row);
        assert!(fades(&shell), "test_shell_overlays_did_not_cross_fade");
        row.set_state_flags(gtk::StateFlags::PRELIGHT, false); // (the pointer on it)
        fade_window(&shell);
        let over = content(&shell).and_downcast::<gtk::Overlay>().expect("in the fade's overlay");
        assert!(over.last_child().is_some_and(|p| p.is::<gtk::Picture>() && !p.can_target()), "the old look over it, letting the pointer through");
        assert_eq!(out.get(), 0, "test_shell_overlay_lost_its_hover_in_a_fade: taken out of the window");
        assert!(row.state_flags().contains(gtk::StateFlags::PRELIGHT), "test_shell_overlay_lost_its_hover_in_a_fade");
        no_fade(&shell);
        assert!(!fades(&shell), "no_fade wins");
        shell.destroy();

        // the Dock and the menu bar (undecorated, no opt-in) fade their own
        // colours: no picture over them
        let bar = gtk::Window::builder().decorated(false).child(&gtk::Label::new(Some("bar"))).build();
        bar.present();
        while gtk::glib::MainContext::default().iteration(false) {}
        assert!(!fades(&bar), "test_dock_cross_faded_as_a_picture");
        bar.destroy();
    }

    #[test]
    fn fades_ease_in_and_out() {
        assert_eq!(ease(0.0), 0.0);
        assert_eq!(ease(1.0), 1.0);
        assert_eq!(ease(0.5), 0.5);
        assert!(ease(0.1) < 0.1 && ease(0.9) > 0.9);
    }
}
