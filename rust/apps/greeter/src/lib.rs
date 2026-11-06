//! The login screen (sonata2/shell/greeter.py): `sonata2-rs greeter`, run
//! by greetd inside a small Wayfire (install.sh --greeter). The lock
//! screen's look (loginui): the user's wallpaper blurred, their picture
//! and name, a capsule password field; Sleep / Restart / Shut Down at the
//! bottom like macOS.
//!
//! - Users: AccountsService (real name, picture), else /etc/passwd.
//!   One user: straight to the password. Several: a grid of pictures first.
//! - Sessions: /usr/share/wayland-sessions; Sonata first. A small menu under
//!   the field when there is more than one. Remembered per user.
//! - Wallpaper: the one the user's Sonata session copied to
//!   /var/lib/sonata-greeter/<user>/wallpaper (wallpaper.py), else a gradient.
//! - State (last user, sessions, display modes): /var/cache/sonata-greeter/state.json.
//! - A user already logged in on this seat goes back to that session
//!   (Switch User), the password asked once.
//! - Their video wallpaper (wallpaper-video beside the picture) plays
//!   under the blur, silent; the screen corners are always drawn (the
//!   login screen has no settings).
//! - Left alone for the user's Settings > Battery time, the screen goes
//!   dark by the compositor's input idle (idlewatch): black, the panel's
//!   backlight at zero, plugged-in monitors off; any key or move brings it
//!   back. A compositor without it: this process's own timer.
//!
//! SONATA_GREETER_FAKE=1: no greetd (previews; password "sonata"); Esc leaves.

pub mod data;
pub mod greetd;
pub mod userswitch;

use adw::prelude::*;
use data::Session;
use serde_json::{json, Map, Value};
use sonata_i18n::tr;
use sonata_shell::logins::{self, User};
use sonata_shell::idlewatch::IdleWatch;
use sonata_shell::lockvideo::LockVideo;
use sonata_shell::{layer, lockdisplay, loginui, screencorners, throttle};
use sonata_ui::menu::{self, Item, Options};
use sonata_ui::progress::Spinner;
use std::cell::{Cell, RefCell};
use std::path::Path;
use std::rc::Rc;
use std::time::Duration;

/// the wallpaper's longest side for the (blurred) backdrop
const WALLPAPER_PX: i32 = 1280;

pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.greeter"
}

/// A previewing login screen: no greetd, no power buttons, password "sonata".
pub fn fake() -> bool {
    std::env::var_os("SONATA_GREETER_FAKE").is_some_and(|v| !v.is_empty())
}

/// The login's controls on one display.
struct LoginPage {
    column: gtk::Box,
    entry: gtk::PasswordEntry,
    spinner: Spinner,
    slot: gtk::Stack,
    links: gtk::Box,
    hint: gtk::Label,
    session_button: Option<gtk::Button>,
}

/// One display's copy of the login screen (Vini: on every monitor).
struct Part {
    win: gtk::Window,
    monitor: gtk::gdk::Monitor,
    center: gtk::Stack,
    power: gtk::Box,
    backdrop: loginui::Backdrop,
    blackout: gtk::Box,
    /// None: the users' page
    page: Option<LoginPage>,
}

enum Outcome {
    Started,
    Resumed,
    Failed(greetd::Error),
}

struct Greeter {
    app: adw::Application,
    fake: bool,
    users: Vec<User>,
    sessions: Vec<Session>,
    state: RefCell<Map<String, Value>>,
    user: RefCell<Option<User>>,
    parts: RefCell<Vec<Part>>,
    /// one display holds the keyboard; every display's field shows what's typed
    keys_win: RefCell<Option<gtk::Window>>,
    throttle: throttle::Throttle,
    countdown: RefCell<Option<glib::SourceId>>,
    busy: Cell<bool>,
    leaving: Cell<bool>,
    syncing: Cell<bool>,
    off_after: i64,
    idle: RefCell<Option<glib::SourceId>>,
    /// the compositor's input idle (None: the timer above, or never)
    watch: RefCell<Option<IdleWatch>>,
    /// the shown user's video wallpaper, one player for every display
    video: RefCell<Option<Rc<LockVideo>>>,
    off: Cell<bool>,
    last_xy: Cell<Option<(i64, i64)>>,
}

fn decode(path: &Path) -> Option<gtk::gdk::Texture> {
    // shown only blurred: no full-size picture
    if let Some((_fmt, w, h)) = gtk::gdk_pixbuf::Pixbuf::file_info(path) {
        if w.max(h) > WALLPAPER_PX {
            if let Ok(pb) = gtk::gdk_pixbuf::Pixbuf::from_file_at_scale(path, WALLPAPER_PX, WALLPAPER_PX, true) {
                #[allow(deprecated)]
                return Some(gtk::gdk::Texture::for_pixbuf(&pb));
            }
        }
    }
    gtk::gdk::Texture::from_filename(path).ok()
}

/// The user picked first: the last one to log in, else the only one.
pub fn first_user(users: &[User], last: Option<&str>) -> Option<User> {
    users.iter().find(|u| Some(u.name.as_str()) == last).cloned().or_else(|| (users.len() == 1).then(|| users[0].clone()))
}

