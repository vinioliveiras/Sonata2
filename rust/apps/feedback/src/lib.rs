//! Feedbacker (feedback/window.py; macOS Feedback Assistant): report a
//! problem with Sonata. One small window:
//! - Monitoring: Sonata's detailed logs on / off (Settings > About's
//!   switch). While off a note says to turn it on, log out and back in,
//!   and make the problem happen again: errors-only logs rarely show what
//!   went wrong.
//! - Describe the Problem: a title and what happened.
//! - Reports: .zip files in ~/Sonata Reports; "Show in Files".
//! - Save Report / Report on GitHub…: the second also opens a pre-filled
//!   GitHub issue in the browser and shows the .zip to attach.
//! After a crash it says so at the top, saves a report at once and fills
//! in the title, and offers to reopen the apps that were open then (the
//! Dock's notes, sonata_desktop::open_apps).

pub mod report;

use adw::prelude::*;
use sonata_i18n::{tr, trn};
use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};
use std::rc::Rc;

const WIDTH: i32 = 560;
const HEIGHT: i32 = 650;

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.feedback"
    } else {
        "io.github.vinioliveiras.sonata2.feedback"
    }
}

/// Files shows the item selected in its folder (FileManager1), else the folder opens.
pub fn reveal(path: &Path) {
    let folder = if path.is_dir() { path.to_path_buf() } else { path.parent().map(Path::to_path_buf).unwrap_or_default() };
    let fallback = move || {
        let _ = gio::AppInfo::launch_default_for_uri(&gio::File::for_path(&folder).uri(), gio::AppLaunchContext::NONE);
    };
    if path.is_dir() {
        fallback();
        return;
    }
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else {
        fallback();
        return;
    };
    let uris = vec![gio::File::for_path(path).uri().to_string()];
    bus.call(Some("org.freedesktop.FileManager1"), "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1", "ShowItems", Some(&(uris, "").to_variant()), None, gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE, move |r| {
        if r.is_err() {
            fallback();
        }
    });
}

/// The row's subtitle: the first four names, then how many more.
fn reopen_subtitle(shown: &[String]) -> String {
    let mut s = shown.iter().take(4).cloned().collect::<Vec<_>>().join(", ");
    if shown.len() > 4 {
        s.push(' ');
        s.push_str(&sonata_i18n::fmt(&tr!("and {n} more"), &[("n", &(shown.len() - 4).to_string())]));
    }
    s
}

/// The apps that were open before the crash, one click away (Vini). No
/// row when the Dock had noted none.
fn reopen_row(grp: &adw::PreferencesGroup) -> Option<(adw::ActionRow, gtk::Button)> {
    use sonata_desktop::open_apps;
    let ids = open_apps::before_crash();
    if ids.is_empty() {
        return None;
    }
    let row = adw::ActionRow::builder().title(tr!("Reopen your apps").as_str()).use_markup(false).subtitle(reopen_subtitle(&open_apps::names(&ids)).as_str()).build();
    row.set_subtitle_lines(0);
    let cell: Rc<RefCell<Option<gtk::Button>>> = Rc::default();
    let (r, c) = (row.clone(), cell.clone());
    let btn = sonata_ui::controls::push_button(
        &tr!("Reopen"),
        move || {
            let n = open_apps::reopen(&ids, None);
            r.set_subtitle(&sonata_i18n::fmt(&trn!("Reopened {n} app.", "Reopened {n} apps.", n), &[("n", &n.to_string())]));
            if let Some(b) = c.borrow().as_ref() {
                b.set_sensitive(false);
            }
        },
        "default",
    );
    btn.set_valign(gtk::Align::Center);
    cell.replace(Some(btn.clone()));
    row.add_suffix(&btn);
    grp.add(&row);
    Some((row, btn))
}

