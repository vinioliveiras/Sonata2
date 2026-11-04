//! Task Manager (activity/window.py): Windows 11 Task Manager's structure
//! with the macOS look (Sonata tokens, glass toolbar, translucent sidebar).
//!
//! The sidebar holds the pages: Processes, Performance, App history,
//! Startup apps, Users, Details and Services. The glass toolbar shows the
//! page's title, its commands and a search field that filters the page.
//! Right-click a process: End task, Open file location, Search online,
//! Properties. Double-click (or ⌘I) shows Properties; ⌘F searches; Delete
//! ends the selected task; ⌘1..7 switch pages; ⌘W closes.
//!
//! Data comes only from /proc and /sys (procfs), sampled every 2 s off the
//! main loop and only while the window is on screen. Rows are updated in
//! place: selections and scroll positions survive refreshes.

use crate::manage::{self, History};
use crate::pages::{DetailsPage, HistoryPage, Page, ProcessesPage, ServicesPage, StartupPage, UsersPage};
use crate::performance::PerformancePage;
use crate::rows::TaskRow;
use crate::widgets::{fmt_cpu, fmt_mem};
use gtk::prelude::*;
use serde_json::{json, Map, Value};
use sonata_backend::procfs::{fmt_count, fmt_cpu_time, CpuInfo, Proc, Sampler, Snapshot};
use sonata_i18n::{fmt, tr};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, HashMap};
use std::rc::{Rc, Weak};
use std::sync::{Arc, Mutex};

/// seconds between samples
const INTERVAL: u32 = 2;
/// samples (~1 minute)
const HISTORY_SAVE_EVERY: u64 = 30;
const GENERIC_ICON: &str = "application-x-executable";
const SEARCH_URL: &str = "https://duckduckgo.com/?q=";
/// launchers and interpreters: their name says nothing about the app.
/// Windows-game launchers too: one of them in a game's entry made every
/// wine process look like that game.
const NOT_APPS: [&str; 34] = ["python", "python3", "sh", "bash", "zsh", "fish", "env", "sudo", "flatpak", "bwrap", "java", "node", "perl", "ruby", "systemd", "dbus-daemon", "gjs", "electron", "xdg-open", "sonata2", "wine", "wine64", "wine-preloader", "wine64-preloader", "wineserver", "umu-run", "proton", "pressure-vessel", "pressure-vessel-wrap", "reaper", "gamemoderun", "mangohud", "gamescope", "sonata2-rs"];

pub type AppIcon = (Option<gio::Icon>, String);

// -- app icons ----------------------------------------------------------------------------------
/// Installed apps by the names their processes go by: the executable,
/// StartupWMClass and desktop id -> (icon, app name).
pub fn icon_index() -> HashMap<String, AppIcon> {
    let mut idx: HashMap<String, AppIcon> = HashMap::new();
    let mut weak: HashMap<String, AppIcon> = HashMap::new();
    for info in gio::AppInfo::all() {
        let Some(icon) = info.icon() else { continue };
        let name = info.display_name().to_string();
        let mut strong = Vec::new();
        let did = info.id().map(|i| i.trim_end_matches(".desktop").to_string()).unwrap_or_default();
        if !did.is_empty() {
            strong.push(did.clone());
            strong.push(did.rsplit('.').next().unwrap_or(&did).to_string());
        }
        if let Some(wm) = info.downcast_ref::<gio::DesktopAppInfo>().and_then(|d| d.startup_wm_class()) {
            strong.push(wm.to_string());
        }
        let exe = info.executable();
        // a shortcut that only asks another program to start something (a
        // Steam game: "steam steam://rungameid/...") doesn't own that
        // program's processes: Steam itself showed as a game
        let cmd = info.commandline().map(|c| c.display().to_string()).unwrap_or_default();
        let loose = if !exe.as_os_str().is_empty() && !cmd.contains("://") { exe.file_name().map(|n| n.to_string_lossy().into_owned()) } else { None };
        for k in strong {
            let k = k.to_lowercase();
            if !k.is_empty() && !NOT_APPS.contains(&k.as_str()) {
                idx.entry(k).or_insert_with(|| (Some(icon.clone()), name.clone()));
            }
        }
        if let Some(k) = loose.map(|k| k.to_lowercase()).filter(|k| !k.is_empty() && !NOT_APPS.contains(&k.as_str())) {
            weak.entry(k).or_insert_with(|| (Some(icon.clone()), name.clone()));
        }
    }
    for (k, v) in weak {
        idx.entry(k).or_insert(v); // an executable's name: only where no app owns that name
    }
    idx
}