/// The name shown under a user's picture.
fn real(u: &User) -> String {
    if u.real.is_empty() { u.name.clone() } else { u.real.clone() }
}

impl Greeter {
    fn new(app: &adw::Application) -> Rc<Self> {
        let fake = fake();
        let users = logins::users();
        let state = data::load_state(Path::new(data::STATE));
        let user = first_user(&users, state.get("user").and_then(Value::as_str));
        let mut me = Self {
            app: app.clone(),
            fake,
            users,
            sessions: data::sessions(),
            state: RefCell::new(state),
            user: RefCell::new(user),
            parts: RefCell::default(),
            keys_win: RefCell::default(),
            throttle: throttle::Throttle::new(data::WAITS.into()),
            countdown: RefCell::default(),
            busy: Cell::new(false),
            leaving: Cell::new(false),
            syncing: Cell::new(false),
            off_after: 0,
            idle: RefCell::default(),
            watch: RefCell::default(),
            video: RefCell::default(),
            off: Cell::new(false),
            last_xy: Cell::new(None),
        };
        me.restore_modes();
        let names = me.names();
        me.off_after = data::login_off_after_in(Path::new(data::WALLPAPERS), &names);
        let me = Rc::new(me);
        // the password on the main display the user chose in Settings (their
        // session shares it, see monitors.share_with_login_screen), else the built-in panel
        let mons = layer::monitors();
        let chosen = data::login_main_in(Path::new(data::WALLPAPERS), &names);
        let main = layer::pick_main(&mons.iter().map(layer::connector).collect::<Vec<_>>(), &chosen);
        for (i, m) in mons.iter().enumerate() {
            me.window(m, Some(i) == main);
        }
        // a display plugged in later (or back after its cable moved) gets the login too
        if let Some(display) = gtk::gdk::Display::default() {
            let weak = Rc::downgrade(&me);
            display.monitors().connect_items_changed(move |list, pos, _removed, added| {
                let Some(me) = weak.upgrade() else { return };
                for k in 0..added {
                    if let Some(m) = list.item(pos + k).and_downcast::<gtk::gdk::Monitor>() {
                        me.window(&m, false);
                    }
                }
            });
        }
        me.sleep();
        me.input();
        me
    }

    /// Who the screen is for: the chosen user; before choosing, the last one, then everyone.
    fn names(&self) -> Vec<String> {
        if let Some(u) = self.user.borrow().as_ref() {
            return vec![u.name.clone()];
        }
        let last = self.state.borrow().get("user").and_then(Value::as_str).map(String::from);
        last.into_iter().chain(self.users.iter().map(|u| u.name.clone())).filter(|n| !n.is_empty()).collect()
    }

    /// The video wallpaper of the user whose picture shows (wallpaper()),
    /// played silent under the blur, one player for every display; None
    /// for a picture.
    fn video_cover(&self) -> Option<sonata_shell::lockvideo::CoverPaintable> {
        let root = Path::new(data::WALLPAPERS);
        let name = self.names().into_iter().find(|n| data::user_wallpaper_in(root, n).is_some());
        let path = name.and_then(|n| data::user_video_in(root, &n));
        let mut cur = self.video.borrow_mut();
        if let (Some(v), Some(p)) = (cur.as_ref(), path.as_ref()) {
            if Path::new(&v.path) == p.as_path() {
                return Some(v.cover.clone());
            }
        }
        if let Some(v) = cur.take() {
            v.close();
        }
        *cur = path.and_then(|p| LockVideo::open(&p)); // (none: the picture stays)
        cur.as_ref().map(|v| v.cover.clone())
    }

    /// The chosen user's wallpaper; before choosing, the last user's (else anyone's).
    fn wallpaper(&self) -> Option<gtk::gdk::Texture> {
        self.names().iter().find_map(|n| data::user_wallpaper_in(Path::new(data::WALLPAPERS), n).and_then(|p| decode(&p)))
    }

