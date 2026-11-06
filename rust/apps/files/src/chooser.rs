//! Open / Save panels (macOS NSOpenPanel / NSSavePanel), files/chooser.py:
//! served to every app through the file chooser portal (sonata-portal).
//!
//! A Files window in "chooser" mode: the same sidebar, views, search and
//! keyboard. Saving adds a "Save As" field above the browser, and a bottom
//! bar holds the file type pop-up (the app's filters) with Cancel and
//! Open / Save. on_done(uris or None, filter index) -- None: cancelled.

use crate::folder::{file_of, is_dir, is_virtual};
use crate::window::{Embed, FilesWindow};
use adw::prelude::*;
use sonata_i18n::{fmt, tr};
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};

/// kind of a filter rule (portal spec): 0 glob pattern, 1 MIME type
pub const GLOB: u32 = 0;
pub const MIME: u32 = 1;

pub type Filter = (String, Vec<(u32, String)>);
pub type OnDone = Box<dyn FnOnce(Option<Vec<String>>, usize)>;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    Open,
    Save,
    Folder,
}

/// fnmatch, case folded (the portal's globs: `*.txt`, `*.[jJ][pP]g`).
pub fn glob_match(pattern: &str, name: &str) -> bool {
    fn m(p: &[char], n: &[char]) -> bool {
        match p.first() {
            None => n.is_empty(),
            Some('*') => (0..=n.len()).any(|i| m(&p[1..], &n[i..])),
            Some('?') => !n.is_empty() && m(&p[1..], &n[1..]),
            Some('[') => {
                let Some(end) = p.iter().skip(2).position(|c| *c == ']').map(|e| e + 2) else { return !n.is_empty() && n[0] == '[' && m(&p[1..], &n[1..]) };
                let Some(&c) = n.first() else { return false };
                let set = &p[1..end];
                let (neg, set) = if set.first() == Some(&'!') { (true, &set[1..]) } else { (false, set) };
                let mut hit = false;
                let mut i = 0;
                while i < set.len() {
                    if i + 2 < set.len() && set[i + 1] == '-' {
                        hit |= set[i] <= c && c <= set[i + 2];
                        i += 3;
                    } else {
                        hit |= set[i] == c;
                        i += 1;
                    }
                }
                hit != neg && m(&p[end + 1..], &n[1..])
            }
            Some(&c) => n.first() == Some(&c) && m(&p[1..], &n[1..]),
        }
    }
    let p: Vec<char> = pattern.to_lowercase().chars().collect();
    let n: Vec<char> = name.to_lowercase().chars().collect();
    m(&p, &n)
}

pub fn matches(name: &str, ct: &str, rules: &[(u32, String)]) -> bool {
    rules.iter().any(|(kind, pattern)| match *kind {
        GLOB => glob_match(pattern, name),
        MIME => !ct.is_empty() && (pattern == ct || gio::content_type_is_a(ct, pattern) || gio::content_type_is_mime_type(ct, pattern)),
        _ => false,
    })
}

pub struct Chooser {
    pub files: Rc<FilesWindow>,
    mode: Mode,
    multiple: bool,
    filters: Vec<Filter>,
    filter_index: Cell<usize>,
    on_done: RefCell<Option<OnDone>>,
    name_entry: Option<gtk::Entry>,
    accept_btn: gtk::Button,
}

pub struct Options {
    pub mode: Mode,
    pub title: String,
    pub accept_label: String,
    pub multiple: bool,
    pub filters: Vec<Filter>,
    pub current_filter: usize,
    pub folder: Option<String>,
    pub name: String,
}

thread_local! {
    static OPEN: RefCell<Vec<Rc<Chooser>>> = const { RefCell::new(Vec::new()) };
}

