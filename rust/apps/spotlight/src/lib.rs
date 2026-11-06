//! Spotlight, macOS Big Sur (shell/spotlight.py): Super+Space or the menu
//! bar's magnifier. A glass search bar in the upper middle of the screen;
//! typing lists Top Hit, Applications, Folders and Documents, plus a
//! calculator answer, with a preview of the selected result on the right.
//! Up/Down move, Return opens, Escape closes. Resident and hidden between
//! uses (instant opening); files come from an index of the home folders
//! built in the background.
//!
//! A package, an AppImage, an archive or a script opened from here is
//! installed, run or extracted, like a double-click in Files (packages.rs).
//!
//! Started with --background (at login), it draws itself once, unseen
//! (layer.prewarm): the first real open is as quick as the others.

pub mod calc;
pub mod index;

use gtk::prelude::*;
use sonata_i18n::{n_, tr};
use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Duration;

const WIDTH: i32 = 680;
/// how many of each are shown
const SHOW_APPS: usize = 6;
const SHOW_FOLDERS: usize = 4;
const SHOW_DOCS: usize = 8;
/// the results area keeps one height while typing (it used to grow and
/// shrink with every key); it slides open under the bar once, then stays
const RESULTS_H: i32 = 400;
/// the search field and the line under it (.sp-field: 48 + padding)
const FIELD_H: i32 = 64;
const CLOSE_MS: u64 = 120;
const REVEAL_MS: u32 = 180;
/// a burst of keys: one search
const SEARCH_DELAY_MS: u64 = 40;
/// layer.prewarm's defaults: the warm-up draw starts this long after the
/// start, and lasts this many frames
const PREWARM_DELAY_MS: u64 = 1500;
const PREWARM_FRAMES: u32 = 3;

/// layer.prewarm (shell/layer.py): draw the hidden window once, invisibly,
/// shortly after start -- the first real open then doesn't pay for
/// building the widgets' render nodes, loading icons and fonts and
/// compiling the GPU shaders. Mapped at 1 % opacity, taking no keyboard or
/// clicks, hidden again after `frames` frames. `warming` is true meanwhile:
/// an open asked for then ends the warm-up and stays (end_warm_up).
pub fn prewarm(win: &gtk::Window, delay_ms: u64, frames: u32, warming: Rc<Cell<bool>>) {
    let win = win.clone();
    glib::timeout_add_local_once(Duration::from_millis(delay_ms), move || {
        if win.is_visible() {
            return; // opened already: nothing to do
        }
        warming.set(true);
        sonata_shell::layer::set_keyboard(&win, false);
        win.set_opacity(0.01);
        win.set_can_target(false);
        win.present();
        sonata_shell::layer::set_input_region(&win, &[]);
        let n = Cell::new(0u32);
        win.add_tick_callback(move |win, _| {
            if !warming.get() {
                return glib::ControlFlow::Break; // opened for real meanwhile
            }
            n.set(n.get() + 1);
            if n.get() < frames {
                return glib::ControlFlow::Continue;
            }
            win.set_visible(false);
            end_warm_up(win.upcast_ref(), &warming);
            glib::ControlFlow::Break
        });
    });
}

/// The window back as it opens: opaque, taking clicks (on the whole
/// surface) and the keyboard.
pub fn end_warm_up(win: &gtk::Window, warming: &Cell<bool>) {
    warming.set(false);
    win.set_opacity(1.0);
    win.set_can_target(true);
    sonata_shell::layer::set_keyboard(win, true);
    if let Some(surface) = win.surface() {
        let all = gtk::cairo::Region::create_rectangle(&gtk::cairo::RectangleInt::new(0, 0, i32::MAX / 2, i32::MAX / 2));
        surface.set_input_region(&all);
    }
}

/// Sonata's Files: folders open there. The Rust session's Files has the
/// same id; on trial the Python Files keeps folders (the Rust one takes no
/// file types then).
const FILES_ID: &str = "io.github.vinioliveiras.sonata2.files.desktop";

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.spotlight"
    } else {
        "io.github.vinioliveiras.sonata2.spotlight"
    }
}