    // -- windows: one per display, each with the whole login (Vini: the column
    // was on one display only, and gone when that display came back) --------------------------
    fn window(self: &Rc<Self>, monitor: &gtk::gdk::Monitor, primary: bool) {
        let win = gtk::Window::builder().application(&self.app).decorated(false).css_classes(["sonata-lock"]).build();
        let over = gtk::Overlay::builder().css_classes(["gr-fade-in"]).build();
        let backdrop = loginui::Backdrop::default();
        backdrop.set_texture(self.wallpaper());
        backdrop.set_video(self.video_cover().as_ref());
        over.set_child(Some(&backdrop));
        over.add_overlay(&loginui::top_clock(None)); // like the lock screen: top, centred (Vini)
        let center = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(sonata_ui::tokens::ms(220.0)).halign(gtk::Align::Center).valign(gtk::Align::Center).build();
        loginui::lift(&center, Some(monitor));
        over.add_overlay(&center);
        let fake = self.fake;
        let power = loginui::power_bar(Rc::new(move |method: &str| {
            if !fake {
                loginui::logind(method);
            }
        }));
        over.add_overlay(&power);
        if !self.fake {
            over.add_overlay(&self.display_button());
        }
        // (always: the login screen has no settings)
        over.add_overlay(&screencorners::corners_overlay(screencorners::RADIUS));
        let blackout = gtk::Box::builder().css_classes(["lk-blackout"]).can_target(false).build(); // dark while idle
        if self.off.get() {
            blackout.add_css_class("on");
        }
        over.add_overlay(&blackout);
        win.set_child(Some(&over));
        if self.fake {
            // a try-out inside the session: Esc leaves
            let keys = gtk::EventControllerKey::new();
            let app = self.app.clone();
            keys.connect_key_pressed(move |_, key, _, _| {
                if key == gtk::gdk::Key::Escape {
                    app.quit();
                    return glib::Propagation::Stop;
                }
                glib::Propagation::Proceed
            });
            win.add_controller(keys);
        }
        self.watch_input(&win);
        if layer::overlay_fullscreen(&win, "sonata2-greeter") {
            // a layer surface per display
            layer::set_monitor(&win, monitor);
            let mut keys = self.keys_win.borrow_mut();
            if primary || keys.is_none() {
                if let Some(old) = keys.as_ref() {
                    layer::set_keyboard(old, false);
                }
                *keys = Some(win.clone()); // (overlay_fullscreen: exclusive keyboard)
            } else {
                layer::set_keyboard(&win, false);
            }
        } else {
            win.fullscreen_on_monitor(monitor);
            if primary || self.keys_win.borrow().is_none() {
                self.keys_win.replace(Some(win.clone()));
            }
        }
        self.parts.borrow_mut().push(Part { win: win.clone(), monitor: monitor.clone(), center, power, backdrop, blackout, page: None });
        let i = self.parts.borrow().len() - 1;
        self.fill(i, primary);
        win.present();
        // unplugged (or gone for a moment in sleep): its copy goes; it comes back as a new display
        let weak = Rc::downgrade(self);
        let w = win.clone();
        monitor.connect_invalidate(move |_| {
            if let Some(me) = weak.upgrade() {
                me.gone(&w);
            }
        });
    }

    fn gone(&self, win: &gtk::Window) {
        self.parts.borrow_mut().retain(|p| &p.win != win);
        if self.keys_win.borrow().as_ref() == Some(win) {
            // the keyboard moves to a display still there
            let next = self.parts.borrow().first().map(|p| p.win.clone());
            if let Some(n) = next.as_ref() {
                layer::set_keyboard(n, true);
            }
            self.keys_win.replace(next);
            self.focus_keys();
        }
        win.destroy();
    }

    /// The field (if shown) on the display with the keyboard takes the keys.
    fn focus_keys(&self) {
        let keys = self.keys_win.borrow().clone();
        let parts = self.parts.borrow();
        let part = parts.iter().find(|p| Some(&p.win) == keys.as_ref()).or(parts.first());
        if let Some(entry) = part.and_then(|p| p.page.as_ref()).map(|pg| pg.entry.clone()) {
            glib::idle_add_local_once(move || {
                entry.grab_focus();
            });
        }
    }

    /// One display's page (the login or the users), in place of the one it shows.
    fn fill(self: &Rc<Self>, i: usize, focus: bool) {
        let Some((center, monitor)) = self.parts.borrow().get(i).map(|p| (p.center.clone(), p.monitor.clone())) else { return };
        let (widget, page): (gtk::Widget, Option<LoginPage>) = if self.user.borrow().is_some() {
            let page = self.login_page(focus);
            (page.column.clone().upcast(), Some(page))
        } else {
            (self.users_page(&monitor).upcast(), None)
        };
        let old = center.visible_child();
        center.add_child(&widget);
        center.set_visible_child(&widget);
        if let Some(old) = old {
            let c = center.clone();
            glib::timeout_add_local_once(Duration::from_millis(260), move || c.remove(&old));
        }
        if let Some(p) = self.parts.borrow_mut().get_mut(i) {
            p.page = page;
        }
    }

    /// Every display's page again (a user picked, or "Other Users").
    fn show(self: &Rc<Self>) {
        if let Some(id) = self.countdown.take() {
            sonata_ui::cancel(id);
        }
        for p in self.parts.borrow_mut().iter_mut() {
            p.page = None;
        }
        let keys = self.keys_win.borrow().clone();
        let n = self.parts.borrow().len();
        for i in 0..n {
            // the field on the display that has the keyboard (the first one
            // could be a display that gets no keys: typing went nowhere)
            let focus = match keys.as_ref() {
                Some(k) => self.parts.borrow().get(i).is_some_and(|p| &p.win == k),
                None => i == 0,
            };
            self.fill(i, focus);
        }
        let tex = self.wallpaper();
        let video = self.video_cover();
        for p in self.parts.borrow().iter() {
            p.backdrop.set_texture(tex.clone());
            p.backdrop.set_video(video.as_ref());
        }
        if let Some(u) = self.user.borrow().clone() {
            self.guard_blocked(&u.name);
        }
    }