impl Chooser {
    pub fn new(app: &gtk::Application, o: Options, on_done: OnDone) -> Rc<Self> {
        sonata_ui::css::register_module("files/chooser.py");
        // its own last size, never Files'
        let files = FilesWindow::new_with(app, o.folder.as_deref(), ("chooser", 820, 520), false);
        files.window.add_css_class("sonata-chooser");
        let title = if o.title.is_empty() {
            match o.mode {
                Mode::Open => tr!("Open"),
                Mode::Save => tr!("Save"),
                Mode::Folder => tr!("Choose Folder"),
            }
        } else {
            o.title.clone()
        };
        files.window.set_title(Some(&title));
        let name_entry = (o.mode == Mode::Save).then(|| gtk::Entry::builder().text(&o.name).hexpand(false).build());
        let label = if o.accept_label.is_empty() {
            match o.mode {
                Mode::Open => tr!("Open"),
                Mode::Save => tr!("Save"),
                Mode::Folder => tr!("Choose"),
            }
        } else {
            o.accept_label.replace('_', "")
        };
        let accept_btn = gtk::Button::builder().label(label).css_classes(["sonata-button", "default", "fc-accept"]).valign(gtk::Align::Center).build();
        let n = o.filters.len();
        let ch = Rc::new(Chooser { files, mode: o.mode, multiple: o.multiple, filters: o.filters, filter_index: Cell::new(o.current_filter.min(n.saturating_sub(1))), on_done: RefCell::new(Some(on_done)), name_entry, accept_btn });
        ch.layout();
        let (w1, w2, w3) = (Rc::downgrade(&ch), Rc::downgrade(&ch), Rc::downgrade(&ch));
        ch.files.embed.replace(Some(Embed {
            matches: Box::new(move |i| w1.upgrade().is_none_or(|c| c.shows(i))),
            open: Box::new(move |i| w2.upgrade().is_some_and(|c| c.open_item(i))),
            changed: Box::new(move || {
                if let Some(c) = w3.upgrade() {
                    c.update_accept();
                }
            }),
        }));
        let w = Rc::downgrade(&ch);
        ch.files.window.connect_close_request(move |_| {
            if let Some(c) = w.upgrade() {
                c.finish(None);
            }
            glib::Propagation::Proceed
        });
        ch.files.refilter();
        ch.update_accept();
        OPEN.with(|o| o.borrow_mut().push(ch.clone()));
        ch
    }

    fn layout(self: &Rc<Self>) {
        let content = &self.files.content;
        if let Some(entry) = &self.name_entry {
            let outer = gtk::Box::builder().css_classes(["fc-top"]).build();
            let row = gtk::Box::builder().spacing(8).halign(gtk::Align::Center).hexpand(true).build();
            outer.append(&row);
            row.append(&gtk::Label::new(Some(&tr!("Save As:"))));
            row.append(entry);
            let w = Rc::downgrade(self);
            entry.connect_activate(move |_| {
                if let Some(c) = w.upgrade() {
                    c.accept(None);
                }
            });
            let w = Rc::downgrade(self);
            entry.connect_changed(move |_| {
                if let Some(c) = w.upgrade() {
                    c.update_accept();
                }
            });
            // the name is selected up to its extension, ready to type over (macOS)
            let e = entry.downgrade();
            glib::idle_add_local_once(move || {
                if let Some(e) = e.upgrade() {
                    let t = e.text();
                    let stem = t.rfind('.').map(|i| t[..i].chars().count() as i32).filter(|i| *i > 0).unwrap_or(-1);
                    e.grab_focus();
                    e.select_region(0, stem);
                }
            });
            content.prepend(&outer);
        }
        let bar = gtk::Box::builder().spacing(8).css_classes(["fc-bottom"]).build();
        if !self.filters.is_empty() {
            bar.append(&gtk::Label::builder().label(if self.mode == Mode::Save { tr!("Format:") } else { tr!("Show:") }).css_classes(["fc-caption"]).build());
            let names: Vec<String> = self.filters.iter().map(|f| f.0.clone()).collect();
            let w = Rc::downgrade(self);
            let combo = sonata_ui::controls::popup_button(&names, self.filter_index.get() as u32, move |i| {
                if let Some(c) = w.upgrade() {
                    c.filter_index.set(i as usize);
                    c.files.refilter();
                }
            });
            combo.set_valign(gtk::Align::Center);
            bar.append(&combo);
        }
        if self.mode != Mode::Open {
            let w = Rc::downgrade(&self.files);
            let new = sonata_ui::controls::push_button(&tr!("New Folder"), move || {
                if let Some(f) = w.upgrade() {
                    f.new_folder();
                }
            }, "");
            new.add_css_class("fc-cancel");
            new.set_valign(gtk::Align::Center);
            bar.append(&new);
        }
        bar.append(&gtk::Box::builder().hexpand(true).build());
        let w = Rc::downgrade(self);
        let cancel = sonata_ui::controls::push_button(&tr!("Cancel"), move || {
            if let Some(c) = w.upgrade() {
                c.finish(None);
            }
        }, "");
        cancel.add_css_class("fc-cancel");
        cancel.set_valign(gtk::Align::Center);
        bar.append(&cancel);
        let w = Rc::downgrade(self);
        self.accept_btn.connect_clicked(move |_| {
            if let Some(c) = w.upgrade() {
                c.accept(None);
            }
        });
        bar.append(&self.accept_btn);
        self.files.window.set_default_widget(Some(&self.accept_btn));
        content.append(&bar);
    }