/// (margin above the panel, results height) on a display this tall: 22 %
/// down like macOS, the results 400 px; on a short display (a small laptop
/// at 2x) both give way so the results stay on screen.
pub fn layout_for(screen_h: i32) -> (i32, i32) {
    let top = (screen_h as f64 * 0.22) as i32;
    let room = screen_h - FIELD_H - 16; // 16: a margin under the results
    let results = (room - 24).min(RESULTS_H).max(160);
    ((room - results).min(top).max(24), results)
}

/// Launchpad's app search (launchpad_model.search): prefix matches on the
/// name first, then word prefixes, then substrings anywhere (the name, then
/// the generic name, description and keywords).
pub fn search_apps(meta: &[(String, String, String)], query: &str, limit: usize) -> Vec<String> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return Vec::new();
    }
    let mut ranked: Vec<(u8, String, &String)> = meta
        .iter()
        .filter_map(|(id, name, extra)| {
            let n = name.to_lowercase();
            let rank = if n.starts_with(&q) {
                0
            } else if n.split_whitespace().any(|w| w.starts_with(&q)) {
                1
            } else if n.contains(&q) {
                2
            } else if extra.to_lowercase().contains(&q) {
                3
            } else {
                return None;
            };
            Some((rank, n, id))
        })
        .collect();
    ranked.sort();
    ranked.into_iter().take(limit).map(|(_, _, id)| id.clone()).collect()
}

#[derive(Clone, Debug, PartialEq)]
enum Hit {
    App(String),
    Calc(String, String),
    File(PathBuf),
}

/// (section, hit) in the order shown: the calculator, Top Hit, the rest of
/// the apps, folders, documents.
fn arrange(calc: Option<(String, String)>, apps: Vec<String>, found: Vec<(PathBuf, bool)>) -> Vec<(&'static str, Hit)> {
    let mut out = Vec::new();
    if let Some((q, a)) = calc {
        out.push((n_!("Calculator"), Hit::Calc(q, a)));
    }
    let mut folders: Vec<PathBuf> = found.iter().filter(|f| f.1).map(|f| f.0.clone()).take(SHOW_FOLDERS).collect();
    let mut docs: Vec<PathBuf> = found.iter().filter(|f| !f.1).map(|f| f.0.clone()).take(SHOW_DOCS).collect();
    if let Some((first, rest)) = apps.split_first() {
        out.push((n_!("Top Hit"), Hit::App(first.clone())));
        out.extend(rest.iter().map(|a| (n_!("Applications"), Hit::App(a.clone()))));
    } else if let Some(top) = folders.first().or(docs.first()).cloned() {
        out.push((n_!("Top Hit"), Hit::File(top.clone())));
        folders.retain(|p| *p != top);
        docs.retain(|p| *p != top);
    }
    out.extend(folders.into_iter().map(|p| (n_!("Folders"), Hit::File(p))));
    out.extend(docs.into_iter().map(|p| (n_!("Documents"), Hit::File(p))));
    out
}

struct Spotlight {
    win: gtk::ApplicationWindow,
    panel: gtk::Box,
    entry: gtk::Text,
    list: gtk::ListBox,
    scroller: gtk::ScrolledWindow,
    prev: gtk::Box,
    reveal: gtk::Revealer,
    index: index::Index,
    apps: RefCell<Vec<gio::AppInfo>>,
    hits: RefCell<Vec<(gtk::ListBoxRow, Hit)>>,
    search_src: RefCell<Option<glib::SourceId>>,
    close_src: RefCell<Option<glib::SourceId>>,
    placed_for: RefCell<Option<gtk::gdk::Surface>>,
    /// drawn unseen right now (prewarm)
    warming: Rc<Cell<bool>>,
}