struct Feedback {
    win: gtk::ApplicationWindow,
    toasts: adw::ToastOverlay,
    monitor_row: adw::SwitchRow,
    off_note: adw::ActionRow,
    title_row: adw::EntryRow,
    text: gtk::TextView,
    spinner: sonata_ui::progress::Spinner,
    save_btn: gtk::Button,
    github_btn: gtk::Button,
    busy: Cell<bool>,
    crash: Option<report::Crash>,
    crash_row: Option<adw::ActionRow>,
    crash_cause: String,
    auto: Cell<bool>,
}

impl Feedback {
    fn new(app: &adw::Application) -> Rc<Self> {
        if glib::application_name().is_none() {
            glib::set_application_name("Feedbacker");
        }
        let win = gtk::ApplicationWindow::builder().application(app).title(tr!("Feedbacker").as_str()).css_classes(["sonata-feedback"]).build();
        sonata_ui::window::standard(&win);
        sonata_ui::window::remember_size(win.upcast_ref(), "feedback", WIDTH, HEIGHT); // (never bigger than the display)
        win.set_size_request(480, 520);
        let toasts = adw::ToastOverlay::new();
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        // no title bar of its own: the compositor draws the glass one (pixdecor)
        let page = adw::PreferencesPage::builder().vexpand(true).build();

        // the last session crashed (tools/sonata-session left a note): a report at once
        let crash = report::crash();
        let (mut crash_row, mut crash_cause) = (None, String::new());
        if let Some(c) = &crash {
            let grp = adw::PreferencesGroup::new();
            // what took it down, from its logs, and how often lately (Vini: crashes, with data)
            let kind = report::note_crash(c);
            let hist = report::history_text();
            crash_cause = report::describe(&kind) + &if hist.is_empty() { String::new() } else { format!(" {hist}.") };
            let row = adw::ActionRow::builder().title(tr!("Sonata quit unexpectedly").as_str()).use_markup(false).subtitle(format!("{} {crash_cause} {}", report::crash_text(c, true), tr!("Saving a report with the logs…")).as_str()).build();
            row.set_subtitle_lines(0);
            row.add_prefix(&gtk::Image::builder().icon_name("dialog-error-symbolic").css_classes(["error"]).build());
            grp.add(&row);
            reopen_row(&grp);
            page.add(&grp);
            crash_row = Some(row);
        }

        let mon = adw::PreferencesGroup::new();
        let monitor_row = adw::SwitchRow::builder().title(tr!("Monitoring").as_str()).use_markup(false).active(report::monitoring()).subtitle(tr!("Keeps detailed logs so problems can be traced").as_str()).build();
        mon.add(&monitor_row);
        let off_note = adw::ActionRow::builder().title(tr!("Monitoring is off").as_str()).use_markup(false).subtitle(tr!("Turn it on, log out and back in, then make the problem happen again before saving a report.").as_str()).build();
        off_note.add_prefix(&gtk::Image::builder().icon_name("dialog-warning-symbolic").css_classes(["warning"]).build());
        off_note.set_subtitle_lines(0);
        mon.add(&off_note);
        page.add(&mon);

        let desc = adw::PreferencesGroup::builder().title(tr!("Describe the Problem").as_str()).build();
        let title_row = adw::EntryRow::builder().title(tr!("Title").as_str()).use_markup(false).build();
        desc.add(&title_row);
        let text = gtk::TextView::builder().wrap_mode(gtk::WrapMode::WordChar).accepts_tab(false).top_margin(2).bottom_margin(2).build();
        let frame = gtk::ScrolledWindow::builder().child(&text).hscrollbar_policy(gtk::PolicyType::Never).css_classes(["fb-text", "card"]).margin_top(12).build();
        let hint = gtk::Label::builder().label(tr!("What happened, what you were doing, and how to make it happen again.").as_str()).xalign(0.0).wrap(true).css_classes(["dim-label", "caption"]).margin_top(6).build();
        let bx = gtk::Box::new(gtk::Orientation::Vertical, 0);
        bx.append(&frame);
        bx.append(&hint);
        desc.add(&bx);
        page.add(&desc);

        let whr = adw::PreferencesGroup::builder().title(tr!("Reports").as_str()).build();
        let row = adw::ActionRow::builder().title(tr!("Saved in your home folder").as_str()).subtitle(format!("~/{}", report::FOLDER).as_str()).use_markup(false).build();
        let show = gtk::Button::builder().label(tr!("Show in Files").as_str()).valign(gtk::Align::Center).css_classes(["sonata-button"]).build();
        show.connect_clicked(|_| reveal(&report::folder()));
        row.add_suffix(&show);
        whr.add(&row);
        page.add(&whr);
        col.append(&page);

        let bottom = gtk::Box::builder().spacing(8).css_classes(["fb-bottom"]).build();
        let spinner = sonata_ui::progress::Spinner::new(false);
        spinner.set_visible(false);
        bottom.append(&spinner);
        bottom.append(&gtk::Box::builder().hexpand(true).build());
        let cell: Rc<RefCell<Option<std::rc::Weak<Feedback>>>> = Rc::default();
        let submit = |github: bool| {
            let c = cell.clone();
            move || {
                if let Some(me) = c.borrow().as_ref().and_then(|w| w.upgrade()) {
                    me.submit(github);
                }
            }
        };
        let save_btn = sonata_ui::controls::push_button(&tr!("Save Report"), submit(false), "");
        let github_btn = sonata_ui::controls::push_button(&tr!("Report on GitHub…"), submit(true), "default");
        bottom.append(&save_btn);
        bottom.append(&github_btn);
        col.append(&bottom);
        toasts.set_child(Some(&col));
        win.set_child(Some(&toasts));
        let me = Rc::new(Self { win, toasts, monitor_row, off_note, title_row, text, spinner, save_btn, github_btn, busy: Cell::new(false), crash, crash_row, crash_cause, auto: Cell::new(false) });
        cell.replace(Some(Rc::downgrade(&me)));
        let weak = Rc::downgrade(&me);
        me.monitor_row.connect_active_notify(move |row| {
            let Some(me) = weak.upgrade() else { return };
            report::set_monitoring(row.is_active());
            me.update_note();
            if row.is_active() {
                me.toast(&tr!("Monitoring on: log out and back in, then make the problem happen again"), None);
            }
        });
        me.update_note();
        if let Some(c) = me.crash {
            me.title_row.set_text(&tr!("Sonata quit unexpectedly"));
            me.text.buffer().set_text(&format!("{}\n\n{}", report::crash_text(&c, true), tr!("What I was doing just before: ")));
            me.auto.set(true);
            me.create(false); // no question about monitoring: errors are logged anyway
        }
        me
    }