/// (app key or None, icon, display name) of a process: its app's when one
/// matches, else no icon and the process name. Sonata's own apps run as
/// `sonata2 <component>` and show as themselves.
pub fn app_for(p: &Proc, idx: &HashMap<String, AppIcon>) -> (Option<String>, Option<gio::Icon>, String) {
    if p.cmdline.contains("sonata2") {
        let parts: Vec<&str> = p.cmdline.split_whitespace().collect();
        for i in 0..parts.len().saturating_sub(1) {
            let a = parts[i];
            if a == "sonata2" || a.ends_with("/sonata2") || a.ends_with("/sonata2-rs") || a == "sonata2-rs" {
                for prefix in ["io.github.vinioliveiras.sonata2.", "io.github.vinioliveiras.sonata2rs."] {
                    let key = format!("{prefix}{}", parts[i + 1].to_lowercase());
                    if let Some((icon, name)) = idx.get(&key) {
                        return (Some(key), icon.clone(), name.clone());
                    }
                }
            }
        }
    }
    if !p.steam.is_empty() {
        // a Steam game's process: the game, by its app id
        let key = format!("steam_app_{}", p.steam);
        if let Some((name, icon)) = crate::steam::shown(&key, &p.name) {
            return (Some(key), Some(icon), name);
        }
    }
    for key in [p.exe.to_lowercase(), p.comm.to_lowercase()] {
        if key.is_empty() || NOT_APPS.contains(&key.as_str()) {
            continue;
        }
        if let Some((icon, name)) = idx.get(&key) {
            // the app's own process takes its name; helpers keep theirs
            let main = p.comm.to_lowercase() == key || p.exe.to_lowercase().starts_with(&p.comm.to_lowercase());
            return (Some(key), icon.clone(), if main { name.clone() } else { p.name.clone() });
        }
    }
    (None, None, p.name.clone())
}

fn defaults() -> Value {
    json!({"page": "processes", "sidebar_width": 190})
}