    pub fn present(&self) {
        self.files.window.present();
    }

    pub fn window(&self) -> &adw::ApplicationWindow {
        &self.files.window
    }

    fn shows(&self, info: &gio::FileInfo) -> bool {
        if is_dir(info) {
            return true;
        }
        match self.mode {
            Mode::Folder => false,
            Mode::Open if !self.filters.is_empty() => {
                let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
                matches(&info.display_name(), &ct, &self.filters[self.filter_index.get()].1)
            }
            _ => true,
        }
    }

    /// Double-click: folders open, files are chosen (Open) or named (Save).
    fn open_item(self: &Rc<Self>, info: &gio::FileInfo) -> bool {
        if is_dir(info) {
            return false; // Files opens it
        }
        match (self.mode, &self.name_entry) {
            (Mode::Open, _) => {
                let (w, i) = (Rc::downgrade(self), info.clone());
                glib::idle_add_local_once(move || {
                    if let Some(c) = w.upgrade() {
                        c.accept(Some(vec![i]));
                    }
                });
            }
            (Mode::Save, Some(e)) => {
                e.set_text(&info.display_name());
                let w = Rc::downgrade(self);
                glib::idle_add_local_once(move || {
                    if let Some(c) = w.upgrade() {
                        c.accept(None);
                    }
                });
            }
            _ => {}
        }
        true
    }

    fn selected(&self) -> Vec<gio::FileInfo> {
        self.files.view().map(|v| v.selected()).unwrap_or_default()
    }

    fn update_accept(&self) {
        let here = !is_virtual(&self.files.location());
        let sel = self.selected();
        let ok = match self.mode {
            Mode::Save => here && self.name_entry.as_ref().is_some_and(|e| !e.text().trim().is_empty()),
            Mode::Folder => here || sel.iter().any(is_dir),
            Mode::Open => !sel.is_empty(),
        };
        self.accept_btn.set_sensitive(ok);
    }

    fn accept(self: &Rc<Self>, chosen: Option<Vec<gio::FileInfo>>) {
        let sel = chosen.unwrap_or_else(|| self.selected());
        let uri = |i: &gio::FileInfo| file_of(i).map(|f| f.uri().to_string());
        match self.mode {
            Mode::Open => {
                let files: Vec<&gio::FileInfo> = sel.iter().filter(|i| !is_dir(i)).collect();
                if files.is_empty() {
                    if sel.len() == 1 {
                        self.files.open_folder_or_file(&sel[0]); // a folder selected: go into it (macOS)
                    }
                    return;
                }
                let n = if self.multiple { files.len() } else { 1 };
                self.finish(Some(files.iter().take(n).filter_map(|i| uri(i)).collect()));
            }
            Mode::Folder => {
                let dirs: Vec<String> = sel.iter().filter(|i| is_dir(i)).filter_map(uri).collect();
                let n = if self.multiple { dirs.len() } else { 1 };
                let mut uris: Vec<String> = dirs.into_iter().take(n).collect();
                if uris.is_empty() {
                    uris.push(self.files.location());
                }
                self.finish(Some(uris));
            }
            Mode::Save => {
                let name = self.name_entry.as_ref().map(|e| e.text().trim().to_string()).unwrap_or_default();
                let here = self.files.location();
                if name.is_empty() || is_virtual(&here) {
                    return;
                }
                let Ok(dest) = gio::File::for_uri(&here).child_for_display_name(&name) else { return };
                if dest.query_exists(gio::Cancellable::NONE) {
                    let (w, u) = (Rc::downgrade(self), dest.uri().to_string());
                    sonata_ui::dialog::alert(
                        &fmt(&tr!("“{name}” already exists. Do you want to replace it?"), &[("name", &name)]),
                        &tr!("A file with the same name already exists in this folder. Replacing it will overwrite its current contents."),
                        &[("cancel", tr!("Cancel"), ""), ("replace", tr!("Replace"), "destructive")],
                        move |r| {
                            if r == "replace" {
                                if let Some(c) = w.upgrade() {
                                    c.finish(Some(vec![u.clone()]));
                                }
                            }
                        },
                        Some(self.files.window.upcast_ref()),
                    );
                    return;
                }
                self.finish(Some(vec![dest.uri().to_string()]));
            }
        }
    }

