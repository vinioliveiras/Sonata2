//! Password and question prompts for mounting (ui/mountop.py): an
//! encrypted disk, a network share, "the disk is busy" -- in Sonata's alert
//! instead of GTK's own dialog.
//!
//! ```text
//! vol.mount(gio::MountMountFlags::NONE, Some(&mountop::new(window)), ...)
//! ```
//!
//! A gio::MountOperation: GIO asks through its signals and waits for
//! reply(). Each handler stops the signal's default (it would answer
//! "unhandled" at once) and answers when the alert closes.

use adw::prelude::*;
use sonata_i18n::tr;
use std::cell::Cell;
use std::rc::Rc;

/// GIO's "heading\nbody" -> (heading, body).
pub fn split_message(message: &str) -> (String, String) {
    let lines: Vec<&str> = message.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    match lines.split_first() {
        None => (tr!("Enter the password"), String::new()),
        Some((h, rest)) => (h.to_string(), rest.join(" ")),
    }
}

/// What a password prompt shows, from GIO's flags.
#[derive(Debug, Default, PartialEq)]
pub struct Form {
    pub heading: String,
    pub body: String,
    /// "Connect as a guest"
    pub anonymous: bool,
    /// the name field, filled with GIO's default
    pub user: Option<String>,
    pub domain: Option<String>,
    pub password: bool,
    /// "Remember this password in my keyring"
    pub save: bool,
}

impl Form {
    pub fn new(message: &str, default_user: &str, default_domain: &str, flags: gio::AskPasswordFlags) -> Self {
        let (heading, body) = split_message(message);
        Form {
            heading,
            body,
            anonymous: flags.contains(gio::AskPasswordFlags::ANONYMOUS_SUPPORTED),
            user: flags.contains(gio::AskPasswordFlags::NEED_USERNAME).then(|| default_user.to_string()),
            domain: flags.contains(gio::AskPasswordFlags::NEED_DOMAIN).then(|| default_domain.to_string()),
            password: flags.contains(gio::AskPasswordFlags::NEED_PASSWORD),
            save: flags.contains(gio::AskPasswordFlags::SAVING_SUPPORTED),
        }
    }

    /// An encrypted disk unlocks; a share with a name connects.
    pub fn ok_label(&self) -> String {
        if self.password && self.user.is_none() { tr!("Unlock") } else { tr!("Connect") }
    }
}

/// What was typed into a password prompt (None: that field wasn't there).
#[derive(Debug, Default, PartialEq)]
pub struct Typed {
    pub anonymous: Option<bool>,
    pub user: Option<String>,
    pub domain: Option<String>,
    pub password: Option<String>,
    pub remember: bool,
}

pub enum Ask {
    Password(Form),
    /// heading, body, the choices (GIO's order)
    Question(String, String, Vec<String>),
}

pub enum Answer {
    /// Cancel or Escape: the mount is aborted
    Cancel,
    Password(Typed),
    Choice(i32),
}

/// Shows an Ask and calls the answer once it closes (the alert; a fake in tests).
type Show = dyn Fn(&Ask, Box<dyn Fn(Answer)>);

/// Hand the answer to GIO.
pub fn answer(op: &gio::MountOperation, a: Answer) {
    match a {
        Answer::Cancel => op.reply(gio::MountOperationResult::Aborted),
        Answer::Password(t) => {
            if let Some(v) = t.anonymous {
                op.set_anonymous(v);
            }
            if let Some(v) = &t.user {
                op.set_username(Some(v));
            }
            if let Some(v) = &t.domain {
                op.set_domain(Some(v));
            }
            if let Some(v) = &t.password {
                op.set_password(Some(v));
            }
            op.set_password_save(if t.remember { gio::PasswordSave::Permanently } else { gio::PasswordSave::Never });
            op.reply(gio::MountOperationResult::Handled);
        }
        Answer::Choice(i) => {
            op.set_choice(i);
            op.reply(gio::MountOperationResult::Handled);
        }
    }
}