pub struct TaskManagerWindow {
    pub window: gtk::ApplicationWindow,
    settings: RefCell<Map<String, Value>>,
    sampler: Arc<Mutex<Sampler>>,
    cpu_info: RefCell<Option<CpuInfo>>,
    pub history: RefCell<History>,
    /// filled on the first idle moment
    pub icons: RefCell<Option<HashMap<String, AppIcon>>>,
    /// (pid, start) -> row
    pub rows: RefCell<HashMap<(i64, i64), TaskRow>>,
    pub pid_rows: RefCell<HashMap<i64, TaskRow>>,
    pub all_store: gio::ListStore,
    pub groups: RefCell<manage::Groups>,
    /// app key -> (icon, name)
    pub app_info: RefCell<HashMap<String, AppIcon>>,
    pub last: RefCell<Option<Snapshot>>,
    timer: RefCell<Option<glib::SourceId>>,
    busy: Cell<bool>,
    alive: Cell<bool>,
    pub restoring: Cell<bool>,
    samples: Cell<u64>,
    info: RefCell<Option<(TaskRow, HashMap<&'static str, gtk::Label>)>>,
    pub pages: RefCell<Vec<Rc<dyn Page>>>,
    pub startup: RefCell<Option<Rc<StartupPage>>>,
    pub services: RefCell<Option<Rc<ServicesPage>>>,
    pub page_id: RefCell<Option<String>>,
    stack: gtk::Stack,
    page_buttons: gtk::Stack,
    pub title_lbl: gtk::Label,
    pub search: gtk::SearchEntry,
    side: gtk::ListBox,
    me: Weak<TaskManagerWindow>,
}

thread_local! {
    static WINDOWS: RefCell<Vec<Rc<TaskManagerWindow>>> = const { RefCell::new(Vec::new()) };
}

impl TaskManagerWindow {
    pub fn new(app: &gtk::Application, sampler: Option<Sampler>) -> Rc<Self> {
        let settings = sonata_config::load("activity", &defaults());
        let history = History::from_value(&sonata_config::load("activity-history", &json!({"since": 0, "apps": {}})));
        let win = Rc::new_cyclic(|me: &Weak<TaskManagerWindow>| {
            let window = gtk::ApplicationWindow::builder().application(app).title(tr!("Task Manager")).build();
            for c in ["sonata-activity", "sonata-glass"] {
                window.add_css_class(c);
            }
            sonata_ui::window::standard(&window);
            sonata_ui::window::remember_size(window.upcast_ref(), "activity", 1040, 680);
            window.set_size_request(720, 460);
            // -- toolbar
            let (toolbar, bar, _) = sonata_ui::window::glass_toolbar(&window, Vec::new());
            bar.add_css_class("tm-toolbar");
            let title_lbl = gtk::Label::builder().css_classes(["tm-title"]).xalign(0.0).build();
            bar.set_start_widget(Some(&title_lbl));
            let end = gtk::Box::builder().spacing(8).valign(gtk::Align::Center).build();
            let page_buttons = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).hhomogeneous(false).build();
            end.append(&page_buttons);
            let search = gtk::SearchEntry::builder().placeholder_text(tr!("Search")).width_chars(16).valign(gtk::Align::Center).build();
            let w = me.clone();
            search.connect_search_changed(move |e| {
                if let Some(t) = w.upgrade() {
                    if let Some(p) = t.current() {
                        p.search(&e.text());
                    }
                }
            });
            search.connect_stop_search(|e| e.set_text(""));
            end.append(&search);
            bar.set_end_widget(Some(&end));
            // -- sidebar and pages
            let sidebar = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["sonata-sidebar", "tm-sidebar"]).build();
            sidebar.set_size_request(170, -1);
            let side = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Browse).build();
            sidebar.append(&side);
            let stack = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).hexpand(true).vexpand(true).css_classes(["tm-content"]).build();
            let paned = gtk::Paned::builder().start_child(&sidebar).end_child(&stack).shrink_start_child(false).resize_start_child(false).shrink_end_child(false).vexpand(true).build();
            paned.set_position(settings.get("sidebar_width").and_then(Value::as_i64).unwrap_or(190) as i32);
            let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
            col.append(&toolbar);
            col.append(&paned);
            window.set_child(Some(&col));
            TaskManagerWindow {
                window,
                settings: RefCell::new(settings),
                sampler: Arc::new(Mutex::new(sampler.unwrap_or_else(|| Sampler::new("/proc", "/sys")))),
                cpu_info: RefCell::default(),
                history: RefCell::new(history),
                icons: RefCell::default(),
                rows: RefCell::default(),
                pid_rows: RefCell::default(),
                all_store: gio::ListStore::new::<TaskRow>(),
                groups: RefCell::default(),
                app_info: RefCell::default(),
                last: RefCell::default(),
                timer: RefCell::default(),
                busy: Cell::new(false),
                alive: Cell::new(true),
                restoring: Cell::new(false),
                samples: Cell::new(0),
                info: RefCell::default(),
                pages: RefCell::default(),
                startup: RefCell::default(),
                services: RefCell::default(),
                page_id: RefCell::default(),
                stack,
                page_buttons,
                title_lbl,
                search,
                side,
                me: me.clone(),
            }
        });
        win.build_pages();
        win.wire();
        let page = win.settings.borrow().get("page").and_then(Value::as_str).unwrap_or("processes").to_string();
        let known = win.pages.borrow().iter().any(|p| p.id() == page);
        win.show_page(if known { &page } else { "processes" });
        // the icon index reads every .desktop file once: when the main loop is idle
        let w = win.me.clone();
        glib::idle_add_local_once(move || {
            if let Some(t) = w.upgrade() {
                t.icons.replace(Some(icon_index()));
                if t.alive.get() {
                    t.schedule();
                }
            }
        });
        win
    }

    fn build_pages(&self) {
        let w = self.me.clone();
        let startup = StartupPage::new(w.clone());
        let services = ServicesPage::new(w.clone());
        let pages: Vec<Rc<dyn Page>> = vec![ProcessesPage::new(w.clone()), PerformancePage::new(w.clone()), HistoryPage::new(w.clone()), startup.clone(), UsersPage::new(), DetailsPage::new(w, &self.all_store), services.clone()];
        self.startup.replace(Some(startup));
        self.services.replace(Some(services));
        for p in &pages {
            p.attach(false);
            self.stack.add_named(&p.widget(), Some(p.id()));
            self.page_buttons.add_named(&p.buttons(), Some(p.id()));
            let row = gtk::ListBoxRow::new();
            let h = gtk::Box::new(gtk::Orientation::Horizontal, 8);
            h.append(&gtk::Image::from_icon_name(p.icon()));
            h.append(&gtk::Label::builder().label(sonata_i18n::gettext(p.title())).xalign(0.0).ellipsize(gtk::pango::EllipsizeMode::End).build());
            row.set_child(Some(&h));
            self.side.append(&row);
        }
        self.pages.replace(pages);
    }

    fn wire(&self) {
        let w = self.me.clone();
        self.side.connect_row_selected(move |_, r| {
            if let (Some(t), Some(r)) = (w.upgrade(), r) {
                let id = t.pages.borrow().get(r.index() as usize).map(|p| p.id());
                if let Some(id) = id {
                    t.show_page(id);
                }
            }
        });
        let keys = gtk::EventControllerKey::new();
        let w = self.me.clone();
        keys.connect_key_pressed(move |_, key, _, state| match w.upgrade() {
            Some(t) if t.key(key, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        self.window.add_controller(keys);
        let w = self.me.clone();
        self.window.connect_map(move |_| {
            if let Some(t) = w.upgrade() {
                t.mapped();
            }
        });
        let w = self.me.clone();
        self.window.connect_unmap(move |_| {
            if let Some(t) = w.upgrade() {
                t.schedule();
            }
        });
        let w = self.me.clone();
        self.window.connect_realize(move |win| {
            if let Some(s) = win.surface() {
                let w = w.clone();
                s.connect_notify_local(Some("state"), move |_, _| {
                    if let Some(t) = w.upgrade() {
                        t.schedule();
                    }
                });
            }
        });
        let w = self.me.clone();
        self.window.connect_destroy(move |_| {
            if let Some(t) = w.upgrade() {
                t.destroyed();
            }
        });
    }

    fn current(&self) -> Option<Rc<dyn Page>> {
        let id = self.page_id.borrow().clone()?;
        self.pages.borrow().iter().find(|p| p.id() == id).cloned()
    }

    pub fn cpu_info(&self) -> CpuInfo {
        if self.cpu_info.borrow().is_none() {
            let info = self.sampler.lock().unwrap().cpu_info();
            self.cpu_info.replace(Some(info));
        }
        self.cpu_info.borrow().clone().unwrap()
    }

    pub fn show_page(&self, page_id: &str) {
        if self.page_id.borrow().as_deref() == Some(page_id) {
            return;
        }
        if let Some(old) = self.current() {
            old.attach(false);
        }
        let (idx, page) = {
            let pages = self.pages.borrow();
            let Some(i) = pages.iter().position(|p| p.id() == page_id) else { return };
            (i, pages[i].clone())
        };
        self.page_id.replace(Some(page_id.into()));
        let row = self.side.row_at_index(idx as i32);
        if self.side.selected_row() != row {
            self.side.select_row(row.as_ref());
        }
        self.stack.set_visible_child_name(page_id);
        self.page_buttons.set_visible_child_name(page_id);
        self.title_lbl.set_label(&sonata_i18n::gettext(page.title()));
        self.search.set_visible(page_id != "performance");
        if !self.search.text().is_empty() {
            self.search.set_text("");
        }
        page.search("");
        page.attach(true);
        page.shown();
        let last = self.last.borrow().clone();
        if let Some(snap) = last.filter(|_| !["performance", "startup", "services"].contains(&page_id)) {
            page.update(&snap); // catch up on what changed while it was hidden
        }
        if self.settings.borrow().get("page").and_then(Value::as_str) != Some(page_id) {
            self.settings.borrow_mut().insert("page".into(), json!(page_id));
            let _ = sonata_config::update("activity", &json!({"page": page_id}));
        }
    }

    // -- sampling ---------------------------------------------------------------------------------
    fn mapped(&self) {
        if let Some(view) = self.current().and_then(|p| p.view()) {
            let focus = gtk::prelude::GtkWindowExt::focus(&self.window);
            if focus.is_none() || focus.as_ref().is_some_and(|f| f.is_ancestor(&self.search) || *f == *self.search.upcast_ref::<gtk::Widget>()) {
                view.grab_focus(); // the list, not the search field, has the keyboard
            }
        }
        self.schedule();
    }

    fn on_screen(&self) -> bool {
        if !self.window.is_mapped() {
            return false;
        }
        match self.window.surface().and_downcast::<gtk::gdk::Toplevel>() {
            // Wayland: minimized/hidden windows are suspended
            Some(t) => !t.state().intersects(gtk::gdk::ToplevelState::MINIMIZED | gtk::gdk::ToplevelState::SUSPENDED),
            None => true,
        }
    }

    /// Sample only while the window is on screen (pause otherwise).
    fn schedule(&self) {
        let run = self.alive.get() && self.icons.borrow().is_some() && self.on_screen();
        if run && self.timer.borrow().is_none() {
            let w = self.me.clone();
            let id = glib::timeout_add_seconds_local(INTERVAL, move || match w.upgrade() {
                Some(t) => {
                    t.tick();
                    glib::ControlFlow::Continue
                }
                None => glib::ControlFlow::Break,
            });
            self.timer.replace(Some(id));
            self.tick();
            if self.last.borrow().as_ref().is_none_or(|s| s.interval <= 0.0) {
                // rates need a second sample soon
                let w = self.me.clone();
                glib::timeout_add_local_once(std::time::Duration::from_millis(700), move || {
                    if let Some(t) = w.upgrade() {
                        if t.timer.borrow().is_some() {
                            t.tick();
                        }
                    }
                });
            }
        } else if !run {
            if let Some(id) = self.timer.take() {
                id.remove();
            }
        }
    }

    pub fn is_sampling(&self) -> bool {
        self.timer.borrow().is_some()
    }

    fn tick(&self) {
        if self.busy.get() || !self.alive.get() {
            return;
        }
        self.busy.set(true);
        let s = self.sampler.clone();
        let w = self.me.clone();
        glib::spawn_future_local(async move {
            let snap = gio::spawn_blocking(move || s.lock().unwrap().sample(&["io"])).await.ok();
            let Some(t) = w.upgrade() else { return };
            t.busy.set(false);
            if let Some(snap) = snap.filter(|_| t.alive.get()) {
                t.apply_snapshot(snap);
            }
        });
    }

    /// One sample now, on the main loop (tests drive the samples).
    pub fn sample_now(&self) -> Snapshot {
        self.sampler.lock().unwrap().sample(&["io"])
    }

    /// Update the shared rows in place, regroup, feed the pages.
    pub fn apply_snapshot(&self, snap: Snapshot) {
        crate::widgets::MEM_TOTAL.with(|t| t.set(snap.memory.total.max(1) as f64)); // before any cell binds
        let idx = self.icons.borrow().clone().unwrap_or_default();
        self.restoring.set(true); // rows leaving/moving don't lose selections
        let mut live: HashMap<(i64, i64), TaskRow> = HashMap::new();
        let mut new = Vec::new();
        let mut app_keys: HashMap<i64, Option<String>> = HashMap::new();
        {
            let rows = self.rows.borrow();
            for p in snap.procs.values() {
                let key = p.key();
                let row = match rows.get(&key) {
                    Some(r) => {
                        r.update(p);
                        r.clone()
                    }
                    None => {
                        let (akey, icon, name) = app_for(p, &idx);
                        if let Some(k) = &akey {
                            let entry = idx.get(k).cloned().unwrap_or((icon.clone(), name.clone()));
                            self.app_info.borrow_mut().entry(k.clone()).or_insert(entry);
                        }
                        let r = TaskRow::process(p, icon, &name, akey);
                        new.push(r.clone());
                        r
                    }
                };
                app_keys.insert(p.pid, row.data().app_key.clone());
                live.insert(key, row);
            }
        }
        let gone: std::collections::HashSet<TaskRow> = self.rows.borrow().iter().filter(|(k, _)| !live.contains_key(k)).map(|(_, r)| r.clone()).collect();
        if !gone.is_empty() {
            for i in (0..self.all_store.n_items()).rev() {
                if self.all_store.item(i).and_downcast::<TaskRow>().is_some_and(|r| gone.contains(&r)) {
                    self.all_store.remove(i);
                }
            }
        }
        self.pid_rows.replace(live.values().map(|r| (r.pid(), r.clone())).collect());
        self.rows.replace(live);
        if !new.is_empty() {
            self.all_store.splice(self.all_store.n_items(), 0, &new);
        }
        self.restoring.set(false);
        let me = unsafe { libc::getuid() } as i64;
        let groups = manage::group_processes(&snap.procs, |p| app_keys.get(&p.pid).cloned().flatten(), me);
        let names: HashMap<String, String> = self.app_info.borrow().iter().map(|(k, v)| (k.clone(), v.1.clone())).collect();
        self.history.borrow_mut().add(&snap, &groups.0, &names);
        self.groups.replace(groups);
        self.samples.set(self.samples.get() + 1);
        if self.samples.get() % HISTORY_SAVE_EVERY == 0 {
            self.save_history();
        }
        self.last.replace(Some(snap.clone()));
        let pages = self.pages.borrow().clone();
        if let Some(p) = pages.iter().find(|p| p.id() == "performance") {
            p.update(&snap); // its history always fills
        }
        if let Some(p) = self.current().filter(|p| p.id() != "performance") {
            p.update(&snap);
        }
        self.update_info();
    }

    pub fn save_history(&self) {
        let v = self.history.borrow().to_value();
        if let Some(m) = v.as_object() {
            let _ = sonata_config::save("activity-history", m);
        }
    }

    fn destroyed(&self) {
        self.alive.set(false);
        if let Some(id) = self.timer.take() {
            id.remove();
        }
        if self.samples.get() > 0 {
            self.save_history();
        }
        let me = self.me.clone();
        WINDOWS.with(|l| l.borrow_mut().retain(|w| !Weak::ptr_eq(&Rc::downgrade(w), &me)));
    }

    // -- process actions --------------------------------------------------------------------------
    /// Whether `row` runs below `ancestor` (End process tree).
    pub fn descends(&self, row: &TaskRow, ancestor: &TaskRow) -> bool {
        let rows = self.pid_rows.borrow();
        let mut seen = std::collections::HashSet::new();
        let mut pid = row.data().ppid;
        let target = ancestor.pid();
        while pid != 0 && seen.insert(pid) {
            if pid == target {
                return true;
            }
            pid = rows.get(&pid).map(|p| p.data().ppid).unwrap_or(0);
        }
        false
    }

    /// SIGTERM (force: SIGKILL). A system process or another user's asks
    /// first, like Windows.
    pub fn end_task(&self, rows: Vec<TaskRow>, force: bool) {
        if rows.is_empty() {
            return;
        }
        let me = unsafe { libc::getuid() } as i64;
        let sig = if force { libc::SIGKILL } else { libc::SIGTERM };
        if rows.iter().all(|r| r.data().uid == me) {
            self.signal(&rows, sig);
            return;
        }
        let name = rows[0].name();
        let w = self.me.clone();
        sonata_ui::dialog::alert(
            &fmt(&tr!("Do you want to end the system process “{name}”?"), &[("name", &name)]),
            &tr!("Ending this process may make the system unstable or lose unsaved data. It may need an administrator."),
            &[("cancel", tr!("Cancel"), ""), ("end", tr!("End process"), "destructive")],
            move |rid| {
                if rid == "end" {
                    if let Some(t) = w.upgrade() {
                        t.signal(&rows, sig);
                    }
                }
            },
            Some(self.window.upcast_ref()),
        );
    }

    fn signal(&self, rows: &[TaskRow], sig: i32) {
        let mut denied = Vec::new();
        for r in rows {
            if unsafe { libc::kill(r.pid() as libc::pid_t, sig) } != 0 && std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM) {
                denied.push(r.clone());
            }
        }
        if let Some(d) = denied.first() {
            sonata_ui::dialog::alert(&fmt(&tr!("“{name}” couldn't be ended."), &[("name", &d.name())]), &tr!("You don't have permission to end a process that belongs to another user."), &[("ok", tr!("OK"), "default")], |_| {}, Some(self.window.upcast_ref()));
        }
        if self.timer.borrow().is_some() && !self.busy.get() {
            let w = self.me.clone();
            glib::timeout_add_local_once(std::time::Duration::from_millis(300), move || {
                if let Some(t) = w.upgrade().filter(|t| t.alive.get()) {
                    t.tick();
                }
            });
        }
    }

    pub fn exe_path(row: &TaskRow) -> String {
        if let Ok(p) = std::fs::read_link(format!("/proc/{}/exe", row.pid())) {
            return p.display().to_string();
        }
        let argv0 = row.data().cmdline.split_whitespace().next().unwrap_or("").to_string();
        if argv0.starts_with('/') {
            return argv0;
        }
        sonata_backend::procfs::which(&argv0).map(|p| p.display().to_string()).unwrap_or_default()
    }

    pub fn open_location(&self, row: &TaskRow) {
        let path = Self::exe_path(row);
        if !path.is_empty() {
            self.reveal_path(&path);
        }
    }

    /// Files shows the item selected in its folder (FileManager1).
    pub fn reveal_path(&self, path: &str) {
        let path = std::path::PathBuf::from(path);
        let folder_uri = gio::File::for_path(path.parent().unwrap_or(&path)).uri().to_string();
        let uri = gio::File::for_path(&path).uri().to_string();
        gio::bus_get(gio::BusType::Session, gio::Cancellable::NONE, move |bus| {
            let open_folder = move || {
                let _ = gio::AppInfo::launch_default_for_uri(&folder_uri, None::<&gio::AppLaunchContext>);
            };
            let Ok(bus) = bus else { return open_folder() };
            let args = (vec![uri], "").to_variant();
            bus.call(Some("org.freedesktop.FileManager1"), "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1", "ShowItems", Some(&args), None, gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE, move |res| {
                if res.is_err() {
                    open_folder();
                }
            });
        });
    }

    pub fn search_online(&self, name: &str) {
        let _ = gio::AppInfo::launch_default_for_uri(&format!("{SEARCH_URL}{}", quote_plus(name)), None::<&gio::AppLaunchContext>);
    }

    /// The Properties lines; path=false skips the Location (a readlink),
    /// which never changes.
    fn props(&self, row: &TaskRow, path: bool) -> Vec<(&'static str, String, String)> {
        let ppid = row.data().ppid;
        let parent = self.pid_rows.borrow().get(&ppid).cloned();
        let started = glib::DateTime::from_unix_local(row.data().started as i64).ok().and_then(|d| d.format("%d %b %Y, %H:%M:%S").ok()).map(|s| s.to_string()).unwrap_or_default();
        let status = row.s("status");
        vec![
            ("pid", "PID".into(), row.pid().to_string()),
            ("parent", tr!("Parent process"), parent.map_or(ppid.to_string(), |p| format!("{} ({ppid})", p.name()))),
            ("user", tr!("User"), format!("{} ({})", row.s("user"), row.data().uid)),
            ("status", tr!("Status"), sonata_i18n::gettext(if status.is_empty() { "Running" } else { &status })),
            ("cpu", tr!("CPU"), fmt_cpu(row.n("cpu"))),
            ("cpu_time", tr!("CPU time"), fmt_cpu_time(row.n("cpu_time"))),
            ("threads", tr!("Threads"), fmt_count(row.n("threads"))),
            ("mem", tr!("Memory"), fmt_mem(row.n("mem"))),
            ("started", tr!("Started"), started),
            ("path", tr!("Location"), if path { Some(Self::exe_path(row)).filter(|p| !p.is_empty()).unwrap_or_else(|| "–".into()) } else { String::new() }),
        ]
    }

    pub fn show_properties(&self, rows: Vec<TaskRow>) {
        let Some(row) = rows.first().cloned() else { return };
        let anchor: gtk::Widget = self.current().and_then(|p| {
            let b = p.buttons();
            let mut c = b.first_child();
            while let Some(w) = c {
                if w.tooltip_text().as_deref() == Some(tr!("Properties").as_str()) {
                    return Some(w);
                }
                c = w.next_sibling();
            }
            None
        }).unwrap_or_else(|| self.search.clone().upcast());
        let bx = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).css_classes(["tm-info"]).build();
        let head = gtk::Box::new(gtk::Orientation::Horizontal, 10);
        let img = gtk::Image::builder().pixel_size(32).build();
        match row.icon() {
            Some(i) => img.set_from_gicon(&i),
            None => img.set_icon_name(Some(GENERIC_ICON)),
        }
        head.append(&img);
        head.append(&gtk::Label::builder().label(row.name()).xalign(0.0).css_classes(["tm-info-name"]).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(28).build());
        bx.append(&head);
        let grid = gtk::Grid::builder().row_spacing(3).column_spacing(12).build();
        let mut labels = HashMap::new();
        for (i, (key, title, value)) in self.props(&row, true).into_iter().enumerate() {
            grid.attach(&gtk::Label::builder().label(fmt(&tr!("{label}:"), &[("label", &title)])).xalign(1.0).css_classes(["tm-key"]).build(), 0, i as i32, 1, 1);
            let v = gtk::Label::builder().label(value).xalign(0.0).selectable(true).css_classes(["tm-val"]).ellipsize(gtk::pango::EllipsizeMode::Middle).max_width_chars(40).build();
            grid.attach(&v, 1, i as i32, 1, 1);
            labels.insert(key, v);
        }
        bx.append(&grid);
        bx.append(&gtk::Label::builder().label(tr!("Command line:")).xalign(0.0).css_classes(["tm-key"]).build());
        let cmd: String = row.data().cmdline.replace('\n', " ").chars().take(2000).collect();
        bx.append(&gtk::Label::builder().label(cmd).xalign(0.0).wrap(true).wrap_mode(gtk::pango::WrapMode::WordChar).selectable(true).max_width_chars(48).lines(6).ellipsize(gtk::pango::EllipsizeMode::End).css_classes(["tm-cmd"]).build());
        let pop = sonata_ui::panel::popup(&anchor, &bx, gtk::PositionType::Bottom);
        self.info.replace(Some((row, labels)));
        let w = self.me.clone();
        pop.connect_closed(move |_| {
            if let Some(t) = w.upgrade() {
                t.info.replace(None);
            }
        });
    }

    fn update_info(&self) {
        let info = self.info.borrow();
        let Some((row, labels)) = info.as_ref() else { return };
        for (key, _, value) in self.props(row, false) {
            if key != "path" {
                if let Some(l) = labels.get(key).filter(|l| l.label() != value) {
                    l.set_label(&value);
                }
            }
        }
    }

    pub fn selected_rows(&self) -> Vec<TaskRow> {
        self.current().map(|p| p.selected_rows()).unwrap_or_default()
    }

    fn key(&self, keyval: gtk::gdk::Key, state: gtk::gdk::ModifierType) -> bool {
        use gtk::gdk::Key;
        let cmd = state.intersects(gtk::gdk::ModifierType::CONTROL_MASK | gtk::gdk::ModifierType::SUPER_MASK);
        let k = keyval.to_lower();
        if !cmd {
            if keyval == Key::Delete && !self.search.has_focus() {
                self.end_task(self.selected_rows(), false);
                return true;
            }
            return false;
        }
        match k {
            Key::f => {
                self.search.grab_focus();
            }
            Key::i => self.show_properties(self.selected_rows()),
            Key::w => self.window.close(),
            k if (Key::_1..=Key::_7).contains(&k) => {
                use glib::translate::IntoGlib;
                let n = (k.into_glib() - Key::_1.into_glib()) as usize;
                let id = self.pages.borrow().get(n).map(|p| p.id());
                if let Some(id) = id {
                    self.show_page(id);
                }
            }
            _ => return false,
        }
        true
    }
}

