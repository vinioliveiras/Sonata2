//! The rows of Task Manager's tables (activity/pages.py's GObject rows):
//! one GObject type for processes, apps, users, history, startup items and
//! services. Their values sit in `sv` (plain values, for fast sorting);
//! set_values() emits "changed" only when one really changed, and the
//! cells bound to the row redraw then. Rows are updated in place, so
//! selections and scroll positions survive refreshes.

use crate::manage::{Service, StartupEntry};
use gtk::glib;
use gtk::glib::subclass::Signal;
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::OnceLock;

/// A cell's value.
#[derive(Clone, Debug, PartialEq)]
pub enum V {
    N(f64),
    S(String),
}

impl V {
    pub fn n(&self) -> f64 {
        match self {
            V::N(n) => *n,
            V::S(_) => 0.0,
        }
    }

    pub fn s(&self) -> String {
        match self {
            V::S(s) => s.clone(),
            V::N(n) => n.to_string(),
        }
    }

    /// Strings without case, numbers as numbers.
    pub fn cmp(&self, other: &V) -> std::cmp::Ordering {
        match (self, other) {
            (V::N(a), V::N(b)) => a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal),
            (a, b) => a.s().to_lowercase().cmp(&b.s().to_lowercase()),
        }
    }
}

impl From<f64> for V {
    fn from(n: f64) -> Self {
        V::N(n)
    }
}

impl From<i64> for V {
    fn from(n: i64) -> Self {
        V::N(n as f64)
    }
}

impl From<u64> for V {
    fn from(n: u64) -> Self {
        V::N(n as f64)
    }
}

impl From<&str> for V {
    fn from(s: &str) -> Self {
        V::S(s.into())
    }
}

impl From<String> for V {
    fn from(s: String) -> Self {
        V::S(s)
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub enum Kind {
    #[default]
    Process,
    App,
    User,
    History,
    Startup,
    Service,
}

/// What a row knows besides its cells.
#[derive(Default)]
pub struct Data {
    pub kind: Kind,
    pub pid: i64,
    pub ppid: i64,
    pub uid: i64,
    /// (pid, start ticks): a process's identity
    pub key: (i64, i64),
    pub comm: String,
    pub exe: String,
    pub cmdline: String,
    pub started: f64,
    /// the app a process belongs to; an app node's own key
    pub app_key: Option<String>,
    pub app_name: String,
    /// an app node's processes
    pub members: Vec<TaskRow>,
    pub children: Option<gio::ListStore>,
    pub entry: Option<StartupEntry>,
    pub service: Option<Service>,
    pub enabled: bool,
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct TaskRow {
        pub sv: RefCell<HashMap<String, V>>,
        pub icon: RefCell<Option<gio::Icon>>,
        pub data: RefCell<Data>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for TaskRow {
        const NAME: &'static str = "SonataRsTaskRow";
        type Type = super::TaskRow;
    }

    impl ObjectImpl for TaskRow {
        fn signals() -> &'static [Signal] {
            static S: OnceLock<Vec<Signal>> = OnceLock::new();
            S.get_or_init(|| vec![Signal::builder("changed").build()])
        }
    }
}

glib::wrapper! {
    pub struct TaskRow(ObjectSubclass<imp::TaskRow>);
}

impl TaskRow {
    pub fn new(kind: Kind, name: &str, icon: Option<gio::Icon>) -> Self {
        let r: Self = glib::Object::new();
        r.imp().data.borrow_mut().kind = kind;
        r.imp().icon.replace(icon);
        r.imp().sv.borrow_mut().insert("name".into(), V::S(name.into()));
        r.imp().sv.borrow_mut().insert("gpu_on".into(), V::S(String::new()));
        r
    }

    pub fn get(&self, k: &str) -> V {
        self.imp().sv.borrow().get(k).cloned().unwrap_or(V::N(0.0))
    }

    pub fn n(&self, k: &str) -> f64 {
        self.get(k).n()
    }

    pub fn s(&self, k: &str) -> String {
        match self.imp().sv.borrow().get(k) {
            Some(V::S(s)) => s.clone(),
            Some(V::N(n)) => n.to_string(),
            None => String::new(),
        }
    }

    pub fn name(&self) -> String {
        self.s("name")
    }

    pub fn icon(&self) -> Option<gio::Icon> {
        self.imp().icon.borrow().clone()
    }

    pub fn data(&self) -> std::cell::Ref<'_, Data> {
        self.imp().data.borrow()
    }

    pub fn data_mut(&self) -> std::cell::RefMut<'_, Data> {
        self.imp().data.borrow_mut()
    }

    pub fn kind(&self) -> Kind {
        self.imp().data.borrow().kind
    }

