//! First-run Setup Assistant (macOS Setup Assistant's last screens;
//! shell/setup.py).
//!
//! After the first login into Sonata, over the blurred wallpaper (the login
//! screen's look): a "hello" in several languages, Choose Your Look (Light /
//! Dark, accent colour), how to see the apps (full screen or the Apps
//! Menu), a few Sonata choices, then "Get Started". Every choice applies at
//! once and stays in Settings. Shown once: setup.json "done".
//! `sonata2-rs setup` shows it while it isn't done; `--preview` any time.
//!
//! On trial the choices land in the trial's own settings folder
//! (~/.config/sonata2-rs): the Python Sonata keeps its own.

use adw::prelude::*;
use gtk::gdk;
use serde_json::{json, Value};
use sonata_desktop::names;
use sonata_i18n::{fmt, gettext, n_, tr};
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

pub const NAME: &str = "setup";
pub const HELLOS: [&str; 10] = ["hello", "olá", "hola", "bonjour", "ciao", "hallo", "こんにちは", "안녕하세요", "привет", "你好"];
pub const HELLO_MS: u64 = 1800;
pub const ACCENTS: [(&str, &str); 8] = [("blue", n_!("Blue")), ("purple", n_!("Purple")), ("pink", n_!("Pink")), ("red", n_!("Red")), ("orange", n_!("Orange")), ("yellow", n_!("Yellow")), ("green", n_!("Green")), ("graphite", n_!("Graphite"))];
pub const PAGES: [&str; 5] = ["hello", "look", "apps", "sonata", "ready"];
/// launchpad_window.py: launcher.json "style"
pub const LAUNCHER: &str = "launcher";
pub const STYLES: [(&str, &str); 2] = [("fullscreen", n_!("Full Screen")), ("window", names::APPS_MENU)];
/// once the Dock and menu bar have slid in
const AFTER_INTRO_MS: u64 = 900;
const INTRO_TIMEOUT_S: u64 = 12;

pub fn done() -> bool {
    sonata_config::load(NAME, &json!({"done": false})).get("done").is_some_and(crate::truthy)
}

/// Full Screen or the Apps Menu (launchpad_window.style()).
pub fn apps_style() -> String {
    let s = sonata_config::load(LAUNCHER, &json!({"style": "fullscreen"})).get("style").and_then(Value::as_str).unwrap_or("").to_string();
    if STYLES.iter().any(|(k, _)| *k == s) { s } else { "fullscreen".into() }
}

/// What the assistant needs from parts ported elsewhere.
#[derive(Clone)]
pub struct Hooks {
    /// Dark Mode for everything (backend/system.py set_dark_mode: sonata-system).
    pub set_dark_mode: Rc<dyn Fn(bool)>,
    /// Sonata's title bars for other apps (titlebars.apply: sonata-tweaks).
    pub apply_titlebars: Rc<dyn Fn(bool)>,
}

impl Default for Hooks {
    /// The real ones, off the main loop (system.run_async in the Python).
    /// They did nothing here once: picking Dark in the Rust session's
    /// assistant only moved the white ring, the desktop stayed light.
    fn default() -> Self {
        Hooks {
            set_dark_mode: Rc::new(|on| {
                std::thread::spawn(move || set_dark_mode(on));
            }),
            apply_titlebars: Rc::new(|on| {
                std::thread::spawn(move || sonata_tweaks::titlebars::apply(Some(on)));
            }),
        }
    }
}

const IFACE: &str = "org.gnome.desktop.interface";

/// What system.set_dark_mode stores: libadwaita / GTK 4 apps follow
/// color-scheme, GTK 3 apps the theme's name.
pub fn dark_mode_prefs(on: bool) -> [(&'static str, &'static str); 2] {
    [("color-scheme", if on { "prefer-dark" } else { "default" }), ("gtk-theme", if on { "Sonata-Dark" } else { "Sonata-Light" })]
}

