//! About > Software Update (app.py's _page_updates) and Sonata Update
//! (_page_sonataupdate). The packages: sonata_system::updates; Sonata's
//! own releases: sonata_system::selfupdate.

use crate::kit::{group, run_async};
use crate::window::{Ctx, Settings};
use adw::prelude::*;
use sonata_i18n::{fmt, tr, trn};
use sonata_system::selfupdate as su;
use sonata_system::updates::{self as up, Runner, Source, Update};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};

/// Updates listed per source, at most (pacman can list thousands).
const LIST_MAX: usize = 300;
/// The log keeps this many lines at most, then drops the oldest LOG_DROP.
const LOG_MAX: i32 = 4000;
const LOG_DROP: i32 = 1000;

fn now_hm() -> String {
    glib::DateTime::now_local().ok().and_then(|d| d.format("%H:%M").ok()).map(|s| s.to_string()).unwrap_or_default()
}

/// A system update with a kernel or driver in it needs a restart.
pub fn needs_restart(system: &[Update]) -> bool {
    system.iter().any(|u| (u.name.starts_with("linux") || u.name.starts_with("nvidia")) && !u.name.starts_with("linux-firmware"))
}

/// "Checked at 10:00", and the sources that couldn't be checked.
pub fn checked_text(when: &str, failed: &[String]) -> String {
    let mut text = fmt(&tr!("Checked at {time}"), &[("time", when)]);
    if !failed.is_empty() {
        text += " · ";
        text += &fmt(&tr!("{names} couldn't be checked"), &[("names", &failed.join(", "))]);
    }
    text
}

fn clear_box(b: &gtk::Box) {
    while let Some(c) = b.first_child() {
        b.remove(&c);
    }
}

// -- Software Update -----------------------------------------------------------------------------
/// Ventura Software Update: the distro's name and state on top, then what
/// can be updated per source (System, AUR, Flatpak), each with its
/// versions. Update Now updates in place, with a progress bar and the
/// output under Details; sources without an unattended mode, or a failed
/// update, go to a terminal.
pub struct UpdatesPage {
    settings: Weak<Settings>,
    me: Weak<UpdatesPage>,
    status: adw::ActionRow,
    spin: sonata_ui::progress::Spinner,
    buttons: gtk::Box,
    prog_row: gtk::ListBoxRow,
    bar: gtk::ProgressBar,
    details: adw::ExpanderRow,
    log: gtk::TextView,
    lst: adw::PreferencesGroup,
    srcs: RefCell<Vec<Source>>,
    /// per source id: its updates, None when it couldn't be checked
    found: RefCell<HashMap<String, Option<Vec<Update>>>>,
    pending: Cell<usize>,
    rows: RefCell<Vec<adw::ExpanderRow>>,
    pulse: RefCell<Option<glib::SourceId>>,
    runner: RefCell<Option<Runner>>,
    /// the app held while updating (see update())
    hold: RefCell<Option<gio::ApplicationHoldGuard>>,
}