    /// Answer once, then close.
    pub fn finish(&self, uris: Option<Vec<String>>) {
        let Some(cb) = self.on_done.take() else { return };
        cb(uris, self.filter_index.get());
        self.files.window.close();
        let me: Weak<Chooser> = OPEN.with(|o| o.borrow().iter().find(|c| std::ptr::eq(c.as_ref(), self)).map(Rc::downgrade)).unwrap_or_default();
        glib::idle_add_local_once(move || {
            if let Some(c) = me.upgrade() {
                OPEN.with(|o| o.borrow_mut().retain(|x| !Rc::ptr_eq(x, &c)));
            }
        });
    }
}

/// Preview's Open / Save panels are these too (preview/window.py opens
/// ChooserWindow). Preview can't name this crate -- Files uses its picture
/// loading -- so the panel is handed to it when the program starts: a
/// constructor in .init_array, run before main wherever this crate is
/// linked (sonata2-rs), with nothing to call from the outside.
#[cfg(target_os = "linux")]
#[used]
#[link_section = ".init_array"]
static INSTALL_IN_PREVIEW: extern "C" fn() = {
    extern "C" fn install() {
        sonata_preview::panel::install(preview_panel);
    }
    install
};

/// A Preview panel: a sheet of its window (modal) when it has one.
pub fn preview_panel(app: &gtk::Application, parent: Option<&gtk::Window>, r: sonata_preview::panel::Request, done: sonata_preview::panel::Done) {
    let o = Options {
        mode: if r.save { Mode::Save } else { Mode::Open },
        title: r.title,
        accept_label: String::new(),
        multiple: r.multiple,
        filters: r.filters,
        current_filter: 0,
        folder: r.folder,
        name: r.name,
    };
    let ch = Chooser::new(app, o, Box::new(move |uris, _| done(uris)));
    if let Some(p) = parent {
        ch.window().set_transient_for(Some(p));
        ch.window().set_modal(true);
    }
    ch.present();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn portal_globs_match_like_fnmatch() {
        assert!(glob_match("*.txt", "Notes.TXT"));
        assert!(glob_match("*.[jJ][pP]g", "a.JPG"));
        assert!(glob_match("photo?.png", "photo1.png"));
        assert!(!glob_match("*.txt", "notes.md"));
        assert!(glob_match("*", "anything"));
        assert!(glob_match("[!a]*", "bcd"));
        assert!(!glob_match("[!a]*", "abc"));
        assert!(matches("x.md", "", &[(GLOB, "*.md".into())]));
        assert!(!matches("x.md", "", &[(MIME, "text/plain".into())]));
    }

    #[test]
    fn test_preview_used_gtks_file_dialog() {
        // Preview's Open, Save As and Export As opened GTK's file dialog
        // (through the portal, or GTK's own chooser without it), not Files'
        // panel with its sidebar, "Save As:" field and Format pop-up. Files'
        // chooser is handed to Preview before main wherever both are linked.
        assert!(sonata_preview::panel::installed());
    }
}