/// system.set_dark_mode: Sonata and every app, Flatpak apps too (gtkstyle.update).
pub fn set_dark_mode(on: bool) {
    for (k, v) in dark_mode_prefs(on) {
        sonata_tweaks::prefs_write::set(IFACE, k, v);
    }
    sonata_tweaks::flatpak_theme::apply();
}

fn css() {
    thread_local!(static DONE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) });
    if DONE.with(|d| d.replace(true)) {
        return;
    }
    sonata_shell::loginui::register_css();
    sonata_ui::css::register_module("shell/setup.py");
    // the accent dots' colours (computed in Python: "".join(... ACCENTS))
    let dots: String = ACCENTS.iter().map(|(n, _)| format!(".su-dot.{n} {{ background: {}; }}\n", accent_color(n))).collect();
    sonata_ui::register("setup-dots", &dots);
}

/// An accent's light colour (tokens.ACCENTS[name][0]; blue is the palette's own).
pub fn accent_color(name: &str) -> String {
    sonata_ui::tokens::accent_tokens(name, false).get("accent").cloned().or_else(|| sonata_ui::tokens::palette(false).get("accent").cloned()).unwrap_or_default()
}

/// The texture as a cairo surface, once (the previews paint it).
fn surface_of(texture: &gdk::Texture) -> Option<gtk::cairo::ImageSurface> {
    let (w, h) = (texture.width(), texture.height());
    let mut s = gtk::cairo::ImageSurface::create(gtk::cairo::Format::ARgb32, w, h).ok()?;
    let stride = s.stride() as usize;
    {
        let mut data = s.data().ok()?;
        texture.download(&mut data, stride); // (B8g8r8a8 premultiplied: cairo's ARGB32 here)
    }
    s.mark_dirty();
    Some(s)
}

fn rgba(cr: &gtk::cairo::Context, spec: &str) {
    let c = gdk::RGBA::parse(spec).unwrap_or(gdk::RGBA::BLACK);
    cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64);
}

fn rounded(cr: &gtk::cairo::Context, x: f64, y: f64, w: f64, h: f64, r: f64, color: &str) {
    let r = r.min(w / 2.0).min(h / 2.0);
    use std::f64::consts::PI;
    cr.new_sub_path();
    cr.arc(x + w - r, y + r, r, -PI / 2.0, 0.0);
    cr.arc(x + w - r, y + h - r, r, 0.0, PI / 2.0);
    cr.arc(x + r, y + h - r, r, PI / 2.0, PI);
    cr.arc(x + r, y + r, r, PI, 1.5 * PI);
    cr.close_path();
    rgba(cr, color);
    let _ = cr.fill();
}

fn cover(cr: &gtk::cairo::Context, s: &Option<gtk::cairo::ImageSurface>, w: f64, h: f64) {
    let Some(s) = s else { return };
    let (tw, th) = (s.width() as f64, s.height() as f64);
    let k = (w / tw).max(h / th);
    let _ = cr.save();
    cr.translate((w - tw * k) / 2.0, (h - th * k) / 2.0);
    cr.scale(k, k);
    let _ = cr.set_source_surface(s, 0.0, 0.0);
    let _ = cr.paint();
    let _ = cr.restore();
}

