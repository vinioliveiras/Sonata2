//! The password sheet (shell/authsheet.py): every prompt for the login
//! password looks and works the same (Vini: like the one installing
//! updates asks). macOS's "<App> wants to make changes": a glass panel in
//! the middle of the display over a light dim, a lock, a title and a
//! message, the account (a pop-up when several may answer), the password
//! field and Cancel / OK; a wrong password shakes the field and asks again.
//!
//! It's a window of its own that takes the keyboard -- above everything
//! with the layer shell, else a small window over the app (asking from a
//! surface that takes no keyboard, the field could be clicked but not
//! typed in). PasswordSheet is the look (polkit's agent checks the password
//! its own way); ask() checks the login password with PAM.

use crate::{layer, loginui, pam};
use gtk::prelude::*;
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

pub fn register_css() {
    loginui::register_css(); // (the field's shake)
    sonata_ui::css::register_module("shell/authsheet.py");
}

/// What a sheet is for: the title, message and accounts it shows.
pub struct Options {
    pub title: String,
    pub text: String,
    pub detail: Option<String>,
    pub ok: Option<String>,
    pub names: Vec<String>,
    pub real_names: Vec<String>,
    pub index: usize,
    pub hint: String,
}

impl Options {
    pub fn new(title: &str, text: &str) -> Self {
        let me = glib::user_name().to_string_lossy().into_owned();
        Self { title: title.into(), text: text.into(), detail: None, ok: None, names: vec![me], real_names: vec![loginui::display_name()], index: 0, hint: String::new() }
    }
}

pub struct PasswordSheet {
    pub win: gtk::Window,
    pub entry: gtk::PasswordEntry,
    pub error: gtk::Label,
    pub ok: gtk::Button,
    who: gtk::Box,
    face: RefCell<adw::Avatar>,
    names: Vec<String>,
    real_names: Vec<String>,
    pub index: Cell<usize>,
    finished: Cell<bool>,
    on_submit: RefCell<Option<Box<dyn Fn(String)>>>,
    on_cancel: RefCell<Option<Box<dyn Fn()>>>,
    on_pick: RefCell<Option<Box<dyn Fn(usize)>>>,
}