    /// The users, in rows that fit the display (Vini: many users ran off a
    /// small screen): as many per row as fit, smaller pictures from 3 rows
    /// on, scrolling past ~half the display's height.
    fn users_page(self: &Rc<Self>, monitor: &gtk::gdk::Monitor) -> gtk::ScrolledWindow {
        let g = monitor.geometry();
        let (sw, sh) = if g.width() > 0 { (g.width(), g.height()) } else { (1920, 1080) };
        let (per_row, px) = data::user_grid(self.users.len(), sw);
        let grid = gtk::FlowBox::builder()
            .selection_mode(gtk::SelectionMode::None)
            .homogeneous(true)
            .column_spacing(18)
            .row_spacing(12)
            .max_children_per_line(per_row as u32)
            .min_children_per_line(self.users.len().min(per_row).max(1) as u32)
            .halign(gtk::Align::Center)
            .valign(gtk::Align::Start)
            .build();
        for u in &self.users {
            let b = gtk::Button::builder().css_classes(["gr-user"]).build();
            let col = gtk::Box::new(gtk::Orientation::Vertical, 8);
            let name = real(u);
            col.append(&loginui::avatar(px, Some(&u.name), Some(&name), Some(u.icon.as_str()).filter(|s| !s.is_empty())));
            col.append(&gtk::Label::builder().label(name.as_str()).css_classes(["lk-name"]).max_width_chars(16).ellipsize(gtk::pango::EllipsizeMode::End).build());
            b.set_child(Some(&col));
            let weak = Rc::downgrade(self);
            let u = u.clone();
            b.connect_clicked(move |_| {
                if let Some(me) = weak.upgrade() {
                    me.pick(Some(u.clone()));
                }
            });
            grid.append(&b);
        }
        gtk::ScrolledWindow::builder()
            .child(&grid)
            .hscrollbar_policy(gtk::PolicyType::Never)
            .propagate_natural_width(true)
            .propagate_natural_height(true)
            .max_content_height((sh as f64 * 0.55) as i32)
            .css_classes(["gr-rise"])
            .build()
    }

    fn pick(self: &Rc<Self>, user: Option<User>) {
        if self.busy.get() {
            // logging in: the page must stay until it ends
            return;
        }
        self.user.replace(user);
        self.show();
    }

    fn login_page(self: &Rc<Self>, focus: bool) -> LoginPage {
        let u = self.user.borrow().clone().expect("a user");
        let column = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(10).halign(gtk::Align::Center).css_classes(["gr-rise"]).build();
        let name = real(&u);
        column.append(&loginui::avatar(108, Some(&u.name), Some(&name), Some(u.icon.as_str()).filter(|s| !s.is_empty())));
        column.append(&gtk::Label::builder().label(name.as_str()).css_classes(["lk-name"]).build());
        let entry = loginui::password_field(None);
        let weak = Rc::downgrade(self);
        entry.connect_activate(move |_| {
            if let Some(me) = weak.upgrade() {
                me.login();
            }
        });
        let weak = Rc::downgrade(self);
        entry.connect_changed(move |src| {
            // typing on any display is typing in all
            let Some(me) = weak.upgrade() else { return };
            if me.syncing.replace(true) {
                return;
            }
            let text = src.text();
            for pg in me.parts.borrow().iter().filter_map(|p| p.page.as_ref()) {
                if &pg.entry != src && pg.entry.text() != text {
                    pg.entry.set_text(&text);
                    pg.entry.set_position(-1);
                }
            }
            me.syncing.set(false);
        });
        // the field turns into a spinner while logging in (macOS)
        let spinner = Spinner::new(false);
        spinner.add_css_class("gr-spinner");
        spinner.set_halign(gtk::Align::Center);
        spinner.set_valign(gtk::Align::Center);
        let slot = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(sonata_ui::tokens::ms(220.0)).halign(gtk::Align::Center).build();
        slot.add_named(&entry, Some("field"));
        slot.add_named(&spinner, Some("progress"));
        column.append(&slot);
        let links = gtk::Box::builder().spacing(4).halign(gtk::Align::Center).build();
        if self.users.len() > 1 {
            let other = gtk::Button::builder().label(tr!("Other Users").as_str()).css_classes(["gr-link"]).build();
            let weak = Rc::downgrade(self);
            other.connect_clicked(move |_| {
                if let Some(me) = weak.upgrade() {
                    me.pick(None);
                }
            });
            links.append(&other);
        }
        let session_button = (self.sessions.len() > 1).then(|| self.session_menu());
        if let Some(b) = session_button.as_ref() {
            links.append(b);
        }
        let hint = gtk::Label::builder().css_classes(["lk-hint"]).build();
        // another display's page: the same text, state and hint
        if let Some(first) = self.parts.borrow().iter().find_map(|p| p.page.as_ref()) {
            entry.set_text(&first.entry.text());
            entry.set_sensitive(first.entry.get_sensitive());
            links.set_sensitive(first.links.get_sensitive());
            slot.set_visible_child_name(first.slot.visible_child_name().as_deref().unwrap_or("field"));
            spinner.set_spinning(first.spinner.spinning());
            hint.set_label(&first.hint.label());
        }
        loginui::balanced(&column, &[links.clone().upcast(), hint.clone().upcast()]); // picture, name and field centred as one
        if focus {
            let e = entry.clone();
            glib::idle_add_local_once(move || {
                e.grab_focus();
            });
        }
        LoginPage { column, entry, spinner, slot, links, hint, session_button }
    }

    fn session(&self) -> Option<Session> {
        let user = self.user.borrow().as_ref().map(|u| u.name.clone());
        data::chosen_session(&self.state.borrow(), &self.sessions, user.as_deref()).cloned()
    }