/// A small desktop in one appearance: the wallpaper, the menu bar, a
/// window and the Dock, in that appearance's tokens.
fn look_preview(tex: Rc<Option<gtk::cairo::ImageSurface>>, dark: bool) -> gtk::DrawingArea {
    let a = gtk::DrawingArea::builder().css_classes(["su-preview"]).overflow(gtk::Overflow::Hidden).width_request(200).height_request(125).build();
    a.set_draw_func(move |_, cr, w, h| {
        let (w, h) = (w as f64, h as f64);
        let t = sonata_ui::tokens::palette(dark);
        let tok = |k: &str| t.get(k).cloned().unwrap_or_default();
        cover(cr, &tex, w, h);
        if dark {
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.28);
            let _ = cr.paint();
        }
        rgba(cr, &tok("bar_bg")); // menu bar
        cr.rectangle(0.0, 0.0, w, 8.0);
        let _ = cr.fill();
        let (wx, wy, ww, wh) = (w * 0.16, h * 0.2, w * 0.56, h * 0.5); // a window
        rounded(cr, wx, wy, ww, wh, 5.0, &tok("window_bg"));
        rounded(cr, wx, wy, ww * 0.3, wh, 5.0, &tok("solid_tint"));
        for (i, c) in ["#ff5f57", "#febc2e", "#28c840"].iter().enumerate() {
            rounded(cr, wx + 5.0 + i as f64 * 6.0, wy + 4.0, 4.0, 4.0, 2.0, c);
        }
        rounded(cr, w * 0.28, h - 16.0, w * 0.44, 11.0, 4.0, &tok("glass_tint")); // Dock
    });
    a
}

const APP_COLORS: [&str; 7] = ["#ff9f0a", "#30d158", "#0a84ff", "#bf5af2", "#ff375f", "#64d2ff", "#ffd60a"];

/// The two ways to see the apps (Settings > Appearance > Apps style): over
/// the whole screen, or a panel in the middle (the Apps Menu).
fn apps_preview(tex: Rc<Option<gtk::cairo::ImageSurface>>, kind: &'static str) -> gtk::DrawingArea {
    let a = gtk::DrawingArea::builder().css_classes(["su-preview"]).overflow(gtk::Overflow::Hidden).width_request(200).height_request(125).build();
    a.set_draw_func(move |_, cr, w, h| {
        let (w, h) = (w as f64, h as f64);
        let t = sonata_ui::tokens::palette(adw::StyleManager::default().is_dark());
        let tok = |k: &str| t.get(k).cloned().unwrap_or_default();
        cover(cr, &tex, w, h);
        let (x0, y0, cols, rows, cell, icon);
        if kind == "fullscreen" {
            // dimmed screen, a big grid
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.38);
            let _ = cr.paint();
            (x0, y0, cols, rows, cell, icon) = (w * 0.14, h * 0.2, 6, 3, w * 0.12, w * 0.07);
        } else {
            // a panel in the middle, smaller icons
            rgba(cr, &tok("bar_bg"));
            cr.rectangle(0.0, 0.0, w, 8.0);
            let _ = cr.fill();
            let (px, py, pw, ph) = (w * 0.2, h * 0.17, w * 0.6, h * 0.62);
            rounded(cr, px, py, pw, ph, 7.0, &tok("menu_bg"));
            rounded(cr, px + 6.0, py + 6.0, pw * 0.4, 4.0, 2.0, &tok("separator"));
            cols = 6;
            rows = 3;
            x0 = px + 8.0;
            y0 = py + 18.0;
            cell = (pw - 16.0) / cols as f64;
            icon = cell * 0.62;
        }
        for i in 0..cols * rows {
            let (c, r) = (i % cols, i / cols);
            rounded(cr, x0 + c as f64 * cell + (cell - icon) / 2.0, y0 + r as f64 * cell * 0.95, icon, icon, icon * 0.24, APP_COLORS[i % APP_COLORS.len()]);
        }
        rounded(cr, w * 0.28, h - 16.0, w * 0.44, 11.0, 4.0, &tok("glass_tint")); // Dock
    });
    a
}

/// The assistant's window and what was chosen in it.
pub struct SetupAssistant {
    pub win: gtk::Window,
    pub stack: gtk::Stack,
    pub cards: RefCell<HashMap<bool, gtk::Button>>,
    pub dots: RefCell<HashMap<&'static str, gtk::Button>>,
    pub app_cards: RefCell<HashMap<&'static str, gtk::Button>>,
    hooks: Hooks,
    on_done: Box<dyn Fn()>,
    me: std::rc::Weak<SetupAssistant>,
}