    fn update_note(&self) {
        self.off_note.set_visible(!self.monitor_row.is_active());
    }

    fn toast(&self, text: &str, show: Option<PathBuf>) {
        let t = adw::Toast::builder().title(glib::markup_escape_text(text).as_str()).timeout(4).build();
        if let Some(p) = show {
            t.set_button_label(Some(&tr!("Show")));
            t.connect_button_clicked(move |_| reveal(&p));
        }
        self.toasts.add_toast(t);
    }

    fn description(&self) -> (String, String) {
        let buf = self.text.buffer();
        (self.title_row.text().to_string(), buf.text(&buf.start_iter(), &buf.end_iter(), false).to_string())
    }

    fn submit(self: &Rc<Self>, github: bool) {
        if self.busy.get() {
            return;
        }
        if report::monitoring() {
            self.create(github);
            return;
        }
        let weak = Rc::downgrade(self);
        sonata_ui::dialog::alert(
            &tr!("Monitoring Is Off"),
            &tr!("Without monitoring the logs only hold errors, so this report may not show what went wrong. Turn monitoring on, log out and back in, make the problem happen again, then save the report."),
            &[("cancel", tr!("Cancel"), ""), ("anyway", tr!("Save Anyway"), ""), ("on", tr!("Turn On Monitoring"), "default")],
            move |rid| {
                let Some(me) = weak.upgrade() else { return };
                match rid {
                    "on" => me.monitor_row.set_active(true),
                    "anyway" => me.create(github),
                    _ => {}
                }
            },
            Some(self.win.upcast_ref()),
        );
    }