impl PasswordSheet {
    /// on_submit(password) when OK / Return is pressed with a password
    /// (the field waits: wrong() or close() next); on_cancel() on Cancel /
    /// Esc; on_pick(i) when another account is chosen.
    pub fn new(app: Option<&gtk::Application>, parent: Option<&gtk::Window>, o: Options, on_submit: impl Fn(String) + 'static, on_cancel: impl Fn() + 'static, on_pick: impl Fn(usize) + 'static) -> Rc<Self> {
        register_css();
        let win = gtk::Window::builder().decorated(false).title(o.title.as_str()).css_classes(["sonata-auth"]).build();
        if let Some(app) = app {
            win.set_application(Some(app));
        }
        let panel = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(10).css_classes(["auth-panel"]).halign(gtk::Align::Center).valign(gtk::Align::Center).width_request(340).build();
        let top = gtk::Box::new(gtk::Orientation::Horizontal, 14);
        top.append(&gtk::Image::builder().icon_name("system-lock-screen-symbolic").pixel_size(40).valign(gtk::Align::Start).build());
        let texts = gtk::Box::new(gtk::Orientation::Vertical, 4);
        let label = |t: &str, class: &str| gtk::Label::builder().label(t).xalign(0.0).wrap(true).max_width_chars(40).css_classes([class]).build();
        texts.append(&label(&o.title, "auth-title"));
        for line in [Some(o.text.as_str()), o.detail.as_deref()].into_iter().flatten().filter(|l| !l.is_empty()) {
            texts.append(&label(line, "auth-text"));
        }
        top.append(&texts);
        panel.append(&top);
        let who = gtk::Box::builder().spacing(10).margin_top(6).build();
        let index = o.index.min(o.names.len().saturating_sub(1));
        let face = loginui::avatar(28, Some(&o.names[index]), Some(&o.real_names[index]), None);
        who.append(&face);
        panel.append(&who);
        let entry = loginui::password_field(Some(&tr!("Password")));
        entry.remove_css_class("lk-field"); // the login screen's look is for dark backdrops
        entry.set_halign(gtk::Align::Fill);
        panel.append(&entry);
        let error = gtk::Label::builder().label(o.hint.as_str()).xalign(0.0).css_classes(["auth-error"]).visible(!o.hint.is_empty()).wrap(true).build();
        panel.append(&error);
        let buttons = gtk::Box::builder().spacing(8).halign(gtk::Align::End).margin_top(4).build();
        let me_cell: Rc<RefCell<Option<std::rc::Weak<PasswordSheet>>>> = Rc::default();
        let call = |f: fn(&Rc<PasswordSheet>)| {
            let cell = me_cell.clone();
            move || {
                if let Some(me) = cell.borrow().as_ref().and_then(|w| w.upgrade()) {
                    f(&me);
                }
            }
        };
        let cancel = sonata_ui::controls::push_button(&tr!("Cancel"), call(|me| me.cancel()), "");
        let ok = sonata_ui::controls::push_button(&o.ok.clone().unwrap_or_else(|| tr!("OK")), call(|me| me.submit()), "default");
        buttons.append(&cancel);
        buttons.append(&ok);
        panel.append(&buttons);
        win.set_child(Some(&panel));
        let me = Rc::new(Self {
            win,
            entry,
            error,
            ok,
            who,
            face: RefCell::new(face),
            names: o.names.clone(),
            real_names: o.real_names.clone(),
            index: Cell::new(index),
            finished: Cell::new(false),
            on_submit: RefCell::new(Some(Box::new(on_submit))),
            on_cancel: RefCell::new(Some(Box::new(on_cancel))),
            on_pick: RefCell::new(Some(Box::new(on_pick))),
        });
        me_cell.replace(Some(Rc::downgrade(&me)));
        if o.names.len() > 1 {
            let weak = Rc::downgrade(&me);
            let account = sonata_ui::controls::popup_button(&o.real_names, index as u32, move |i| {
                if let Some(me) = weak.upgrade() {
                    me.pick(i as usize);
                }
            });
            account.set_hexpand(true);
            me.who.append(&account);
        } else {
            me.who.append(&gtk::Label::builder().label(o.real_names[index].as_str()).xalign(0.0).hexpand(true).build());
        }
        let weak = Rc::downgrade(&me);
        me.entry.connect_activate(move |_| {
            if let Some(me) = weak.upgrade() {
                me.submit();
            }
        });
        let keys = gtk::EventControllerKey::new();
        let weak = Rc::downgrade(&me);
        keys.connect_key_pressed(move |_, key, _, _| {
            if key == gtk::gdk::Key::Escape {
                if let Some(me) = weak.upgrade() {
                    me.cancel();
                }
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        me.win.add_controller(keys);
        if !layer::overlay_fullscreen(&me.win, "sonata2-auth") {
            // no layer shell (an app): a small window over it
            me.win.add_css_class("in-app");
            me.win.set_modal(true);
            me.win.set_transient_for(parent);
        }
        me.win.present();
        me.entry.grab_focus();
        me
    }

    fn pick(&self, i: usize) {
        self.index.set(i);
        // the picture follows the chosen account
        let face = loginui::avatar(28, Some(&self.names[i]), Some(&self.real_names[i]), None);
        let old = self.face.replace(face.clone());
        self.who.insert_child_after(&face, Some(&old));
        self.who.remove(&old);
        if let Some(f) = self.on_pick.borrow().as_ref() {
            f(i);
        }
    }

    fn submit(&self) {
        let pw = self.entry.text().to_string();
        if pw.is_empty() || self.finished.get() || !self.entry.get_sensitive() {
            return;
        }
        self.ok.set_sensitive(false);
        self.entry.set_sensitive(false);
        self.error.set_visible(false);
        if let Some(f) = self.on_submit.borrow().as_ref() {
            f(pw);
        }
    }

    pub fn say(&self, text: &str) {
        self.error.set_label(text);
        self.error.set_visible(true);
    }

    /// Ask again: the field empties and shakes, the reason under it.
    pub fn wrong(&self, text: Option<&str>) {
        if self.finished.get() {
            return;
        }
        self.ok.set_sensitive(true);
        if text.is_some() || !self.error.get_visible() {
            self.say(&text.map(String::from).unwrap_or_else(|| tr!("Wrong password")));
        }
        loginui::shake(&self.entry);
    }

    pub fn is_finished(&self) -> bool {
        self.finished.get()
    }

    pub fn close(&self) {
        if !self.finished.replace(true) {
            self.win.destroy();
            // (the callbacks hold the caller's state: let go of them with the window)
            self.on_submit.take();
            self.on_pick.take();
        }
    }

    pub fn cancel(&self) {
        if self.finished.get() {
            return;
        }
        let cb = self.on_cancel.take();
        self.close();
        if let Some(f) = cb {
            f();
        }
    }
}

/// The login password, checked with PAM before on_ok(password) runs. A
/// wrong one asks again in the same sheet.
pub fn ask(title: &str, text: &str, ok: Option<&str>, app: Option<&gtk::Application>, parent: Option<&gtk::Window>, on_ok: impl Fn(String) + 'static, on_cancel: impl Fn() + 'static) -> Rc<PasswordSheet> {
    let mut o = Options::new(title, text);
    o.ok = ok.map(String::from);
    if !pam::available() {
        o.hint = tr!("Passwords can't be checked (PAM missing)");
    }
    let cell: Rc<RefCell<Option<std::rc::Weak<PasswordSheet>>>> = Rc::default();
    let on_ok = Rc::new(on_ok);
    let c = cell.clone();
    let sheet = PasswordSheet::new(app, parent, o, move |pw| {
        let (c, on_ok) = (c.clone(), on_ok.clone());
        glib::spawn_future_local(async move {
            let user = glib::user_name().to_string_lossy().into_owned();
            let check = pw.clone();
            let right = gio::spawn_blocking(move || pam::authenticate(&user, &check)).await.unwrap_or(false);
            let Some(sheet) = c.borrow().as_ref().and_then(|w| w.upgrade()) else { return };
            if sheet.is_finished() {
                return;
            }
            if right {
                sheet.close();
                on_ok(pw);
            } else {
                sheet.wrong(Some(&tr!("Wrong password")));
            }
        });
    }, on_cancel, |_| {});
    cell.replace(Some(Rc::downgrade(&sheet)));
    sheet
}