impl SetupAssistant {
    pub fn new(app: &gtk::Application, hooks: Hooks, on_done: impl Fn() + 'static) -> Rc<Self> {
        css();
        let tex = sonata_shell::loginui::wallpaper_texture(1600);
        let win = gtk::Window::builder().application(app).decorated(false).title(tr!("Setup Assistant").as_str()).css_classes(["sonata-lock"]).build();
        let stack = gtk::Stack::builder().transition_type(gtk::StackTransitionType::SlideLeftRight).transition_duration(420).hexpand(true).vexpand(true).build();
        let me = Rc::new_cyclic(|w| SetupAssistant { win: win.clone(), stack: stack.clone(), cards: RefCell::default(), dots: RefCell::default(), app_cards: RefCell::default(), hooks, on_done: Box::new(on_done), me: w.clone() });
        let small = Rc::new(sonata_shell::loginui::wallpaper_texture(480).as_ref().and_then(surface_of));
        let over = gtk::Overlay::new();
        over.set_child(Some(&sonata_shell::loginui::Backdrop::new(tex, 0.22, 64.0)));
        stack.add_named(&me.hello(), Some("hello"));
        stack.add_named(&me.look(small.clone()), Some("look"));
        stack.add_named(&me.apps(small), Some("apps"));
        stack.add_named(&me.sonata(), Some("sonata"));
        stack.add_named(&me.ready(), Some("ready"));
        over.add_overlay(&stack);
        win.set_child(Some(&over));
        let keys = gtk::EventControllerKey::new();
        let w = me.me.clone();
        keys.connect_key_pressed(move |_, key, _, _| {
            let Some(me) = w.upgrade() else { return glib::Propagation::Proceed };
            match key {
                gdk::Key::Return | gdk::Key::KP_Enter | gdk::Key::Right => {
                    if me.stack.visible_child_name().as_deref() == Some("ready") {
                        me.finish();
                    } else {
                        me.go(1);
                    }
                    glib::Propagation::Stop
                }
                gdk::Key::Left | gdk::Key::BackSpace => {
                    me.go(-1);
                    glib::Propagation::Stop
                }
                _ => glib::Propagation::Proceed,
            }
        });
        win.add_controller(keys);
        if !sonata_shell::layer::overlay_fullscreen(&win, "sonata2-setup") {
            win.fullscreen();
        }
        win.present();
        me
    }

    fn nav_button(&self, icon: &str, tip: &str, step: i32) -> gtk::Button {
        let b = gtk::Button::builder().icon_name(icon).css_classes(["su-next"]).tooltip_text(tip).build();
        let w = self.me.clone();
        b.connect_clicked(move |_| {
            if let Some(me) = w.upgrade() {
                me.go(step);
            }
        });
        b
    }

    fn page(&self, title: &str, text: &str, body: Option<gtk::Widget>, back: bool, next: bool) -> gtk::Box {
        let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(12).halign(gtk::Align::Center).valign(gtk::Align::Center).css_classes(["gr-rise"]).build();
        col.append(&gtk::Label::builder().label(title).css_classes(["su-title"]).build());
        col.append(&gtk::Label::builder().label(text).css_classes(["su-text"]).wrap(true).max_width_chars(56).justify(gtk::Justification::Center).build());
        if let Some(b) = body {
            b.set_margin_top(18);
            col.append(&b);
        }
        let nav = gtk::Box::builder().spacing(16).halign(gtk::Align::Center).margin_top(28).build();
        if back {
            nav.append(&self.nav_button("go-previous-symbolic", &tr!("Back"), -1));
        }
        if next {
            nav.append(&self.nav_button("go-next-symbolic", &tr!("Continue"), 1));
        }
        col.append(&nav);
        col
    }