pub fn page_updates(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let head = group(&tr!("System Update"), &tr!("Packages, AUR and Flatpak apps. Sonata updates itself above."));
    let status = adw::ActionRow::builder().title(tr!("Checking for updates…")).use_markup(false).build();
    let logo = gtk::Image::builder().pixel_size(40).valign(gtk::Align::Center).build();
    sonata_ui::icons::set_logo(&logo);
    status.add_prefix(&logo);
    let spin = sonata_ui::progress::Spinner::new(true);
    spin.set_valign(gtk::Align::Center);
    let buttons = gtk::Box::builder().spacing(8).valign(gtk::Align::Center).build();
    status.add_suffix(&spin);
    status.add_suffix(&buttons);
    head.add(&status);
    let prog_row = gtk::ListBoxRow::builder().activatable(false).selectable(false).visible(false).build();
    let bar = gtk::ProgressBar::builder().margin_start(12).margin_end(12).margin_top(10).margin_bottom(10).valign(gtk::Align::Center).build();
    prog_row.set_child(Some(&bar));
    head.add(&prog_row);
    let details = adw::ExpanderRow::builder().title(tr!("Details")).visible(false).build();
    let log = gtk::TextView::builder().editable(false).cursor_visible(false).monospace(true).css_classes(["st-log"]).wrap_mode(gtk::WrapMode::WordChar).top_margin(8).bottom_margin(8).left_margin(12).right_margin(12).build();
    let scroll = gtk::ScrolledWindow::builder().child(&log).min_content_height(180).max_content_height(180).build();
    details.add_row(&gtk::ListBoxRow::builder().activatable(false).selectable(false).child(&scroll).build());
    head.add(&details);
    let lst = group(&tr!("Updates Available"), "");
    lst.set_visible(false);
    let page = Rc::new_cyclic(|me| UpdatesPage {
        settings: Rc::downgrade(ctx),
        me: me.clone(),
        status,
        spin,
        buttons,
        prog_row,
        bar,
        details,
        log,
        lst: lst.clone(),
        srcs: RefCell::default(),
        found: RefCell::default(),
        pending: Cell::new(0),
        rows: RefCell::default(),
        pulse: RefCell::default(),
        runner: RefCell::default(),
        hold: RefCell::default(),
    });
    page.check();
    ctx.keep("updates_page", page);
    vec![head, lst]
}

impl UpdatesPage {
    fn button(&self, label: &str, cb: fn(&UpdatesPage), suggested: bool) {
        let me = self.me.clone();
        let b = sonata_ui::controls::push_button(label, move || {
            if let Some(p) = me.upgrade() {
                cb(&p);
            }
        }, if suggested { "default" } else { "" });
        self.buttons.append(&b);
    }

    fn total(&self) -> usize {
        self.found.borrow().values().map(|v| v.as_ref().map_or(0, Vec::len)).sum()
    }

    fn clear_rows(&self) {
        for r in self.rows.borrow_mut().drain(..) {
            self.lst.remove(&r);
        }
        self.lst.set_visible(false);
    }

    fn check(&self) {
        clear_box(&self.buttons);
        self.clear_rows();
        self.spin.set_visible(true);
        self.status.set_title(&tr!("Checking for updates…"));
        self.status.set_subtitle("");
        let srcs = up::sources();
        self.found.borrow_mut().clear();
        self.pending.set(srcs.len());
        *self.srcs.borrow_mut() = srcs.clone();
        if srcs.is_empty() {
            self.spin.set_visible(false);
            self.status.set_title(&tr!("Updates can't be checked here"));
            self.status.set_subtitle(&tr!("No supported package manager found (pacman, dnf, apt, zypper, Flatpak)"));
            return;
        }
        for src in srcs {
            let (me, id) = (self.me.clone(), src.id.clone());
            run_async(move || up::check(&src), move |ups| {
                if let Some(p) = me.upgrade() {
                    p.checked(&id, ups);
                }
            });
        }
    }

    fn checked(&self, id: &str, ups: Option<Vec<Update>>) {
        self.found.borrow_mut().insert(id.to_string(), ups);
        self.pending.set(self.pending.get().saturating_sub(1));
        if self.pending.get() == 0 {
            let srcs = self.srcs.borrow().clone();
            for s in &srcs {
                // in the sources' order
                let ups = self.found.borrow().get(&s.id).cloned().flatten();
                self.add_list(s, ups.as_deref());
            }
            self.summary();
        }
    }

    fn add_list(&self, src: &Source, ups: Option<&[Update]>) {
        let Some(ups) = ups.filter(|u| !u.is_empty()) else { return };
        let exp = adw::ExpanderRow::builder().title(&src.title).use_markup(false).subtitle(fmt(&trn!("{n} update", "{n} updates", ups.len()), &[("n", &ups.len().to_string())])).build();
        for u in ups.iter().take(LIST_MAX) {
            let r = adw::ActionRow::builder().title(&u.name).use_markup(false).build();
            if !u.new.is_empty() {
                let text = if u.old.is_empty() { u.new.clone() } else { format!("{} → {}", u.old, u.new) };
                r.add_suffix(&gtk::Label::builder().label(text).css_classes(["st-caption"]).ellipsize(gtk::pango::EllipsizeMode::Middle).max_width_chars(36).build());
            }
            exp.add_row(&r);
        }
        self.lst.add(&exp);
        self.rows.borrow_mut().push(exp);
        self.lst.set_visible(true);
    }