    fn set_busy(&self, busy: bool) {
        self.busy.set(busy);
        self.spinner.set_visible(busy);
        self.spinner.set_spinning(busy);
        self.save_btn.set_sensitive(!busy);
        self.github_btn.set_sensitive(!busy);
    }

    fn create(self: &Rc<Self>, github: bool) {
        let (title, text) = self.description();
        self.set_busy(true);
        let me = self.clone();
        glib::spawn_future_local(async move {
            let made = gio::spawn_blocking(move || {
                let info = report::system_info(); // once: it runs pacman and lspci
                let path = report::create(&title, &text, &info).map_err(|e| e.to_string())?;
                let url = github.then(|| report::issue_url(&title, &text, &path.file_name().unwrap_or_default().to_string_lossy(), &info));
                Ok::<_, String>((path, url))
            })
            .await
            .unwrap_or_else(|_| Err("?".into())); // never busy forever
            me.created(made);
        });
    }

    fn created(&self, made: Result<(PathBuf, Option<String>), String>) {
        self.set_busy(false);
        if self.auto.replace(false) {
            // the crash report, made on opening
            if let (Ok((path, _)), Some(c), Some(row)) = (&made, &self.crash, &self.crash_row) {
                report::clear_crash(); // the next login doesn't open Feedbacker again
                let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
                row.set_subtitle(&format!("{} {} {}", report::crash_text(c, true), self.crash_cause, sonata_i18n::fmt(&tr!("The logs are saved in “{name}”."), &[("name", &name)])));
                let show = gtk::Button::builder().label(tr!("Show").as_str()).valign(gtk::Align::Center).css_classes(["sonata-button"]).build();
                let p = path.clone();
                show.connect_clicked(move |_| reveal(&p));
                row.add_suffix(&show);
                return;
            }
        }
        match made {
            Err(e) => self.toast(&sonata_i18n::fmt(&tr!("Couldn't save the report: {error}"), &[("error", &e)]), None),
            Ok((path, Some(url))) => {
                let _ = gio::AppInfo::launch_default_for_uri(&url, gio::AppLaunchContext::NONE);
                reveal(&path);
                self.toast(&tr!("Attach the report shown in Files to the GitHub issue"), None);
            }
            Ok((path, None)) => {
                let name = path.file_name().unwrap_or_default().to_string_lossy().into_owned();
                self.toast(&sonata_i18n::fmt(&tr!("Saved “{name}”"), &[("name", &name)]), Some(path));
            }
        }
    }
}

/// `sonata2-rs feedback`: one Feedbacker window; opening it again brings it forward.
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = adw::Application::builder().application_id(app_id()).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let state: Rc<RefCell<Option<Rc<Feedback>>>> = Rc::default();
    app.connect_activate(move |app| {
        if let Some(f) = state.borrow().as_ref() {
            f.win.present();
            return;
        }
        sonata_ui::setup();
        if let Some(f) = force {
            sonata_ui::force_appearance(f);
        }
        sonata_ui::css::register_module("feedback/window.py");
        let f = Feedback::new(app);
        f.win.present();
        state.replace(Some(f));
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn test_reopen_row() {
        assert_eq!(reopen_subtitle(&names(&["Firefox", "Spotify"])), "Firefox, Spotify");
    }

    #[test]
    fn test_reopen_row_names_four_then_counts() {
        // (in the language the tests run in)
        let more = sonata_i18n::fmt(&tr!("and {n} more"), &[("n", "2")]);
        assert_eq!(reopen_subtitle(&names(&["A", "B", "C", "D", "E", "F"])), format!("A, B, C, D {more}"));
    }
}