    fn hello(&self) -> gtk::Box {
        let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).halign(gtk::Align::Center).valign(gtk::Align::Center).css_classes(["gr-fade-in"]).build();
        let words = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(700).hhomogeneous(false).interpolate_size(true).build();
        for (i, w) in HELLOS.iter().enumerate() {
            words.add_named(&gtk::Label::builder().label(*w).css_classes(["su-hello"]).build(), Some(&i.to_string()));
        }
        col.append(&words);
        col.append(&gtk::Label::builder().label(tr!("Welcome to Sonata").as_str()).css_classes(["su-text"]).build());
        let i = std::cell::Cell::new(0usize);
        let stack = self.stack.downgrade();
        glib::timeout_add_local(std::time::Duration::from_millis(HELLO_MS), move || {
            let Some(stack) = stack.upgrade() else { return glib::ControlFlow::Break };
            if stack.visible_child_name().as_deref() == Some("hello") {
                i.set((i.get() + 1) % HELLOS.len());
                words.set_visible_child_name(&i.get().to_string());
            }
            glib::ControlFlow::Continue
        });
        let n = self.nav_button("go-next-symbolic", &tr!("Continue"), 1);
        n.set_halign(gtk::Align::Center);
        n.set_margin_top(36);
        col.append(&n);
        col
    }

    fn card(&self, child: &gtk::Widget, label: &str, selected: bool) -> gtk::Button {
        let b = gtk::Box::new(gtk::Orientation::Vertical, 8);
        b.append(child);
        b.append(&gtk::Label::builder().label(label).css_classes(["su-caption"]).build());
        let btn = gtk::Button::builder().child(&b).css_classes(["su-card"]).build();
        if selected {
            btn.add_css_class("selected");
        }
        btn
    }

    fn look(&self, tex: Rc<Option<gtk::cairo::ImageSurface>>) -> gtk::Box {
        let body = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(22).halign(gtk::Align::Center).build();
        let cards = gtk::Box::builder().spacing(24).halign(gtk::Align::Center).build();
        let dark_now = adw::StyleManager::default().is_dark();
        for (dark, label) in [(false, tr!("Light")), (true, tr!("Dark"))] {
            let b = self.card(look_preview(tex.clone(), dark).upcast_ref(), &label, dark == dark_now);
            let w = self.me.clone();
            b.connect_clicked(move |_| {
                if let Some(me) = w.upgrade() {
                    me.set_dark(dark);
                }
            });
            cards.append(&b);
            self.cards.borrow_mut().insert(dark, b);
        }
        body.append(&cards);
        let dots = gtk::Box::builder().halign(gtk::Align::Center).build();
        let cur = sonata_config::load("appearance", &json!({"accent": "blue"})).get("accent").and_then(Value::as_str).unwrap_or("blue").to_string();
        for (name, label) in ACCENTS {
            let d = gtk::Button::builder().css_classes(["su-dot", name]).tooltip_text(gettext(label).as_str()).build();
            if name == cur {
                d.add_css_class("selected");
            }
            let w = self.me.clone();
            d.connect_clicked(move |_| {
                if let Some(me) = w.upgrade() {
                    me.set_accent(name);
                }
            });
            dots.append(&d);
            self.dots.borrow_mut().insert(name, d);
        }
        let acc = gtk::Box::new(gtk::Orientation::Vertical, 8);
        acc.append(&gtk::Label::builder().label(tr!("Accent Colour").as_str()).css_classes(["su-caption"]).build());
        acc.append(&dots);
        body.append(&acc);
        self.page(&tr!("Choose Your Look"), &tr!("Pick an appearance. You can change it later in Settings > Appearance."), Some(body.upcast()), true, true)
    }

    /// Vini: the first setup asks how to see the apps (full screen or the Apps Menu).
    fn apps(&self, tex: Rc<Option<gtk::cairo::ImageSurface>>) -> gtk::Box {
        let cards = gtk::Box::builder().spacing(24).halign(gtk::Align::Center).build();
        let cur = apps_style();
        for (kind, label) in STYLES {
            let b = self.card(apps_preview(tex.clone(), kind).upcast_ref(), &gettext(label), kind == cur);
            let w = self.me.clone();
            b.connect_clicked(move |_| {
                if let Some(me) = w.upgrade() {
                    me.set_apps_style(kind);
                }
            });
            cards.append(&b);
            self.app_cards.borrow_mut().insert(kind, b);
        }
        let title = fmt(&tr!("Choose How to See Your {apps}"), &[("apps", &gettext(names::APPS))]);
        let text = fmt(&tr!("Over the whole screen, or a smaller {apps_menu} in the middle that can also sort them by category. You can change it later in Settings > Appearance."), &[("apps_menu", &gettext(names::APPS_MENU))]);
        self.page(&title, &text, Some(cards.upcast()), true, true)
    }

    fn sonata(&self) -> gtk::Box {
        let panel = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["su-panel"]).width_request(460).build();
        let app_cfg = sonata_config::load("appearance", &json!({"system_titlebars": true}));
        let dock = sonata_config::load("dock", &json!({"click_minimizes": true, "autohide": false}));
        let on = |m: &serde_json::Map<String, Value>, k: &str| m.get(k).is_some_and(crate::truthy);
        let titlebars = self.hooks.apply_titlebars.clone();
        let rows: [(String, String, bool, Box<dyn Fn(bool)>); 3] = [
            (
                tr!("Sonata title bars for all apps"),
                tr!("Chrome, VS Code, Firefox and others match Sonata's windows"),
                on(&app_cfg, "system_titlebars"),
                Box::new(move |v| {
                    let _ = sonata_config::update("appearance", &json!({"system_titlebars": v}));
                    titlebars(v);
                }),
            ),
            (tr!("Click an app in the Dock to minimize it"), tr!("Its windows go back into the Dock"), on(&dock, "click_minimizes"), Box::new(|v| drop(sonata_config::update("dock", &json!({"click_minimizes": v}))))),
            (tr!("Automatically hide the Dock"), tr!("It slides in when the pointer reaches the screen edge"), on(&dock, "autohide"), Box::new(|v| drop(sonata_config::update("dock", &json!({"autohide": v}))))),
        ];
        for (i, (title, sub, active, cb)) in rows.into_iter().enumerate() {
            if i > 0 {
                panel.append(&gtk::Separator::builder().margin_start(16).margin_end(16).build());
            }
            let row = gtk::Box::builder().spacing(16).css_classes(["su-row"]).build();
            let text = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(2).hexpand(true).valign(gtk::Align::Center).build();
            text.append(&gtk::Label::builder().label(title.as_str()).xalign(0.0).css_classes(["su-row-title"]).build());
            text.append(&gtk::Label::builder().label(sub.as_str()).xalign(0.0).css_classes(["su-row-sub"]).wrap(true).build());
            row.append(&text);
            let sw = gtk::Switch::builder().active(active).valign(gtk::Align::Center).build();
            sw.connect_active_notify(move |s| cb(s.is_active()));
            row.append(&sw);
            panel.append(&row);
        }
        self.page(&tr!("Make It Yours"), &tr!("A few of Sonata's choices. Everything else is in Settings."), Some(panel.upcast()), true, true)
    }

    fn ready(&self) -> gtk::Box {
        let start = gtk::Button::builder().label(tr!("Get Started").as_str()).css_classes(["sonata-button", "default", "su-start"]).halign(gtk::Align::Center).build();
        let w = self.me.clone();
        start.connect_clicked(move |_| {
            if let Some(me) = w.upgrade() {
                me.finish();
            }
        });
        let bx = gtk::Box::new(gtk::Orientation::Vertical, 10);
        bx.append(&start);
        let text = fmt(&tr!("Open {apps} with the Super key, {search} with Super+Space, and {overview} with F3."), &[("apps", &gettext(names::APPS)), ("search", &gettext(names::SEARCH)), ("overview", &gettext(names::OVERVIEW))]);
        self.page(&tr!("You're All Set"), &text, Some(bx.upcast()), true, false)
    }

    // -- choices ----------------------------------------------------------------------

    pub fn set_dark(&self, dark: bool) {
        for (d, b) in self.cards.borrow().iter() {
            if *d == dark { b.add_css_class("selected") } else { b.remove_css_class("selected") }
        }
        (self.hooks.set_dark_mode)(dark);
    }

    pub fn set_accent(&self, name: &str) {
        for (n, b) in self.dots.borrow().iter() {
            if *n == name { b.add_css_class("selected") } else { b.remove_css_class("selected") }
        }
        let _ = sonata_config::update("appearance", &json!({"accent": name}));
    }

    pub fn set_apps_style(&self, kind: &str) {
        for (k, b) in self.app_cards.borrow().iter() {
            if *k == kind { b.add_css_class("selected") } else { b.remove_css_class("selected") }
        }
        let _ = sonata_config::update(LAUNCHER, &json!({"style": kind}));
    }

    // -- navigation -------------------------------------------------------------------

    pub fn go(&self, step: i32) {
        let cur = self.stack.visible_child_name().map(|n| n.to_string()).unwrap_or_default();
        let i = PAGES.iter().position(|p| *p == cur).unwrap_or(0) as i32 + step;
        if (0..PAGES.len() as i32).contains(&i) {
            self.stack.set_transition_type(if step > 0 { gtk::StackTransitionType::SlideLeft } else { gtk::StackTransitionType::SlideRight });
            self.stack.set_visible_child_name(PAGES[i as usize]);
        }
    }

    pub fn finish(&self) {
        let _ = sonata_config::update(NAME, &json!({"done": true}));
        self.stack.add_css_class("gr-leave");
        let start = glib::monotonic_time();
        let w = self.me.clone();
        self.win.add_tick_callback(move |win, _| {
            let t = ((glib::monotonic_time() - start) as f64 / 420_000.0).min(1.0);
            win.set_opacity(1.0 - t * t);
            if t >= 1.0 {
                win.set_visible(false);
                if let Some(me) = w.upgrade() {
                    (me.on_done)();
                }
                return glib::ControlFlow::Break;
            }
            glib::ControlFlow::Continue
        });
    }
}

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.setup"
    } else {
        "io.github.vinioliveiras.sonata2.setup"
    }
}