    fn failed(&self) -> Vec<String> {
        let found = self.found.borrow();
        self.srcs.borrow().iter().filter(|s| found.get(&s.id).is_none_or(Option::is_none)).map(|s| s.title.clone()).collect()
    }

    fn summary(&self) {
        self.spin.set_visible(false);
        clear_box(&self.buttons);
        let n = self.total();
        self.status.set_subtitle(&checked_text(&now_hm(), &self.failed()));
        if n > 0 {
            self.status.set_title(&fmt(&trn!("{n} update available", "{n} updates available", n), &[("n", &n.to_string())]));
            self.button(&tr!("Update Now"), UpdatesPage::update, true);
        } else {
            self.status.set_title(&tr!("Your computer is up to date"));
            self.button(&tr!("Check Again"), UpdatesPage::check, false);
        }
    }

    /// The sources with something to update.
    fn todo(&self) -> Vec<Source> {
        let found = self.found.borrow();
        self.srcs.borrow().iter().filter(|s| found.get(&s.id).and_then(Option::as_ref).is_some_and(|v| !v.is_empty())).cloned().collect()
    }

    fn in_terminal(&self) {
        let cmd: Vec<String> = self.todo().into_iter().map(|s| s.terminal).collect();
        if !sonata_backend::system::run_in_terminal(&cmd.join(" && ")) {
            if let Some(s) = self.settings.upgrade() {
                s.toast(&tr!("No terminal found"));
            }
        }
    }

    fn say(&self, text: &str) {
        let buf = self.log.buffer();
        buf.insert(&mut buf.end_iter(), &format!("{text}\n"));
        if buf.line_count() > LOG_MAX {
            // keep the log light
            if let Some(mut cut) = buf.iter_at_line(LOG_DROP) {
                buf.delete(&mut buf.start_iter(), &mut cut);
            }
        }
        self.log.scroll_to_mark(&buf.get_insert(), 0.0, false, 0.0, 0.0);
        buf.place_cursor(&buf.end_iter());
    }

    fn fraction(&self, f: Option<f64>) {
        let Some(f) = f else {
            if self.pulse.borrow().is_none() {
                let bar = self.bar.downgrade();
                let id = glib::timeout_add_local(std::time::Duration::from_millis(120), move || match bar.upgrade() {
                    Some(b) => {
                        b.pulse();
                        glib::ControlFlow::Continue
                    }
                    None => glib::ControlFlow::Break,
                });
                *self.pulse.borrow_mut() = Some(id);
            }
            return;
        };
        if let Some(id) = self.pulse.borrow_mut().take() {
            id.remove();
        }
        self.bar.set_fraction(f);
    }