    pub fn pid(&self) -> i64 {
        let d = self.imp().data.borrow();
        match d.kind {
            Kind::App => d.members.first().map(|m| m.pid()).unwrap_or(0),
            _ => d.pid,
        }
    }

    /// Set the values; "changed" (one redraw of its cells) only when one
    /// differs.
    pub fn set_values(&self, vals: Vec<(&str, V)>) {
        let mut changed = false;
        {
            let mut sv = self.imp().sv.borrow_mut();
            for (k, v) in vals {
                if sv.get(k) != Some(&v) {
                    sv.insert(k.to_string(), v);
                    changed = true;
                }
            }
        }
        if changed {
            self.emit_by_name::<()>("changed", &[]);
        }
    }

    pub fn connect_changed<F: Fn(&Self) + 'static>(&self, f: F) -> glib::SignalHandlerId {
        self.connect_local("changed", false, move |args| {
            let row = args[0].get::<TaskRow>().ok()?;
            f(&row);
            None
        })
    }

    /// A row from a process (the shared rows of Processes and Details).
    pub fn process(p: &sonata_backend::procfs::Proc, icon: Option<gio::Icon>, name: &str, app_key: Option<String>) -> Self {
        let r = Self::new(Kind::Process, name, icon);
        let cmd = p.cmdline.replace('\n', " ");
        {
            let mut d = r.data_mut();
            d.pid = p.pid;
            d.key = p.key();
            d.uid = p.uid;
            d.comm = p.comm.clone();
            d.exe = p.exe.clone();
            d.cmdline = p.cmdline.clone();
            d.started = p.started;
            d.app_key = app_key;
        }
        r.set_values(vec![("pid", p.pid.into()), ("user", p.user.as_str().into()), ("command", cmd.into())]);
        r.update(p);
        r
    }

    pub fn update(&self, p: &sonata_backend::procfs::Proc) {
        self.data_mut().ppid = p.ppid;
        let r1 = |x: f64| (x * 10.0).round_ties_even() / 10.0;
        self.set_values(vec![
            ("cpu", r1(p.cpu).into()),
            ("cpu_time", ((p.cpu_time * 100.0).round_ties_even() / 100.0).into()),
            ("threads", p.threads.into()),
            ("mem", p.rss.into()),
            ("read_bytes", p.read_bytes.into()),
            ("write_bytes", p.write_bytes.into()),
            ("disk", (p.read_ps + p.write_ps).round_ties_even().into()),
            ("gpu", r1(p.gpu).into()),
            ("status", p.status().into()),
            ("gpu_on", p.gpu_on.as_str().into()),
        ]);
    }

    /// An app on the Processes page: its processes' totals; expands to them.
    pub fn app(key: &str, name: &str, icon: Option<gio::Icon>) -> Self {
        let r = Self::new(Kind::App, name, icon);
        {
            let mut d = r.data_mut();
            d.app_key = Some(key.into());
            d.app_name = name.into();
            d.children = Some(gio::ListStore::new::<TaskRow>());
        }
        r.set_values(vec![("group", 0i64.into())]);
        r
    }
}

/// Make `store` hold exactly `wanted`, moving nothing that stays: gone
/// ones are removed, new ones appended.
pub fn sync_store(store: &gio::ListStore, wanted: &[TaskRow]) {
    let want: std::collections::HashSet<TaskRow> = wanted.iter().cloned().collect();
    let mut have = std::collections::HashSet::new();
    for i in (0..store.n_items()).rev() {
        let Some(it) = store.item(i).and_downcast::<TaskRow>() else { continue };
        if want.contains(&it) {
            have.insert(it);
        } else {
            store.remove(i);
        }
    }
    let new: Vec<TaskRow> = wanted.iter().filter(|w| !have.contains(*w)).cloned().collect();
    if !new.is_empty() {
        store.splice(store.n_items(), 0, &new);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The Processes list keeps surviving rows (no reorder flicker): gone
    /// ones go, new ones are appended.
    #[test]
    fn sync_store_keeps_rows_in_place() {
        let rows: Vec<TaskRow> = (0..4).map(|i| TaskRow::new(Kind::Process, &i.to_string(), None)).collect();
        let store = gio::ListStore::new::<TaskRow>();
        store.splice(0, 0, &rows[..3]);
        sync_store(&store, &[rows[2].clone(), rows[3].clone(), rows[0].clone()]);
        let got: Vec<TaskRow> = (0..store.n_items()).filter_map(|i| store.item(i).and_downcast::<TaskRow>()).collect();
        assert_eq!(got, [rows[0].clone(), rows[2].clone(), rows[3].clone()]);
    }
}