/// after a restart: never wait longer than this for it (intro.py's RESTART_TIMEOUT_S)
const RESTART_TIMEOUT_S: u64 = 6;
/// after a restart: from all ready to sliding in (intro.py's ARRIVE_MS)
const ARRIVE_MS: u64 = 120;

/// What intro.wait waits for: the login intro (`pending`), else a restart
/// under way, with their timeouts; None: nothing, go now.
fn intro_wait(pending: bool, restarting: bool) -> Option<(fn() -> bool, u64, u64)> {
    if pending {
        Some((sonata_shell::intro::pending, INTRO_TIMEOUT_S, 0))
    } else if restarting {
        Some((sonata_shell::intro::restarting, RESTART_TIMEOUT_S, ARRIVE_MS))
    } else {
        None
    }
}

/// callback once the login intro is over (right away without one; never
/// later than its timeout) -- intro.wait. After a restart: once the
/// restart is over too (the Dock and the menu bar slide in first).
fn after_intro(callback: impl FnOnce() + 'static) {
    let Some((still, timeout_s, then_ms)) = intro_wait(sonata_shell::intro::pending(), sonata_shell::intro::restarting()) else {
        glib::idle_add_local_once(callback);
        return;
    };
    let cb = RefCell::new(Some(callback));
    let start = std::time::Instant::now();
    glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
        if still() && start.elapsed().as_secs() < timeout_s {
            return glib::ControlFlow::Continue;
        }
        if let Some(f) = cb.borrow_mut().take() {
            glib::timeout_add_local_once(std::time::Duration::from_millis(then_ms), f);
        }
        glib::ControlFlow::Break
    });
}