    fn update(&self) {
        let srcs = self.todo();
        if srcs.iter().any(|s| s.install.is_none()) {
            // apt/dnf/zypper: their own terminal flow
            self.in_terminal();
            return;
        }
        clear_box(&self.buttons);
        let names: Vec<String> = srcs.iter().map(|s| s.title.clone()).collect();
        self.status.set_title(&tr!("Updating…"));
        self.status.set_subtitle(&fmt(&tr!("{names} · you may be asked for your password"), &[("names", &names.join(", "))]));
        self.prog_row.set_visible(true);
        self.details.set_visible(true);
        self.log.buffer().set_text("");
        let kernel = self.found.borrow().get("system").cloned().flatten().is_some_and(|u| needs_restart(&u));
        let srcs = up::plan(&srcs); // System and AUR in one run: one password (Vini)
        let steps: Vec<Vec<String>> = srcs.iter().map(|s| s.install.clone().unwrap_or_default()).collect();
        // the step running: the Runner says "$ <its command>" as each starts
        let starts: Vec<String> = steps.iter().map(|a| format!("$ {}", a.join(" "))).collect();
        let index = Rc::new(Cell::new(-1i64));
        let titles: Rc<Vec<String>> = Rc::new(srcs.iter().map(|s| s.title.clone()).collect());
        let (me, i, t) = (self.me.clone(), index.clone(), titles.clone());
        let on_line = move |text: &str| {
            let Some(p) = me.upgrade() else { return };
            p.say(text);
            let next = (i.get() + 1) as usize;
            if starts.get(next).is_some_and(|s| s == text) {
                i.set(next as i64);
            }
            if let Some(name) = usize::try_from(i.get()).ok().and_then(|n| t.get(n)) {
                p.status.set_title(&fmt(&tr!("Updating {name}…"), &[("name", name)]));
            }
        };
        let me = self.me.clone();
        let on_fraction = move |f: Option<f64>| {
            if let Some(p) = me.upgrade() {
                p.fraction(f);
            }
        };
        let me = self.me.clone();
        let on_done = move |ok: bool, failed_at: i64| {
            let Some(p) = me.upgrade() else { return };
            p.hold.borrow_mut().take(); // the app may quit again
            p.done(ok, failed_at, kernel, &titles);
        };
        // the app stays alive (the log keeps being read) if the window closes
        // mid-update: a closed pipe would stop pacman halfway
        if let Some(app) = self.settings.upgrade().and_then(|s| s.window.application()) {
            *self.hold.borrow_mut() = Some(app.hold());
        }
        let runner = Runner::new(steps, on_line, on_fraction, on_done);
        *self.runner.borrow_mut() = Some(runner.clone());
        runner.start();
    }

    fn done(&self, ok: bool, failed_at: i64, kernel: bool, titles: &[String]) {
        self.fraction(Some(0.0));
        self.prog_row.set_visible(false);
        self.runner.borrow_mut().take();
        clear_box(&self.buttons);
        if ok {
            self.clear_rows();
            self.status.set_title(&tr!("Your computer is up to date"));
            if kernel {
                self.status.set_subtitle(&tr!("Restart to finish installing the system updates"));
                self.button(&tr!("Restart…"), |p| {
                    if let Some(s) = p.settings.upgrade() {
                        s.ask_restart("system", &tr!("The system update"));
                    }
                }, true);
            } else {
                self.status.set_subtitle(&fmt(&tr!("Updated at {time}"), &[("time", &now_hm())]));
                self.button(&tr!("Check Again"), UpdatesPage::check, false);
            }
        } else {
            let name = usize::try_from(failed_at).ok().and_then(|i| titles.get(i)).cloned().unwrap_or_default();
            self.status.set_title(&fmt(&tr!("{name} couldn't be updated"), &[("name", &name)]));
            self.status.set_subtitle(&tr!("See Details, or update in a terminal"));
            self.details.set_expanded(true);
            self.button(&tr!("Open in Terminal"), UpdatesPage::in_terminal, false);
            self.button(&tr!("Try Again"), UpdatesPage::update, true);
        }
    }
}

// -- Sonata Update -------------------------------------------------------------------------------
/// GitHub is given this long to answer (selfupdate.latest_release's default).
const RELEASE_TIMEOUT_S: u64 = 10;

/// About > Sonata Update: Sonata's own releases, apart from the system's
/// packages (Vini). Checks on open; What's New opens the release, Update
/// runs its installer in a terminal (it may ask for sudo), Check Again
/// asks GitHub again.
pub struct SonataUpdate {
    settings: Weak<Settings>,
    me: Weak<SonataUpdate>,
    pub row: adw::ActionRow,
    spin: sonata_ui::progress::Spinner,
    buttons: gtk::Box,
}