    fn session_menu(self: &Rc<Self>) -> gtk::Button {
        let label = self.session().map(|s| format!("{}  \u{25be}", s.name)).unwrap_or_default();
        let btn = gtk::Button::builder().label(label.as_str()).css_classes(["gr-link"]).build();
        let weak = Rc::downgrade(self);
        btn.connect_clicked(move |b| {
            let Some(me) = weak.upgrade() else { return };
            let current = me.session().map(|s| s.key);
            let items = me
                .sessions
                .iter()
                .map(|s| {
                    let weak = Rc::downgrade(&me);
                    let on = current.as_deref() == Some(s.key.as_str());
                    let pick = s.clone();
                    Item::check(&s.name, on, move |_| {
                        if let Some(me) = weak.upgrade() {
                            me.choose_session(&pick);
                        }
                    })
                })
                .collect();
            menu::popup(b, vec![items], Options::below());
        });
        btn
    }

    fn choose_session(&self, s: &Session) {
        let Some(user) = self.user.borrow().as_ref().map(|u| u.name.clone()) else { return };
        data::remember_session(&mut self.state.borrow_mut(), &user, &s.key);
        let label = format!("{}  \u{25be}", s.name);
        for b in self.parts.borrow().iter().filter_map(|p| p.page.as_ref()?.session_button.as_ref()) {
            b.set_label(&label); // every display's
        }
    }

    // -- displays: the saved resolution of each, else its highest refresh rate ------------------
    fn restore_modes(&self) {
        if self.fake {
            return;
        }
        let saved = self.state.borrow().get("modes").cloned().unwrap_or(json!({}));
        for o in data::outputs() {
            if let Some(m) = data::wanted_mode(&o, saved.get(&o.name)) {
                data::set_mode(&o.name, m);
            }
        }
    }

    fn display_button(self: &Rc<Self>) -> gtk::Button {
        let btn = gtk::Button::builder().icon_name("video-display-symbolic").css_classes(["gr-power"]).tooltip_text(tr!("Displays").as_str()).halign(gtk::Align::Start).valign(gtk::Align::End).margin_start(24).margin_bottom(24).build();
        let weak = Rc::downgrade(self);
        btn.connect_clicked(move |b| {
            if let Some(me) = weak.upgrade() {
                me.display_menu(b);
            }
        });
        btn
    }

    fn display_menu(self: &Rc<Self>, btn: &gtk::Button) {
        let mut sections = Vec::new();
        for o in data::outputs() {
            let mut items = vec![Item::new(&o.description, || {}).enabled(false)];
            let o = Rc::new(o);
            for m in data::best_modes(&o.modes).into_iter().take(14) {
                let on = o.current.is_some_and(|c| (c.0, c.1) == (m.0, m.1));
                let (weak, o) = (Rc::downgrade(self), o.clone());
                items.push(Item::check(&data::mode_label(m), on, move |_| {
                    if let Some(me) = weak.upgrade() {
                        me.try_mode(&o, m);
                    }
                }));
            }
            sections.push(items);
        }
        if !sections.is_empty() {
            menu::popup(btn, sections, Options::above());
        }
    }

    /// Apply, and keep it only if confirmed within 15 s (a mode the display
    /// can't show would otherwise leave the screen black).
    fn try_mode(self: &Rc<Self>, o: &data::Output, mode: data::Mode) {
        let old = o.current;
        if !data::set_mode(&o.name, mode) {
            return;
        }
        let timer: Rc<RefCell<Option<glib::SourceId>>> = Rc::default();
        let done = Rc::new(Cell::new(false));
        let keep = {
            let (timer, done, weak, name) = (timer.clone(), done.clone(), Rc::downgrade(self), o.name.clone());
            Rc::new(move |ok: bool| {
                if let Some(id) = timer.take() {
                    sonata_ui::cancel(id);
                } else if done.get() {
                    return;
                }
                done.set(true);
                if ok {
                    let Some(me) = weak.upgrade() else { return };
                    let mut st = me.state.borrow_mut();
                    let modes = st.entry("modes").or_insert_with(|| json!({}));
                    if !modes.is_object() {
                        *modes = json!({});
                    }
                    modes.as_object_mut().expect("an object").insert(name.clone(), data::mode_json(mode));
                    data::save_state(Path::new(data::STATE), &st);
                } else if let Some(old) = old {
                    data::set_mode(&name, old);
                }
            })
        };
        // in the login screen's own window: a separate one would open below it
        let parent = self.parts.borrow().first().map(|p| p.win.clone().upcast::<gtk::Widget>());
        let k = keep.clone();
        let dlg = sonata_ui::dialog::alert(
            &tr!("Keep these display settings?"),
            &tr!("Going back to the previous ones in 15 seconds."),
            &[("revert", tr!("Revert"), ""), ("keep", tr!("Keep"), "default")],
            move |r| k(r == "keep"),
            parent.as_ref(),
        );
        let t = timer.clone();
        let id = glib::timeout_add_seconds_local_once(15, move || {
            t.take(); // (ran: nothing to remove)
            keep(false);
            dlg.force_close();
        });
        timer.replace(Some(id));
    }