/// A search query in a URL: spaces as +, the rest percent-encoded.
pub fn quote_plus(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b' ' => out.push('+'),
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// One Task Manager window (opening it again brings it forward).
pub fn present(app: &gtk::Application) {
    let existing = WINDOWS.with(|w| w.borrow().first().cloned());
    match existing {
        Some(w) => w.window.present(),
        None => {
            let w = TaskManagerWindow::new(app, None);
            WINDOWS.with(|l| l.borrow_mut().push(w.clone()));
            w.window.present();
        }
    }
}

/// Every snapshot's procs by pid (tests).
pub fn pids(snap: &Snapshot) -> BTreeMap<i64, Proc> {
    snap.procs.clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn search_queries_quoted() {
        assert_eq!(quote_plus("gnome text-editor"), "gnome+text-editor");
        assert_eq!(quote_plus("a&b/ç"), "a%26b%2F%C3%A7");
    }

    #[test]
    fn apps_found_by_their_executable() {
        let idx = HashMap::from([("firefox".to_string(), (None, "Firefox".to_string())), ("wine".to_string(), (None, "Some Game".to_string()))]);
        let p = Proc { exe: "firefox".into(), comm: "firefox".into(), name: "firefox".into(), ..Default::default() };
        assert_eq!(app_for(&p, &idx), (Some("firefox".into()), None, "Firefox".into()));
        let helper = Proc { exe: "firefox".into(), comm: "Web Content".into(), name: "Web Content".into(), ..Default::default() };
        assert_eq!(app_for(&helper, &idx).2, "Web Content"); // helpers keep their name
        // a launcher's name says nothing about the app
        let wine = Proc { exe: "wine".into(), comm: "wine".into(), name: "wine".into(), ..Default::default() };
        assert_eq!(app_for(&wine, &idx).0, None);
    }
}