/// A question's buttons: one per choice, the last one Return's.
pub fn question_responses(choices: &[String]) -> Vec<(String, String, &'static str)> {
    if choices.is_empty() {
        return vec![("0".into(), tr!("OK"), "default")];
    }
    choices.iter().enumerate().map(|(i, c)| (i.to_string(), c.clone(), if i + 1 == choices.len() { "default" } else { "" })).collect()
}

/// A question's response id -> the answer: a digit picks that choice,
/// anything else (Escape's "close") aborts.
pub fn question_answer(rid: &str) -> Answer {
    match rid.parse::<i32>() {
        Ok(i) if i >= 0 && rid.bytes().all(|b| b.is_ascii_digit()) => Answer::Choice(i),
        _ => Answer::Cancel,
    }
}

/// A mount operation that asks in Sonata's alert, over `parent`.
pub fn new(parent: Option<&gtk::Window>) -> gio::MountOperation {
    let parent = parent.map(|p| p.downgrade());
    with_show(Rc::new(move |ask, done| {
        let p = parent.as_ref().and_then(|w| w.upgrade());
        show_alert(ask, done, p.as_ref().map(|w| w.upcast_ref::<gtk::Widget>()));
    }))
}

fn with_show(show: Rc<Show>) -> gio::MountOperation {
    let op = gio::MountOperation::new();
    // an alert on screen: show-processes comes again while the list changes
    let open = Rc::new(Cell::new(false));
    let (s, o) = (show.clone(), open.clone());
    op.connect_ask_password(move |op, message, user, domain, flags| {
        op.stop_signal_emission_by_name("ask-password");
        ask(op, &s, &o, Ask::Password(Form::new(message, user, domain, flags)));
    });
    for (signal, at) in [("ask-question", 2), ("show-processes", 3)] {
        let (s, o) = (show.clone(), open.clone());
        op.connect_local(signal, false, move |args| {
            let op = args[0].get::<gio::MountOperation>().ok()?;
            op.stop_signal_emission_by_name(signal);
            if signal == "show-processes" && o.get() {
                return None; // asked again while open (the list changed)
            }
            let message = args[1].get::<Option<String>>().ok().flatten().unwrap_or_default();
            let choices = args[at].get::<Vec<String>>().unwrap_or_default();
            let (h, b) = split_message(&message);
            ask(&op, &s, &o, Ask::Question(h, b, choices));
            None
        });
    }
    op
}

fn ask(op: &gio::MountOperation, show: &Rc<Show>, open: &Rc<Cell<bool>>, a: Ask) {
    open.set(true);
    let (op, open) = (op.clone(), open.clone());
    show(&a, Box::new(move |ans| {
        open.set(false);
        answer(&op, ans);
    }));
}