    // -- the display goes dark when nobody's there ------------------------------------------------
    /// Vini: left at the login screen, the display never went dark. After
    /// `off_after` seconds without input (the user's Settings > Battery
    /// choice) it goes dark like the session's (idlelock.darken); any key
    /// or move brings it back -- counted by the compositor, whichever
    /// display it is on.
    fn sleep(self: &Rc<Self>) {
        if let Some(w) = sleep_watch(self.off_after, self.fake, IdleWatch::connect) {
            let (dark, back) = (Rc::downgrade(self), Rc::downgrade(self));
            w.watch(
                self.off_after.clamp(1, u32::MAX as i64) as u32,
                Rc::new(move || {
                    if let Some(me) = dark.upgrade() {
                        me.go_dark();
                    }
                }),
                Rc::new(move || {
                    if let Some(me) = back.upgrade() {
                        me.wake();
                    }
                }),
            );
            self.watch.replace(Some(w));
        }
    }

    /// A key or a move here: the displays on, and (without the
    /// compositor's idle watch) this process's countdown again.
    fn input(self: &Rc<Self>) {
        self.wake();
        if let Some(id) = self.idle.take() {
            sonata_ui::cancel(id);
        }
        if self.off_after <= 0 || self.fake || self.watch.borrow().is_some() {
            return;
        }
        let weak = Rc::downgrade(self);
        let id = glib::timeout_add_seconds_local_once(self.off_after as u32, move || {
            if let Some(me) = weak.upgrade() {
                me.idle.take();
                me.go_dark();
            }
        });
        self.idle.replace(Some(id));
    }

    /// Black and a laptop panel's backlight at zero -- the panel itself
    /// stays on: powering it off and on again failed on Vini's laptop.
    /// Plugged-in monitors really go off (Vini: they only went black, the
    /// pointer on them).
    fn go_dark(&self) {
        if self.off.replace(true) {
            return;
        }
        lockdisplay::lights(false); // the keyboard's light with the displays
        lockdisplay::mark_dark(true); // (the video pauses)
        for p in self.parts.borrow().iter() {
            p.blackout.add_css_class("on");
        }
        lockdisplay::pointer(true); // black, no pointer (Vini: it only dimmed)
        self.displays(false);
    }

    /// idlelock.displays_dark with the compositor's watch; without it,
    /// only the panel's backlight.
    fn displays(&self, on: bool) {
        match self.watch.borrow().as_ref() {
            Some(w) => w.displays_dark(!on),
            None => lockdisplay::dim(!on),
        }
    }

    fn wake(&self) {
        if !self.off.replace(false) {
            return;
        }
        self.displays(true);
        for p in self.parts.borrow().iter() {
            p.blackout.remove_css_class("on");
        }
        lockdisplay::pointer(false);
        lockdisplay::mark_dark(false);
        lockdisplay::lights(true);
    }

    fn watch_input(self: &Rc<Self>, win: &gtk::Window) {
        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        let weak = Rc::downgrade(self);
        keys.connect_key_pressed(move |_, _, _, _| {
            if let Some(me) = weak.upgrade() {
                me.input();
            }
            glib::Propagation::Proceed
        });
        win.add_controller(keys);
        let motion = gtk::EventControllerMotion::new();
        let weak = Rc::downgrade(self);
        motion.connect_motion(move |_, x, y| {
            let Some(me) = weak.upgrade() else { return };
            let now = Some((x.round() as i64, y.round() as i64));
            let last = me.last_xy.replace(now);
            if last.is_some() && last != now {
                me.input(); // (a real move, not the surface appearing)
            }
        });
        win.add_controller(motion);
        let click = gtk::GestureClick::new();
        click.set_propagation_phase(gtk::PropagationPhase::Capture);
        let weak = Rc::downgrade(self);
        click.connect_pressed(move |_, _, _, _| {
            if let Some(me) = weak.upgrade() {
                me.input();
            }
        });
        win.add_controller(click);
    }

    // -- waits after wrong passwords ("Try again in 4:59") ----------------------------------------
    fn pages(&self) -> Vec<(gtk::PasswordEntry, gtk::Label)> {
        self.parts.borrow().iter().filter_map(|p| p.page.as_ref()).map(|pg| (pg.entry.clone(), pg.hint.clone())).collect()
    }

    /// True while `user` must wait (and the wait shows).
    fn guard_blocked(self: &Rc<Self>, user: &str) -> bool {
        if self.throttle.wait_left(user) > 0 {
            self.count_down(user);
            return true;
        }
        false
    }

    fn count_down(self: &Rc<Self>, user: &str) {
        for (e, _) in self.pages() {
            e.set_sensitive(false);
        }
        if self.countdown.borrow().is_some() {
            return;
        }
        let tick = {
            let (weak, user) = (Rc::downgrade(self), user.to_string());
            move || {
                let Some(me) = weak.upgrade() else { return glib::ControlFlow::Break };
                let left = me.throttle.wait_left(&user);
                let pages = me.pages();
                if left > 0 && !pages.is_empty() {
                    let text = throttle::describe(left);
                    for (_, h) in &pages {
                        h.set_label(&text);
                    }
                    return glib::ControlFlow::Continue;
                }
                for (e, h) in &pages {
                    h.set_label("");
                    e.set_sensitive(true);
                }
                me.countdown.take();
                me.focus_keys();
                glib::ControlFlow::Break
            }
        };
        let first = tick.clone();
        if first() == glib::ControlFlow::Continue {
            self.countdown.replace(Some(glib::timeout_add_seconds_local(1, tick)));
        }
    }

