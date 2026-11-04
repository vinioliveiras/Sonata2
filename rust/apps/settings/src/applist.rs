//! Every app in a Settings list (applist.py): a search field, one row per
//! app (icon, name, a subtitle, ›), built a little at a time so a long list
//! doesn't freeze the window. App Icons and Apps use it.

use crate::kit::{group, search_entry};
use adw::prelude::*;
use gtk::{gio, glib};
use sonata_i18n::tr;
use std::cell::RefCell;
use std::rc::{Rc, Weak};
use std::time::Duration;

/// rows built per step
pub const BATCH: usize = 24;
pub const ROW_ICON: i32 = 32;

/// [(desktop id, info)] of the apps a launcher lists, by name.
pub fn app_list() -> Vec<(String, gio::AppInfo)> {
    let mut out: Vec<(String, gio::AppInfo)> = sonata_ui::apps::scan()
        .into_iter()
        .filter(|(_, info)| info.should_show())
        .map(|(did, info)| (did.strip_suffix(".desktop").unwrap_or(&did).to_string(), info.upcast()))
        .collect();
    out.sort_by_key(|(_, info)| info.display_name().to_lowercase());
    out
}

/// One app's row.
#[derive(Clone)]
pub struct AppRow {
    pub row: adw::ActionRow,
    pub did: String,
    pub info: gio::AppInfo,
    pub image: gtk::Image,
}

pub type OnActivate = Rc<dyn Fn(&AppRow)>;
pub type Subtitle = Rc<dyn Fn(&AppRow) -> Option<String>>;

pub struct AppList {
    pub group: adw::PreferencesGroup,
    pub search: gtk::SearchEntry,
    /// in the list's order
    pub rows: RefCell<Vec<AppRow>>,
    on_activate: OnActivate,
    subtitle: Option<Subtitle>,
    pending: RefCell<Vec<(String, gio::AppInfo)>>,
    focus: RefCell<Option<String>>,
}

impl AppList {
    pub fn new(title: &str, description: &str, on_activate: OnActivate, subtitle: Option<Subtitle>) -> Rc<Self> {
        Self::with_apps(title, description, on_activate, subtitle, app_list())
    }

    /// The list of `apps` (tests give their own).
    pub fn with_apps(title: &str, description: &str, on_activate: OnActivate, subtitle: Option<Subtitle>, apps: Vec<(String, gio::AppInfo)>) -> Rc<Self> {
        let me = Rc::new(AppList {
            group: group(title, description),
            // the sidebar's search look (Vini), at the list's top right
            search: search_entry(Some(&tr!("Search apps")), true),
            rows: RefCell::new(Vec::new()),
            on_activate,
            subtitle,
            pending: RefCell::new(apps),
            focus: RefCell::new(None),
        });
        me.search.set_valign(gtk::Align::End);
        let w = Rc::downgrade(&me);
        me.search.connect_changed(move |e| {
            if let Some(l) = w.upgrade() {
                l.filter(&e.text());
            }
        });
        me.group.set_header_suffix(Some(&me.search));
        // between frames (an idle can wait behind redraws)
        let w: Weak<AppList> = Rc::downgrade(&me);
        glib::timeout_add_local(Duration::from_millis(16), move || match w.upgrade() {
            Some(l) if l.build_some() => glib::ControlFlow::Continue,
            _ => glib::ControlFlow::Break,
        });
        me
    }

    /// The next rows; true while more are to come.
    pub fn build_some(self: &Rc<Self>) -> bool {
        let batch: Vec<_> = {
            let mut p = self.pending.borrow_mut();
            let n = p.len().min(BATCH);
            p.drain(..n).collect()
        };
        for (did, info) in batch {
            self.add_row(did, info);
        }
        let text = self.search.text();
        if !text.is_empty() {
            self.filter(&text);
        }
        let focus = self.focus.borrow().clone();
        if let Some(did) = focus {
            if self.row(&did).is_some() {
                self.focus(&did);
            }
        }
        !self.pending.borrow().is_empty()
    }

    pub fn row(&self, did: &str) -> Option<AppRow> {
        self.rows.borrow().iter().find(|r| r.did == did).cloned()
    }

    /// Open one app (its row may still be on its way).
    pub fn focus(self: &Rc<Self>, did: &str) {
        let did = did.strip_suffix(".desktop").unwrap_or(did);
        let Some(row) = self.row(did) else {
            *self.focus.borrow_mut() = Some(did.to_string());
            return;
        };
        *self.focus.borrow_mut() = None;
        let act = self.on_activate.clone();
        glib::idle_add_local_once(move || {
            row.row.grab_focus();
            act(&row);
        });
    }

    fn add_row(self: &Rc<Self>, did: String, info: gio::AppInfo) {
        let row = adw::ActionRow::builder().title(info.display_name()).use_markup(false).activatable(true).build();
        let image = gtk::Image::builder().pixel_size(ROW_ICON).build();
        row.add_prefix(&image);
        row.add_suffix(&gtk::Image::builder().icon_name("go-next-symbolic").css_classes(["dim-label"]).build());
        let r = AppRow { row: row.clone(), did, info, image };
        let (act, rr) = (self.on_activate.clone(), r.clone());
        row.connect_activated(move |_| act(&rr));
        self.refresh_row(&r);
        self.rows.borrow_mut().push(r);
        self.group.add(&row);
    }

    /// Its icon and subtitle read again.
    pub fn refresh_row(&self, row: &AppRow) {
        sonata_ui::icons::set_image(&row.image, &sonata_ui::icons::app_icon(&row.info));
        if let Some(sub) = &self.subtitle {
            row.row.set_subtitle(&sub(row).unwrap_or_default());
        }
    }

    pub fn refresh(&self) {
        let rows = self.rows.borrow().clone();
        for r in &rows {
            self.refresh_row(r);
        }
    }

    pub fn filter(&self, text: &str) {
        let q = text.trim().to_lowercase();
        for r in self.rows.borrow().iter() {
            r.row.set_visible(q.is_empty() || r.row.title().to_lowercase().contains(&q) || r.did.to_lowercase().contains(&q));
        }
    }

    pub fn remove(&self, did: &str) {
        let mut rows = self.rows.borrow_mut();
        if let Some(i) = rows.iter().position(|r| r.did == did) {
            let r = rows.remove(i);
            self.group.remove(&r.row);
        }
    }
}