/// `sonata2-rs setup [--preview]`: the Setup Assistant, after the login
/// intro, while it isn't done (`--preview`: any time).
pub fn run_setup(args: &[String], hooks: Hooks) -> glib::ExitCode {
    let preview = args.iter().any(|a| a == "--preview");
    if done() && !preview {
        return glib::ExitCode::SUCCESS;
    }
    sonata_shell::layer::ensure_preload();
    let app = adw::Application::builder().application_id(app_id()).build();
    let state: Rc<RefCell<Option<Rc<SetupAssistant>>>> = Rc::default();
    app.connect_activate(move |app| {
        if state.borrow().is_some() {
            return;
        }
        sonata_ui::setup();
        let guard = app.hold();
        let (app, state, hooks) = (app.clone(), state.clone(), hooks.clone());
        let holder = Rc::new(RefCell::new(Some(guard)));
        after_intro(move || {
            glib::timeout_add_local_once(std::time::Duration::from_millis(AFTER_INTRO_MS), move || {
                let h = holder.clone();
                let a = SetupAssistant::new(app.upcast_ref(), hooks, move || drop(h.borrow_mut().take()));
                *state.borrow_mut() = Some(a);
            });
        });
    });
    app.run_with_args(&[] as &[&str])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn done_once_and_styles() {
        let _e = crate::testenv::env();
        assert!(!done());
        assert_eq!(apps_style(), "fullscreen");
        sonata_config::update(LAUNCHER, &json!({"style": "window"})).unwrap();
        assert_eq!(apps_style(), "window");
        sonata_config::update(LAUNCHER, &json!({"style": "nonsense"})).unwrap();
        assert_eq!(apps_style(), "fullscreen");
        sonata_config::update(NAME, &json!({"done": true})).unwrap();
        assert!(done());
        assert_eq!(PAGES, ["hello", "look", "apps", "sonata", "ready"]); // Vini: it asks how to see the apps
        assert_eq!(STYLES.map(|s| s.0), ["fullscreen", "window"]);
    }

    /// The Rust session's Setup Assistant: Dark was picked, the ring moved
    /// to it, and the desktop stayed light -- the hooks did nothing unless
    /// the caller filled them, and sonata2-rs never did.
    #[test]
    fn test_dark_choice_in_setup_did_nothing() {
        assert_eq!(dark_mode_prefs(true), [("color-scheme", "prefer-dark"), ("gtk-theme", "Sonata-Dark")]);
        assert_eq!(dark_mode_prefs(false), [("color-scheme", "default"), ("gtk-theme", "Sonata-Light")]);
        // the defaults are the real ones now: a call reaches set_dark_mode
        // (checked by what it stores, in a thread of its own)
        let _e = crate::testenv::env();
        let path = std::env::var_os("PATH");
        std::env::set_var("PATH", ""); // no gsettings, no flatpak run
        set_dark_mode(true);
        if let Some(p) = path {
            std::env::set_var("PATH", p);
        }
        let data = sonata_config::prefs::load();
        assert_eq!(sonata_config::prefs::get(IFACE, "color-scheme", "", &data), "prefer-dark");
        assert_eq!(sonata_config::prefs::get(IFACE, "gtk-theme", "", &data), "Sonata-Dark");
    }

    /// intro.wait: the login intro first; after a restart, the restart.
    #[test]
    fn waits_like_intro_wait() {
        assert!(intro_wait(false, false).is_none());
        assert_eq!(intro_wait(true, true).map(|w| (w.1, w.2)), Some((INTRO_TIMEOUT_S, 0)));
        assert_eq!(intro_wait(false, true).map(|w| (w.1, w.2)), Some((RESTART_TIMEOUT_S, ARRIVE_MS)));
    }

    #[test]
    fn every_accent_has_its_dot() {
        assert_eq!(accent_color("blue"), "#007aff"); // tokens.ACCENTS
        assert_eq!(accent_color("green"), "#62ba46");
        for (n, _) in ACCENTS {
            assert!(accent_color(n).starts_with('#'), "{n}");
        }
    }
}