    // -- login --------------------------------------------------------------------------------------
    fn login(self: &Rc<Self>) {
        let Some(user) = self.user.borrow().as_ref().map(|u| u.name.clone()) else { return };
        let Some(pw) = self.pages().first().map(|(e, _)| e.text().to_string()) else { return };
        let session = self.session();
        if pw.is_empty() || self.busy.get() || self.leaving.get() {
            return;
        }
        let Some(session) = session else { return };
        if self.guard_blocked(&user) {
            return;
        }
        self.busy.set(true);
        for p in self.parts.borrow().iter().filter_map(|p| p.page.as_ref()) {
            p.entry.set_sensitive(false);
            p.links.set_sensitive(false); // no "Other Users" / session change mid-login
            p.hint.set_label("");
            p.spinner.set_spinning(true);
            p.slot.set_visible_child_name("progress");
        }
        let (fake, who) = (self.fake, user.clone());
        let me = self.clone();
        glib::spawn_future_local(async move {
            // any error must end the spinner, never leave it turning
            let outcome = gio::spawn_blocking(move || work(fake, &who, pw, &session)).await.unwrap_or_else(|_| Outcome::Failed(greetd::Error::new("error", "")));
            match outcome {
                Outcome::Started => me.started(&user),
                Outcome::Resumed => me.resumed(&user),
                Outcome::Failed(e) => me.failed(&e, &user),
            }
        });
    }

    /// Back in a running session: this login screen's job is done (a
    /// switch one stops; the boot one stays for the next login).
    fn resumed(self: &Rc<Self>, user: &str) {
        self.busy.set(false);
        self.throttle.succeeded(user);
        for p in self.parts.borrow().iter().filter_map(|p| p.page.as_ref()) {
            p.spinner.set_spinning(false);
            p.slot.set_visible_child_name("field");
            p.entry.set_text("");
            p.entry.set_sensitive(true);
            p.links.set_sensitive(true);
        }
        std::thread::spawn(userswitch::leave_switch_greeter);
    }

    fn failed(self: &Rc<Self>, err: &greetd::Error, user: &str) {
        self.busy.set(false);
        let text = if err.description.is_empty() { tr!("Couldn't log in") } else { err.description.clone() };
        for p in self.parts.borrow().iter().filter_map(|p| p.page.as_ref()) {
            p.links.set_sensitive(true);
            p.spinner.set_spinning(false);
            p.slot.set_visible_child_name("field");
            if !err.is_auth() {
                p.hint.set_label(&text);
            }
            loginui::shake(&p.entry);
        }
        self.focus_keys();
        if err.is_auth() && self.throttle.failed(user) > 0 {
            self.count_down(user);
        }
    }

    fn started(self: &Rc<Self>, user: &str) {
        self.throttle.succeeded(user);
        self.leaving.set(true);
        {
            let mut st = self.state.borrow_mut();
            st.insert("user".into(), json!(user));
            data::save_state(Path::new(data::STATE), &st);
        }
        // (the spinner keeps turning while) the picture, name and bar fade away
        // over the blurred wallpaper; the session's welcome screen picks up from the same look
        for p in self.parts.borrow().iter() {
            if let Some(pg) = p.page.as_ref() {
                pg.column.add_css_class("gr-leave");
            }
            p.power.add_css_class("gr-leave");
        }
        let me = self.clone();
        glib::timeout_add_local_once(Duration::from_millis(420), move || me.quit());
    }

    fn quit(&self) {
        self.app.quit();
        if !self.fake && std::env::var_os("SONATA_GREETER_WAYFIRE").is_some_and(|v| !v.is_empty()) {
            // greetd starts the session when the greeter command (our Wayfire) ends
            use std::os::unix::fs::MetadataExt;
            if let Ok(uid) = std::fs::metadata("/proc/self").map(|m| m.uid()) {
                let _ = std::process::Command::new("pkill").args(["-TERM", "-u", &uid.to_string(), "-x", "wayfire"]).status();
            }
        }
    }
}

/// The compositor's idle watch for the login screen's darkness, or None:
/// never (`seconds` <= 0), a preview, or no compositor to ask.
fn sleep_watch(seconds: i64, fake: bool, connect: impl FnOnce() -> IdleWatch) -> Option<IdleWatch> {
    if seconds <= 0 || fake {
        return None;
    }
    let w = connect();
    if !w.ok() {
        w.close();
        return None;
    }
    Some(w)
}

/// Zeros over the password once it was used.
fn wipe(mut pw: String) {
    // SAFETY: zeros are valid UTF-8
    for b in unsafe { pw.as_bytes_mut() } {
        unsafe { std::ptr::write_volatile(b, 0) };
    }
}