pub fn page_sonataupdate(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let g = group(&tr!("Sonata Update"), "");
    let version = su::version();
    let row = adw::ActionRow::builder().title(tr!("Checking for a new Sonata…")).subtitle(fmt(&tr!("Installed: Sonata {version}"), &[("version", &version)])).use_markup(false).build();
    row.add_prefix(&gtk::Image::builder().icon_name("sonata-launchpad").pixel_size(40).valign(gtk::Align::Center).build());
    let spin = sonata_ui::progress::Spinner::new(true);
    spin.set_valign(gtk::Align::Center);
    let buttons = gtk::Box::builder().spacing(8).valign(gtk::Align::Center).build();
    row.add_suffix(&spin);
    row.add_suffix(&buttons);
    g.add(&row);
    let page = Rc::new_cyclic(|me| SonataUpdate { settings: Rc::downgrade(ctx), me: me.clone(), row, spin, buttons });
    page.check();
    ctx.keep("sonataupdate_page", page);
    vec![g]
}

impl SonataUpdate {
    fn button(&self, label: &str, cb: impl Fn(&SonataUpdate) + 'static, suggested: bool) {
        let me = self.me.clone();
        self.buttons.append(&sonata_ui::controls::push_button(label, move || {
            if let Some(p) = me.upgrade() {
                cb(&p);
            }
        }, if suggested { "default" } else { "" }));
    }

    fn check(&self) {
        clear_box(&self.buttons);
        self.spin.set_visible(true);
        self.row.set_title(&tr!("Checking for a new Sonata…"));
        let me = self.me.clone();
        run_async(|| su::latest_release(RELEASE_TIMEOUT_S), move |rel| {
            if let Some(p) = me.upgrade() {
                p.found(rel);
            }
        });
    }

    pub fn found(&self, rel: Option<su::Release>) {
        self.spin.set_visible(false);
        clear_box(&self.buttons);
        let (version, when) = (su::version(), now_hm());
        let Some(rel) = rel else {
            self.row.set_title(&tr!("Couldn't check for a new Sonata"));
            self.row.set_subtitle(&fmt(&tr!("Installed: Sonata {version} · GitHub couldn't be reached"), &[("version", &version)]));
            self.button(&tr!("Check Again"), SonataUpdate::check, false);
            return;
        };
        if su::newer(&rel.tag, &version) {
            let new = rel.tag.trim_start_matches(['v', 'V']);
            self.row.set_title(&fmt(&tr!("Sonata {version} is available"), &[("version", new)]));
            self.row.set_subtitle(&fmt(&tr!("Installed: Sonata {version} · checked at {time}"), &[("version", &version), ("time", &when)]));
            let url = rel.url.clone();
            self.button(&tr!("What's New"), move |p| {
                let parent = p.settings.upgrade().map(|s| s.window.clone());
                gtk::UriLauncher::new(&url).launch(parent.as_ref(), gio::Cancellable::NONE, |_| {});
            }, false);
            let tag = rel.tag.clone();
            self.button(&tr!("Update"), move |p| {
                if !sonata_backend::system::run_in_terminal(&su::terminal_command(&tag)) {
                    if let Some(s) = p.settings.upgrade() {
                        s.toast(&tr!("No terminal found"));
                    }
                }
            }, true);
        } else {
            self.row.set_title(&tr!("Sonata is up to date"));
            self.row.set_subtitle(&fmt(&tr!("Sonata {version} · checked at {time}"), &[("version", &version), ("time", &when)]));
            self.button(&tr!("Check Again"), SonataUpdate::check, false);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn u(name: &str) -> Update {
        Update { name: name.into(), ..Default::default() }
    }

    #[test]
    fn kernel_updates_ask_for_a_restart() {
        assert!(needs_restart(&[u("firefox"), u("linux-zen")]));
        assert!(needs_restart(&[u("nvidia-open")]));
        assert!(!needs_restart(&[u("linux-firmware"), u("mesa")]));
    }

    #[test]
    fn checked_text_names_the_failures() {
        assert_eq!(checked_text("10:00", &[]), fmt(&tr!("Checked at {time}"), &[("time", "10:00")]));
        assert!(checked_text("10:00", &["AUR".into(), "Flatpak".into()]).ends_with(&fmt(&tr!("{names} couldn't be checked"), &[("names", "AUR, Flatpak")])));
    }
}