impl Spotlight {
    fn new(app: &gtk::Application) -> Rc<Self> {
        let win = gtk::ApplicationWindow::builder().application(app).title("Search").decorated(false).resizable(true).css_classes(["sonata-spotlight"]).build();
        let panel = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["sp-panel"]).halign(gtk::Align::Center).valign(gtk::Align::Start).build();
        let field = gtk::Box::builder().spacing(10).css_classes(["sp-field"]).build();
        field.append(&gtk::Image::builder().icon_name("sonata-search-symbolic").pixel_size(22).build());
        let entry = gtk::Text::builder().placeholder_text(tr!("Search").as_str()).hexpand(true).build();
        field.append(&entry);
        panel.append(&field);
        let results = gtk::Box::new(gtk::Orientation::Vertical, 0);
        results.append(&gtk::Box::builder().css_classes(["sp-sep"]).build());
        let body = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        let list = gtk::ListBox::builder().css_classes(["sp-list"]).selection_mode(gtk::SelectionMode::Single).build();
        list.set_placeholder(Some(&gtk::Label::builder().label(tr!("No Results").as_str()).css_classes(["sp-prev-meta"]).margin_top(16).build()));
        let scroller = gtk::ScrolledWindow::builder().child(&list).hscrollbar_policy(gtk::PolicyType::Never).build();
        scroller.set_size_request(290, RESULTS_H);
        body.append(&scroller);
        let prev = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).css_classes(["sp-preview"]).hexpand(true).valign(gtk::Align::Fill).build();
        body.append(&prev);
        results.append(&body);
        let reveal = gtk::Revealer::builder().child(&results).transition_type(gtk::RevealerTransitionType::SlideDown).transition_duration(sonata_ui::tokens::ms(REVEAL_MS as f64)).reveal_child(false).build();
        panel.append(&reveal);
        // one width: a long file name in the results never widens Search
        let fixed = sonata_ui::fixed::FixedWidth::new(&panel, WIDTH);
        fixed.set_halign(gtk::Align::Center);
        fixed.set_valign(gtk::Align::Start);
        win.set_child(Some(&fixed));
        sonata_ui::theme::fade_too(&win); // (Dark Mode cross-fades it, as Python)
        let me = Rc::new(Self {
            win,
            panel,
            entry,
            list,
            scroller,
            prev,
            reveal,
            index: index::Index::default(),
            apps: RefCell::default(),
            hits: RefCell::default(),
            search_src: RefCell::default(),
            close_src: RefCell::default(),
            placed_for: RefCell::default(),
            warming: Rc::default(),
        });
        me.glass_class();
        me.wire();
        // an overlay on the whole display, taking the keyboard: a click beside
        // the panel closes it (without layer-shell: a plain window)
        sonata_shell::layer::overlay_fullscreen(me.win.upcast_ref(), "sonata2-spotlight");
        let weak = Rc::downgrade(&me);
        me.win.connect_realize(move |_| {
            if let Some(me) = weak.upgrade() {
                me.place(None);
            }
        });
        me
    }

    /// The glass panel's "solid" class in step with the setting.
    fn glass_class(self: &Rc<Self>) {
        let sync = {
            let panel = self.panel.downgrade();
            move || {
                if let Some(p) = panel.upgrade() {
                    if sonata_ui::glass() { p.remove_css_class("solid") } else { p.add_css_class("solid") }
                }
            }
        };
        sync();
        sonata_ui::on_change(sync);
    }

    fn wire(self: &Rc<Self>) {
        let weak = Rc::downgrade(self);
        self.entry.connect_changed(move |_| {
            if let Some(me) = weak.upgrade() {
                me.search_soon();
            }
        });
        let weak = Rc::downgrade(self);
        self.entry.connect_activate(move |_| {
            if let Some(me) = weak.upgrade() {
                if let Some(r) = me.list.selected_row() {
                    me.open(&r);
                }
            }
        });
        let weak = Rc::downgrade(self);
        self.list.connect_row_selected(move |_, r| {
            if let Some(me) = weak.upgrade() {
                me.preview(r);
            }
        });
        let weak = Rc::downgrade(self);
        self.list.connect_row_activated(move |_, r| {
            if let Some(me) = weak.upgrade() {
                me.open(r);
            }
        });
        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        let weak = Rc::downgrade(self);
        keys.connect_key_pressed(move |_, key, _, _| {
            let Some(me) = weak.upgrade() else { return glib::Propagation::Proceed };
            match key {
                gtk::gdk::Key::Escape => {
                    if me.entry.text().is_empty() {
                        me.close();
                    } else {
                        me.entry.set_text("");
                    }
                    glib::Propagation::Stop
                }
                gtk::gdk::Key::Down | gtk::gdk::Key::Up => {
                    me.move_by(if key == gtk::gdk::Key::Down { 1 } else { -1 });
                    glib::Propagation::Stop
                }
                _ => glib::Propagation::Proceed,
            }
        });
        self.win.add_controller(keys);
        let click = gtk::GestureClick::new();
        let weak = Rc::downgrade(self);
        click.connect_released(move |_, _, x, y| {
            let Some(me) = weak.upgrade() else { return };
            let hit = me.win.pick(x, y, gtk::PickFlags::DEFAULT);
            if hit.is_none() || hit.as_ref() == Some(me.win.upcast_ref()) {
                me.close(); // a click beside the panel
            }
        });
        self.win.add_controller(click);
    }

    fn place(&self, monitor: Option<gtk::gdk::Monitor>) {
        let mon = monitor.or_else(|| self.win.surface().and_then(|s| WidgetExt::display(&self.win).monitor_at_surface(&s)));
        let (top, results) = layout_for(mon.map(|m| m.geometry().height()).unwrap_or(900));
        self.panel.set_margin_top(top);
        self.scroller.set_size_request(290, results);
    }

    // -- open / close ------------------------------------------------------------------------
    fn toggle(self: &Rc<Self>) {
        if self.warming.get() {
            // asked for while it draws itself unseen: open for real
            end_warm_up(self.win.upcast_ref(), &self.warming);
            self.open_spotlight();
            return;
        }
        if self.win.is_visible() {
            self.close();
        } else {
            self.open_spotlight();
        }
    }

    fn open_spotlight(self: &Rc<Self>) {
        self.index.refresh();
        // apps in Launchpad's Hidden folder (behind the password) aren't found here either
        let lp = sonata_config::load("launchpad", &serde_json::json!({"pages": [], "hidden": []}));
        let hidden: Vec<String> = lp.get("hidden").and_then(|h| h.as_array()).map(|a| a.iter().filter_map(|v| v.as_str()).map(|s| format!("{s}.desktop")).collect()).unwrap_or_default();
        let apps: Vec<gio::AppInfo> = gio::AppInfo::all().into_iter().filter(|a| sonata_ui::apps::should_show(a) && a.id().is_some_and(|id| !hidden.contains(&id.to_string()))).collect();
        self.apps.replace(apps);
        if let Some(id) = self.close_src.take() {
            sonata_ui::cancel(id); // reopened while fading out
        }
        self.panel.remove_css_class("closing");
        self.entry.set_text("");
        self.search();
        self.reveal.set_reveal_child(false);
        self.panel.remove_css_class("opening"); // (again: restarts the animation)
        self.panel.add_css_class("opening");
        self.win.present();
        if let Some(surface) = self.win.surface() {
            if self.placed_for.borrow().as_ref() != Some(&surface) {
                // the compositor puts it on the focused display: placed again
                // for that one (else a 4K's margin pushed the results off a laptop)
                self.placed_for.replace(Some(surface.clone()));
                let weak = Rc::downgrade(self);
                surface.connect_enter_monitor(move |_, m| {
                    if let Some(me) = weak.upgrade() {
                        me.place(Some(m.clone()));
                    }
                });
            }
        }
        self.entry.grab_focus();
    }

    fn close(self: &Rc<Self>) {
        if !self.win.is_visible() || self.close_src.borrow().is_some() {
            return;
        }
        self.panel.remove_css_class("opening");
        self.panel.add_css_class("closing");
        let weak = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(CLOSE_MS), move || {
            if let Some(me) = weak.upgrade() {
                me.close_src.take();
                me.win.set_visible(false);
                me.panel.remove_css_class("closing");
            }
        });
        self.close_src.replace(Some(id));
    }

    fn move_by(&self, d: i32) {
        let rows: Vec<gtk::ListBoxRow> = self.hits.borrow().iter().map(|h| h.0.clone()).collect();
        if rows.is_empty() {
            return;
        }
        let cur = self.list.selected_row();
        let i = cur.and_then(|c| rows.iter().position(|r| *r == c)).map(|i| i as i32).unwrap_or(-1);
        let next = (i + d).clamp(0, rows.len() as i32 - 1) as usize;
        self.list.select_row(Some(&rows[next]));
    }

    // -- results ------------------------------------------------------------------------------------
    fn search_soon(self: &Rc<Self>) {
        if let Some(id) = self.search_src.take() {
            sonata_ui::cancel(id);
        }
        let weak = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(SEARCH_DELAY_MS), move || {
            if let Some(me) = weak.upgrade() {
                me.search_src.take();
                me.search();
            }
        });
        self.search_src.replace(Some(id));
    }

    fn app(&self, id: &str) -> Option<gio::AppInfo> {
        self.apps.borrow().iter().find(|a| a.id().is_some_and(|x| x == id)).cloned()
    }

    fn search(&self) {
        let q = self.entry.text().trim().to_string();
        self.list.remove_all();
        self.hits.borrow_mut().clear();
        if q.is_empty() {
            self.reveal.set_reveal_child(false);
            return;
        }
        let meta: Vec<(String, String, String)> = self
            .apps
            .borrow()
            .iter()
            .filter_map(|a| {
                let d = a.clone().downcast::<gio::DesktopAppInfo>().ok();
                let generic = d.as_ref().and_then(|d| d.generic_name()).map(|s| s.to_string()).unwrap_or_default();
                let keywords = d.as_ref().map(|d| d.keywords().iter().map(|k| k.to_string()).collect::<Vec<_>>().join(" ")).unwrap_or_default();
                let desc = a.description().map(|s| s.to_string()).unwrap_or_default();
                let extra = [generic, desc, keywords].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" ");
                Some((a.id()?.to_string(), sonata_ui::apps::display_name(a), extra))
            })
            .collect();
        let calc = calc::calculate(&q).map(|a| (q.clone(), a));
        let found = if q.chars().count() > 1 { self.index.search(&q) } else { Vec::new() };
        let results = arrange(calc, search_apps(&meta, &q, SHOW_APPS), found);
        let mut last = "";
        for (section, hit) in &results {
            if *section != last {
                let head = gtk::ListBoxRow::builder().selectable(false).activatable(false).css_classes(["sp-head"]).build();
                head.set_child(Some(&gtk::Label::builder().label(sonata_i18n::gettext(section).as_str()).xalign(0.0).build()));
                self.list.append(&head);
                last = section;
            }
            let row = gtk::ListBoxRow::new();
            let bx = gtk::Box::new(gtk::Orientation::Horizontal, 8);
            let img = gtk::Image::builder().pixel_size(20).build();
            bx.append(&img);
            bx.append(&gtk::Label::builder().label(self.title(hit).as_str()).xalign(0.0).hexpand(true).ellipsize(gtk::pango::EllipsizeMode::Middle).build());
            self.icon(&img, hit);
            row.set_child(Some(&bx));
            self.list.append(&row);
            self.hits.borrow_mut().push((row, hit.clone()));
        }
        // once open, the results area stays (a "no results" moment while
        // typing doesn't collapse and reopen it)
        if !results.is_empty() || !self.reveal.reveals_child() {
            self.reveal.set_reveal_child(!results.is_empty());
        }
        let first = self.hits.borrow().first().map(|h| h.0.clone());
        if let Some(r) = first {
            self.list.select_row(Some(&r));
        }
    }

    fn title(&self, hit: &Hit) -> String {
        match hit {
            Hit::App(id) => self.app(id).map(sonata_ui::apps::display_name).unwrap_or_default(),
            Hit::Calc(q, a) => format!("{q} = {a}"),
            Hit::File(p) => p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
        }
    }

    fn icon(&self, img: &gtk::Image, hit: &Hit) {
        match hit {
            Hit::App(id) => {
                if let Some(a) = self.app(id) {
                    sonata_ui::icons::set_image(img, &sonata_ui::icons::app_icon(&a));
                }
            }
            Hit::Calc(..) => img.set_icon_name(Some("sonata-calculator")),
            Hit::File(p) => match gio::File::for_path(p).query_info("standard::icon", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE).ok().and_then(|i| i.icon()) {
                Some(icon) => sonata_ui::icons::set_image(img, &icon),
                None => img.set_icon_name(Some("text-x-generic")),
            },
        }
    }

    fn hit_of(&self, row: &gtk::ListBoxRow) -> Option<Hit> {
        self.hits.borrow().iter().find(|h| h.0 == *row).map(|h| h.1.clone())
    }

    fn preview(&self, row: Option<&gtk::ListBoxRow>) {
        while let Some(c) = self.prev.first_child() {
            self.prev.remove(&c);
        }
        let Some(hit) = row.and_then(|r| self.hit_of(r)) else { return };
        if let Hit::Calc(_, answer) = &hit {
            self.prev.append(&gtk::Label::builder().label(answer.as_str()).css_classes(["sp-calc"]).wrap(true).valign(gtk::Align::Center).vexpand(true).build());
            return;
        }
        let image = match &hit {
            Hit::File(p) => gio::content_type_guess(Some(p), &[]).0.starts_with("image/"),
            _ => false,
        };
        if let (true, Hit::File(p)) = (image, &hit) {
            // pictures: the picture itself
            let pic = gtk::Picture::builder().content_fit(gtk::ContentFit::Contain).margin_top(6).build();
            pic.set_filename(Some(p));
            pic.set_size_request(-1, 170);
            self.prev.append(&pic);
        } else {
            let img = gtk::Image::builder().pixel_size(128).margin_top(10).build();
            self.icon(&img, &hit);
            self.prev.append(&img);
        }
        let label = |text: &str, class: &str| gtk::Label::builder().label(text).css_classes([class]).wrap(true).justify(gtk::Justification::Center).max_width_chars(40).build();
        self.prev.append(&label(&self.title(&hit), "sp-prev-name"));
        let meta = match &hit {
            Hit::App(id) => self.app(id).and_then(|a| a.description()).map(|s| s.to_string()).unwrap_or_default(),
            Hit::File(p) => {
                let home = glib::home_dir().display().to_string();
                let dir = p.parent().map(|d| d.display().to_string()).unwrap_or_default();
                dir.replacen(&home, "~", 1)
            }
            Hit::Calc(..) => String::new(),
        };
        self.prev.append(&label(&meta, "sp-prev-meta"));
    }

    fn open(self: &Rc<Self>, row: &gtk::ListBoxRow) {
        let Some(hit) = self.hit_of(row) else { return };
        let ctx = WidgetExt::display(&self.win).app_launch_context();
        match hit {
            Hit::App(id) => {
                if let Some(a) = self.app(&id) {
                    // Sonata's launch path: the GPU, its scope, the app lock...
                    let _ = sonata_ui::apps::launch(&a, &[], Some(ctx.upcast_ref()));
                }
            }
            Hit::Calc(_, answer) => self.win.clipboard().set_text(&answer), // like macOS: copy the result
            Hit::File(p) => open_path(&p, &ctx),
        }
        self.close();
    }
}