/// The real prompt: Sonata's alert, its fields the kit's.
fn show_alert(ask: &Ask, done: Box<dyn Fn(Answer)>, parent: Option<&gtk::Widget>) {
    match ask {
        Ask::Question(h, b, choices) => {
            let rs = question_responses(choices);
            let refs: Vec<(&str, String, &str)> = rs.iter().map(|(id, l, s)| (id.as_str(), l.clone(), *s)).collect();
            let dlg = sonata_ui::dialog::alert(h, b, &refs, move |rid| done(question_answer(rid)), parent);
            // Escape aborts: never picks a choice ("Unmount Anyway" comes first)
            dlg.set_close_response("close");
        }
        Ask::Password(f) => {
            let bx = gtk::Box::new(gtk::Orientation::Vertical, 8);
            let anon = f.anonymous.then(|| gtk::CheckButton::builder().label(tr!("Connect as a guest")).halign(gtk::Align::Start).build());
            let field = |text: &str, hint: String| {
                let e = sonata_ui::controls::text_field(text, &hint);
                e.set_activates_default(true);
                e
            };
            let user = f.user.as_deref().map(|u| field(u, tr!("Name")));
            let domain = f.domain.as_deref().map(|d| field(d, tr!("Domain")));
            let pw = f.password.then(|| {
                let e = sonata_ui::controls::secret_field("", &tr!("Password"));
                e.set_activates_default(true);
                e
            });
            let remember = f.save.then(|| gtk::CheckButton::builder().label(tr!("Remember this password in my keyring")).halign(gtk::Align::Start).build());
            if let Some(w) = &anon {
                bx.append(w);
            }
            for w in [&user, &domain].into_iter().flatten() {
                bx.append(w);
            }
            if let Some(w) = &pw {
                bx.append(w);
            }
            if let Some(w) = &remember {
                bx.append(w);
            }
            let first: Option<gtk::Widget> = user.as_ref().map(|w| w.clone().upcast()).or_else(|| pw.as_ref().map(|w| w.clone().upcast()));
            let dlg = sonata_ui::dialog::alert(&f.heading, &f.body, &[("cancel", tr!("Cancel"), ""), ("ok", f.ok_label(), "default")], move |rid| {
                if rid != "ok" {
                    return done(Answer::Cancel);
                }
                done(Answer::Password(Typed {
                    anonymous: anon.as_ref().map(|c| c.is_active()),
                    user: user.as_ref().map(|e| e.text().to_string()),
                    domain: domain.as_ref().map(|e| e.text().to_string()),
                    password: pw.as_ref().map(|e| e.text().to_string()),
                    remember: remember.as_ref().is_some_and(|c| c.is_active()),
                }));
            }, parent);
            dlg.set_extra_child(Some(&bx));
            if let Some(w) = first {
                glib::idle_add_local_once(move || {
                    w.grab_focus();
                });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[test]
    fn split() {
        assert_eq!(split_message("Enter a passphrase\nNeeded for USB\n"), ("Enter a passphrase".into(), "Needed for USB".into()));
        assert_eq!(split_message("  \n"), (tr!("Enter the password"), String::new()));
    }

    #[test]
    fn form_from_flags() {
        use gio::AskPasswordFlags as F;
        let f = Form::new("Unlock disk\nEncrypted", "", "", F::NEED_PASSWORD | F::SAVING_SUPPORTED);
        assert_eq!((f.heading.as_str(), f.body.as_str(), f.password, f.save, f.user.is_none()), ("Unlock disk", "Encrypted", true, true, true));
        assert_eq!(f.ok_label(), tr!("Unlock"));
        let f = Form::new("Share", "me", "WORK", F::NEED_USERNAME | F::NEED_DOMAIN | F::NEED_PASSWORD | F::ANONYMOUS_SUPPORTED);
        assert_eq!((f.user.as_deref(), f.domain.as_deref(), f.anonymous), (Some("me"), Some("WORK"), true));
        assert_eq!(f.ok_label(), tr!("Connect"));
    }

    fn replies(op: &gio::MountOperation) -> Rc<RefCell<Vec<gio::MountOperationResult>>> {
        let r = Rc::new(RefCell::new(Vec::new()));
        let rr = r.clone();
        op.connect_reply(move |_, res| rr.borrow_mut().push(res));
        r
    }

    /// A fake alert: keeps what was asked and the way to answer it.
    #[allow(clippy::type_complexity)]
    fn fake() -> (gio::MountOperation, Rc<RefCell<Vec<(Ask, Box<dyn Fn(Answer)>)>>>) {
        let asked: Rc<RefCell<Vec<(Ask, Box<dyn Fn(Answer)>)>>> = Rc::default();
        let a = asked.clone();
        let op = with_show(Rc::new(move |ask, done| {
            let copy = match ask {
                Ask::Password(f) => Ask::Password(Form { heading: f.heading.clone(), body: f.body.clone(), anonymous: f.anonymous, user: f.user.clone(), domain: f.domain.clone(), password: f.password, save: f.save }),
                Ask::Question(h, b, c) => Ask::Question(h.clone(), b.clone(), c.clone()),
            };
            a.borrow_mut().push((copy, done));
        }));
        (op, asked)
    }

    /// Regression: unlocking an encrypted disk showed GTK's own dialog --
    /// and GIO's default answer ("unhandled") must not slip out first.
    #[test]
    fn password_handled() {
        let (op, asked) = fake();
        let r = replies(&op);
        op.emit_by_name::<()>("ask-password", &[&"Unlock disk\nEncrypted", &"", &"", &(gio::AskPasswordFlags::NEED_PASSWORD | gio::AskPasswordFlags::SAVING_SUPPORTED)]);
        while glib::MainContext::default().iteration(false) {}
        assert!(r.borrow().is_empty()); // GIO's default "unhandled" stopped
        let (ask, done) = &asked.borrow()[0];
        assert!(matches!(ask, Ask::Password(f) if f.heading == "Unlock disk"));
        done(Answer::Password(Typed { password: Some("secret".into()), remember: true, ..Default::default() }));
        assert_eq!(*r.borrow(), [gio::MountOperationResult::Handled]);
        assert_eq!(op.password().as_deref(), Some("secret"));
        assert_eq!(op.password_save(), gio::PasswordSave::Permanently);
    }

    #[test]
    fn cancel_aborts() {
        let (op, asked) = fake();
        let r = replies(&op);
        op.emit_by_name::<()>("ask-password", &[&"x", &"", &"", &gio::AskPasswordFlags::NEED_PASSWORD]);
        (asked.borrow()[0].1)(Answer::Cancel);
        assert_eq!(*r.borrow(), [gio::MountOperationResult::Aborted]);
    }

    /// Mount questions: a digit picks that choice; closing aborts.
    #[test]
    fn mount_question_answers() {
        let (op, asked) = fake();
        let r = replies(&op);
        let choices = glib::StrV::from(vec!["Unmount Anyway", "Cancel"]);
        op.emit_by_name::<()>("ask-question", &[&"Disk busy\nApps use it", &choices]);
        while glib::MainContext::default().iteration(false) {}
        assert!(r.borrow().is_empty());
        {
            let a = asked.borrow();
            let Ask::Question(h, b, c) = &a[0].0 else { panic!("not a question") };
            assert_eq!((h.as_str(), b.as_str(), c.len()), ("Disk busy", "Apps use it", 2));
            assert_eq!(question_responses(c)[1].2, "default"); // the last choice: Return
            (a[0].1)(question_answer("1"));
        }
        assert_eq!((r.borrow().clone(), op.choice()), (vec![gio::MountOperationResult::Handled], 1));
        (asked.borrow()[0].1)(question_answer("close"));
        assert_eq!(r.borrow().last(), Some(&gio::MountOperationResult::Aborted));
    }

    /// "The disk is busy" asked again while its alert is open: one alert.
    #[test]
    fn show_processes_once_while_open() {
        let (op, asked) = fake();
        let choices = glib::StrV::from(vec!["Unmount Anyway", "Cancel"]);
        // (a GArray of GPid, left empty: the prompt doesn't list them)
        let procs = glib::Value::from_type(unsafe { glib::translate::from_glib(glib::ffi::g_array_get_type()) });
        for _ in 0..2 {
            op.emit_by_name_with_values("show-processes", &["Disk busy".to_value(), procs.clone(), choices.to_value()]);
        }
        assert_eq!(asked.borrow().len(), 1);
        (asked.borrow()[0].1)(Answer::Choice(0));
        op.emit_by_name_with_values("show-processes", &["Disk busy".to_value(), procs, choices.to_value()]);
        assert_eq!(asked.borrow().len(), 2);
    }

    #[test]
    fn no_gtk_dialogs_left() {
        for (name, src) in [("sidebar.rs", include_str!("sidebar.rs")), ("server.rs", include_str!("server.rs")), ("automount.rs", include_str!("automount.rs")), ("lib.rs", include_str!("lib.rs")), ("window.rs", include_str!("window.rs"))] {
            assert!(!src.contains(concat!("gtk::Mount", "Operation")), "{name}");
        }
    }
}