/// Off the GTK thread: the password to greetd, then the session -- or,
/// for a user already logged in here, back to theirs.
fn work(fake: bool, user: &str, pw: String, session: &Session) -> Outcome {
    let mut client: Box<dyn greetd::Greetd> = if fake {
        Box::new(greetd::Fake::default())
    } else {
        match greetd::Client::connect(None) {
            Ok(c) => Box::new(c),
            Err(e) => {
                wipe(pw);
                return Outcome::Failed(e);
            }
        }
    };
    let r = greetd::login(client.as_mut(), user, &pw);
    wipe(pw);
    let r = r.and_then(|()| {
        // already logged in: never a second session
        let listed = if fake { Vec::new() } else { userswitch::sessions() };
        if let Some(running) = userswitch::graphical_session(user, &listed, "seat0") {
            // their session, unlocked and in front: the password asked once
            // (Vini: here and again on its lock screen)
            client.cancel();
            client.close();
            if !userswitch::resume(&running.id) {
                return Err(greetd::Error::new("error", &tr!("Couldn't switch user")));
            }
            return Ok(Outcome::Resumed);
        }
        client.start_session(&session.cmd, &session_env(session))?;
        Ok(Outcome::Started)
    });
    r.unwrap_or_else(|e| {
        client.close();
        Outcome::Failed(e)
    })
}

/// The environment greetd starts the session with.
pub fn session_env(s: &Session) -> Vec<String> {
    let mut env = vec!["XDG_SESSION_TYPE=wayland".to_string(), format!("XDG_SESSION_DESKTOP={}", s.key)];
    if !s.desktops.is_empty() {
        env.push(format!("XDG_CURRENT_DESKTOP={}", s.desktops));
    }
    env
}

/// `sonata2-rs greeter`: the login screen (greetd; install.sh --greeter).
/// No arguments; SONATA_GREETER_FAKE=1 tries it out without greetd.
pub fn run(_args: &[String]) -> glib::ExitCode {
    if !fake() {
        layer::ensure_preload(); // (may start this process again)
    }
    let app = adw::Application::builder().application_id(app_id()).build();
    let state: Rc<RefCell<Option<Rc<Greeter>>>> = Rc::default();
    let hold: RefCell<Option<gio::ApplicationHoldGuard>> = RefCell::default();
    app.connect_activate(move |app| {
        if state.borrow().is_some() {
            return;
        }
        sonata_ui::setup();
        loginui::register_css();
        hold.replace(Some(app.hold()));
        state.replace(Some(Greeter::new(app)));
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn user(name: &str) -> User {
        User { name: name.into(), real: String::new(), icon: String::new() }
    }

    /// Vini: left at the login screen, the display never went dark. It
    /// does after the user's time, by the compositor's idle watch -- never
    /// when they chose "never", in a preview, or without a compositor.
    #[test]
    fn test_never_or_no_compositor() {
        let dir = tempfile::tempdir().unwrap();
        let none = || IdleWatch::connect_to(&dir.path().join("wayland-9"));
        assert!(sleep_watch(-1, false, || panic!("not asked")).is_none());
        assert!(sleep_watch(0, false, || panic!("not asked")).is_none());
        assert!(sleep_watch(300, true, || panic!("not asked")).is_none()); // a preview
        assert!(sleep_watch(300, false, none).is_none());
    }

    #[test]
    fn the_last_user_or_the_only_one_comes_first() {
        let two = [user("ana"), user("vini")];
        assert_eq!(first_user(&two, Some("vini")).unwrap().name, "vini");
        assert!(first_user(&two, None).is_none()); // several: the pictures first
        assert!(first_user(&two, Some("gone")).is_none());
        assert_eq!(first_user(&two[..1], None).unwrap().name, "ana");
        assert_eq!(real(&two[0]), "ana"); // no real name: the login name
    }

    #[test]
    fn the_session_gets_its_desktop_names() {
        let s = Session { key: "sonata".into(), name: "Sonata".into(), cmd: vec!["sonata-session".into()], desktops: "Sonata:wlroots".into() };
        assert_eq!(session_env(&s), ["XDG_SESSION_TYPE=wayland", "XDG_SESSION_DESKTOP=sonata", "XDG_CURRENT_DESKTOP=Sonata:wlroots"]);
        let bare = Session { desktops: String::new(), ..s };
        assert_eq!(session_env(&bare).len(), 2);
    }

    #[test]
    fn a_preview_logs_in_with_sonata_and_never_resumes() {
        // (tests/test_switch_user.py: wrong password unlocks nothing; not logged in logs in)
        let s = Session { key: "sonata".into(), name: "Sonata".into(), cmd: vec!["sonata-session".into()], desktops: String::new() };
        assert!(matches!(work(true, "vini", "sonata".into(), &s), Outcome::Started));
        assert!(matches!(work(true, "vini", "nope".into(), &s), Outcome::Failed(e) if e.is_auth()));
    }

    #[test]
    fn without_greetd_the_login_fails_with_a_message() {
        if std::env::var_os("GREETD_SOCK").is_some() {
            return; // never the real greetd
        }
        let s = Session { key: "sonata".into(), name: "Sonata".into(), cmd: vec![], desktops: String::new() };
        match work(false, "vini", "pw".into(), &s) {
            Outcome::Failed(e) => assert!(!e.is_auth() && !e.description.is_empty()),
            _ => panic!("logged in without greetd"),
        }
    }
}