/// A folder in Sonata's Files (else the default file manager); a package,
/// an archive or a script installed / run / extracted (packages.rs); any
/// other file with its default app.
fn open_path(p: &Path, ctx: &gtk::gdk::AppLaunchContext) {
    let uri = gio::File::for_path(p).uri();
    if p.is_dir() {
        if let Some(files) = gio::DesktopAppInfo::new(FILES_ID) {
            if sonata_ui::apps::launch_uris(&files, &[uri.as_str()], Some(ctx.upcast_ref())).is_ok() {
                return;
            }
        }
    } else if sonata_files::packages::open_path(p, None) {
        return; // install / run / extract
    }
    let _ = sonata_ui::apps::launch_default_for_uri(&uri, Some(ctx.upcast_ref()));
}

/// `sonata2-rs spotlight [--background] [--search TEXT]`: single instance,
/// resident: running it again toggles it.
pub fn run(args: &[String]) -> glib::ExitCode {
    let preview = args.iter().any(|a| a == "--preview");
    if !preview {
        sonata_shell::layer::ensure_preload();
    }
    let background = args.iter().any(|a| a == "--background");
    let search = args.iter().position(|a| a == "--search").and_then(|i| args.get(i + 1)).cloned();
    let app = gtk::Application::builder().application_id(app_id()).build();
    let state: Rc<RefCell<Option<Rc<Spotlight>>>> = Rc::default();
    let hold: RefCell<Option<gio::ApplicationHoldGuard>> = RefCell::default();
    let first = Cell::new(true);
    app.connect_activate(move |app| {
        if let Some(s) = state.borrow().as_ref() {
            s.toggle();
            return;
        }
        sonata_ui::setup();
        sonata_ui::css::register_module("shell/spotlight.py");
        let s = Spotlight::new(app);
        hold.replace(Some(app.hold()));
        state.replace(Some(s.clone()));
        if first.replace(false) && background {
            s.index.refresh();
            if !preview {
                prewarm(s.win.upcast_ref(), PREWARM_DELAY_MS, PREWARM_FRAMES, s.warming.clone());
            }
            return;
        }
        s.open_spotlight();
        if let Some(text) = search.clone() {
            let s = s.clone();
            glib::timeout_add_local_once(Duration::from_millis(300), move || s.entry.set_text(&text));
        }
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A package, an archive or a script opened from Spotlight is
    /// installed, run or extracted (spotlight.py's _open).
    #[test]
    fn packages_open_the_files_way() {
        let src = include_str!("lib.rs");
        let open = &src[src.find("fn open_path(").unwrap()..];
        assert!(open[..open.find("\n}").unwrap()].contains("sonata_files::packages::open_path("));
    }

    /// Vini, in the Rust session: the first Search after login opened with
    /// a lag the later ones didn't have -- the Python one draws itself once,
    /// unseen, at login (layer.prewarm); the Rust one never had.
    #[test]
    fn test_first_search_after_login_lagged() {
        if gtk::init().is_err() {
            return; // (no display)
        }
        let win = gtk::Window::new();
        win.set_child(Some(&gtk::Label::new(Some("Search"))));
        let mapped = Rc::new(Cell::new(false));
        let m = mapped.clone();
        win.connect_map(move |_| m.set(true));
        let warming: Rc<Cell<bool>> = Rc::default();
        prewarm(&win, 0, PREWARM_FRAMES, warming.clone());
        let ctx = glib::MainContext::default();
        let t = std::time::Instant::now();
        while !(mapped.get() && !win.is_visible()) && t.elapsed() < Duration::from_secs(5) {
            ctx.iteration(false);
        }
        assert!(mapped.get(), "never drawn before the first open");
        assert!(!win.is_visible() && !warming.get(), "still on screen after the warm-up");
        assert_eq!(win.opacity(), 1.0);
        assert!(win.can_target());
        // opened while it drew itself unseen: it stays, whole
        warming.set(true);
        win.set_opacity(0.01);
        win.set_can_target(false);
        end_warm_up(&win, &warming);
        assert!(!warming.get() && win.opacity() == 1.0 && win.can_target());
        win.destroy();
    }

    #[test]
    fn layout_on_tall_and_short_displays() {
        assert_eq!(layout_for(1080), (237, 400));
        let (top, results) = layout_for(600); // a small laptop at 2x: both give way
        assert!(top >= 24 && results >= 160 && top + 64 + results + 16 <= 600, "{top} {results}");
    }

    #[test]
    fn apps_ranked_as_launchpad_ranks_them() {
        let m = |id: &str, name: &str, extra: &str| (id.to_string(), name.to_string(), extra.to_string());
        let meta = vec![m("term", "Terminal", "shell"), m("vlc", "VLC media player", "video"), m("vids", "Videos", ""), m("mpv", "mpv", "media player video")];
        // "Videos" starts with it; VLC and mpv only say "video": by name then
        assert_eq!(search_apps(&meta, "vi", 6), ["vids", "mpv", "vlc"]);
        assert_eq!(search_apps(&meta, "media", 6), ["vlc", "mpv"]);
        assert_eq!(search_apps(&meta, "shell", 6), ["term"]);
        assert_eq!(search_apps(&meta, "  ", 6), Vec::<String>::new());
        assert_eq!(search_apps(&meta, "v", 1).len(), 1);
    }

    #[test]
    fn results_in_their_sections() {
        let f = |p: &str, d: bool| (PathBuf::from(p), d);
        let got = arrange(Some(("1+1".into(), "2".into())), vec!["a".into(), "b".into()], vec![f("/h/Dir", true), f("/h/doc.txt", false)]);
        let sections: Vec<&str> = got.iter().map(|g| g.0).collect();
        assert_eq!(sections, ["Calculator", "Top Hit", "Applications", "Folders", "Documents"]);
        // no app: the first folder is the Top Hit, not listed twice
        let got = arrange(None, vec![], vec![f("/h/Dir", true), f("/h/Dir2", true), f("/h/doc.txt", false)]);
        assert_eq!(got.iter().map(|g| (g.0, g.1.clone())).collect::<Vec<_>>(), [
            ("Top Hit", Hit::File("/h/Dir".into())),
            ("Folders", Hit::File("/h/Dir2".into())),
            ("Documents", Hit::File("/h/doc.txt".into())),
        ]);
    }
}
