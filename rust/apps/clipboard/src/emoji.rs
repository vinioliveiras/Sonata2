//! Character Viewer, compact (shell/emoji.py; macOS Big Sur
//! Ctrl+Cmd+Space): a small panel with a search field, the emoji of one
//! category (Frequently Used first) and the category tabs at the bottom.
//! Picking one types it into the window that had focus (pasted with
//! Ctrl+V, its real keycode: clip_picker::paste) and leaves it on the
//! clipboard. Lazy grid (GridView): only the visible cells exist.
//! Data: data/emoji.tsv (tools/gen-emoji.py), built into the binary.
//! "Frequently Used" is config emoji.json {"recent": [...]}, the Python
//! picker's own file.

use gtk::prelude::*;
use serde_json::{json, Map, Value};
use sonata_i18n::{n_, tr};
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;
use std::time::Duration;

const DATA: &str = include_str!("../../../../data/emoji.tsv");
pub const RECENT_MAX: usize = 16;
/// (group in emoji.tsv, tab icon, title) in macOS order; the title shown
/// translated
pub const CATEGORIES: [(&str, &str, &str); 9] = [
    ("recent", "document-open-recent-symbolic", n_!("Frequently Used")),
    ("Smileys & Emotion", "face-smile-symbolic", n_!("Smileys & People")),
    ("Animals & Nature", "emoji-nature-symbolic", n_!("Animals & Nature")),
    ("Food & Drink", "emoji-food-symbolic", n_!("Food & Drink")),
    ("Activities", "emoji-activities-symbolic", n_!("Activity")),
    ("Travel & Places", "emoji-travel-symbolic", n_!("Travel & Places")),
    ("Objects", "emoji-objects-symbolic", n_!("Objects")),
    ("Symbols", "emoji-symbols-symbolic", n_!("Symbols")),
    ("Flags", "emoji-flags-symbolic", n_!("Flags")),
];

/// macOS: "Smileys & People"
fn merge(group: &str) -> &str {
    if group == "People & Body" {
        "Smileys & Emotion"
    } else {
        group
    }
}

/// (group, emoji, name)
pub type Emoji = (&'static str, &'static str, &'static str);

/// Every emoji; a malformed line ends the list there (load()'s ValueError).
pub fn load() -> Vec<Emoji> {
    parse(DATA)
}

fn parse(data: &'static str) -> Vec<Emoji> {
    let mut out = Vec::new();
    for line in data.lines() {
        let parts: Vec<&'static str> = line.split('\t').collect();
        let [g, e, n] = parts[..] else { break };
        out.push((merge(g), e, n));
    }
    out
}

/// What the grid shows: (title, emoji). A search looks in every group
/// (each word in the name); else one group, "recent" being the user's.
pub fn content(all: &[Emoji], group: &str, query: &str, recent: &[String]) -> (String, Vec<String>) {
    let q = query.trim().to_lowercase();
    if !q.is_empty() {
        let words: Vec<&str> = q.split_whitespace().collect();
        let items: Vec<String> = all.iter().filter(|(_, _, n)| words.iter().all(|w| n.contains(w))).map(|(_, e, _)| e.to_string()).collect();
        let title = if items.is_empty() { tr!("No Results") } else { tr!("Search Results") };
        return (title, items);
    }
    if group == "recent" {
        let known: std::collections::HashSet<&str> = all.iter().map(|(_, e, _)| *e).collect();
        return (tr!("Frequently Used"), recent.iter().filter(|e| known.contains(e.as_str())).cloned().collect());
    }
    let title = CATEGORIES.iter().find(|c| c.0 == group).map(|c| sonata_i18n::gettext(c.2)).unwrap_or_default();
    (title, all.iter().filter(|(g, _, _)| *g == group).map(|(_, e, _)| e.to_string()).collect())
}

/// `e` first, the others after it, at most RECENT_MAX.
pub fn with_recent(recent: &[String], e: &str) -> Vec<String> {
    let mut out = vec![e.to_string()];
    out.extend(recent.iter().filter(|r| *r != e).cloned());
    out.truncate(RECENT_MAX);
    out
}

pub fn load_recent() -> Vec<String> {
    let cfg = sonata_config::load("emoji", &json!({"recent": []}));
    cfg.get("recent").and_then(Value::as_array).map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default()
}

pub fn save_recent(recent: &[String]) {
    let mut cfg = Map::new();
    cfg.insert("recent".into(), json!(recent));
    if let Err(e) = sonata_config::save("emoji", &cfg) {
        eprintln!("sonata2: emoji.json: {e}");
    }
}

pub struct EmojiPicker {
    pub win: gtk::Window,
    pub panel: gtk::Box,
    search: gtk::SearchEntry,
    title: gtk::Label,
    store: gtk::StringList,
    scroll: gtk::ScrolledWindow,
    tabs: Vec<(&'static str, gtk::ToggleButton)>,
    all: Vec<Emoji>,
    recent: RefCell<Vec<String>>,
    group: RefCell<&'static str>,
}

impl EmojiPicker {
    pub fn new(app: &gtk::Application) -> Rc<Self> {
        let win = gtk::Window::builder().application(app).title(tr!("Emoji & Symbols").as_str()).decorated(false).css_classes(["sonata-emoji"]).build();
        let all = load();
        let names: Rc<HashMap<&'static str, &'static str>> = Rc::new(all.iter().map(|(_, e, n)| (*e, *n)).collect());
        let recent = load_recent();
        let group = if recent.is_empty() { "Smileys & Emotion" } else { "recent" };

        let panel = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["emoji-panel"]).build();
        panel.set_size_request(340, 380);
        let search = gtk::SearchEntry::builder().placeholder_text(tr!("Search").as_str()).build();
        panel.append(&search);
        let title = gtk::Label::builder().xalign(0.0).css_classes(["emoji-title"]).build();
        panel.append(&title);

        let store = gtk::StringList::new(&[]);
        let fac = gtk::SignalListItemFactory::new();
        fac.connect_setup(|_, item| {
            if let Some(item) = item.downcast_ref::<gtk::ListItem>() {
                item.set_child(Some(&gtk::Label::builder().css_classes(["emoji-cell"]).build()));
            }
        });
        fac.connect_bind(move |_, item| {
            let Some(item) = item.downcast_ref::<gtk::ListItem>() else { return };
            let (Some(s), Some(lbl)) = (item.item().and_downcast::<gtk::StringObject>(), item.child().and_downcast::<gtk::Label>()) else { return };
            let e = s.string();
            lbl.set_label(&e);
            lbl.set_tooltip_text(Some(names.get(e.as_str()).copied().unwrap_or("")));
        });
        let sel = gtk::SingleSelection::builder().model(&store).autoselect(false).build();
        let grid = gtk::GridView::builder().model(&sel).factory(&fac).max_columns(9).min_columns(9).single_click_activate(true).build();
        let scroll = gtk::ScrolledWindow::builder().child(&grid).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build();
        panel.append(&scroll);

        let tab_box = gtk::Box::builder().css_classes(["emoji-tabs"]).homogeneous(true).build();
        let mut tabs = Vec::new();
        let mut first: Option<gtk::ToggleButton> = None;
        for (key, icon, t) in CATEGORIES {
            let b = gtk::ToggleButton::builder().icon_name(icon).tooltip_text(sonata_i18n::gettext(t).as_str()).can_focus(false).build();
            b.set_group(first.as_ref());
            first.get_or_insert_with(|| b.clone());
            tab_box.append(&b);
            tabs.push((key, b));
        }
        panel.append(&tab_box);
        win.set_child(Some(&panel));

        let me = Rc::new(Self { win, panel, search, title, store, scroll, tabs, all, recent: RefCell::new(recent), group: RefCell::new(group) });
        crate::clip_picker::glass_class(&me.panel);
        for (key, b) in &me.tabs {
            let (weak, key) = (Rc::downgrade(&me), *key);
            b.connect_toggled(move |b| {
                if let Some(me) = weak.upgrade().filter(|_| b.is_active()) {
                    me.set_group(key);
                }
            });
        }
        let weak = Rc::downgrade(&me);
        grid.connect_activate(move |_, pos| {
            if let Some(me) = weak.upgrade() {
                let e = me.store.string(pos);
                me.pick(e.as_deref().unwrap_or(""));
            }
        });
        let weak = Rc::downgrade(&me);
        me.search.connect_search_changed(move |_| {
            if let Some(me) = weak.upgrade() {
                me.refill();
            }
        });
        let weak = Rc::downgrade(&me);
        me.search.connect_activate(move |_| {
            if let Some(me) = weak.upgrade() {
                me.pick_first();
            }
        });

        // Esc closes it, also from the search field (which kept Esc for
        // itself: Vini)
        let keys = gtk::EventControllerKey::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
        let w = me.win.downgrade();
        keys.connect_key_pressed(move |_, keyval, _, _| {
            if keyval != gtk::gdk::Key::Escape {
                return glib::Propagation::Proceed;
            }
            if let Some(w) = w.upgrade() {
                w.set_visible(false);
            }
            glib::Propagation::Stop
        });
        me.win.add_controller(keys);
        let w = me.win.downgrade();
        me.search.connect_stop_search(move |_| {
            if let Some(w) = w.upgrade() {
                w.set_visible(false);
            }
        });
        // in the middle of the screen, a little lower; a click outside
        // closes it (Vini)
        let w = me.win.downgrade();
        let close = move || {
            if let Some(w) = w.upgrade() {
                w.set_visible(false);
            }
        };
        if !crate::picker::centered_picker(&me.win, me.panel.upcast_ref(), "sonata2-emoji", close) {
            me.win.set_resizable(false); // a plain window: the panel's size
        }
        if let Some((_, b)) = me.tabs.iter().find(|(k, _)| *k == group) {
            b.set_active(true);
        }
        me.refill();
        me
    }

    // -- data -----------------------------------------------------------------------------------
    fn set_group(&self, key: &'static str) {
        self.group.replace(key);
        if self.search.text().is_empty() {
            self.refill();
        } else {
            self.search.set_text(""); // search-changed refills
        }
    }

    fn refill(&self) {
        let (title, items) = content(&self.all, *self.group.borrow(), &self.search.text(), &self.recent.borrow());
        self.title.set_label(&title);
        let items: Vec<&str> = items.iter().map(String::as_str).collect();
        self.store.splice(0, self.store.n_items(), &items);
        self.scroll.vadjustment().set_value(0.0);
    }

    // -- picking --------------------------------------------------------------------------------
    fn pick_first(&self) {
        if let Some(e) = self.store.string(0) {
            self.pick(&e);
        }
    }

    fn pick(&self, e: &str) {
        if e.is_empty() {
            return;
        }
        let recent = with_recent(&self.recent.borrow(), e);
        save_recent(&recent);
        self.recent.replace(recent);
        WidgetExt::display(&self.win).clipboard().set_text(e);
        self.win.set_visible(false);
        // pasted (Ctrl+V; Ctrl+Shift+V in a terminal) once the keyboard is
        // back on the app, never typed as keys: Chromium and Electron apps
        // (WhatsApp) turned typed emoji into other characters (Vini)
        glib::timeout_add_local_once(Duration::from_millis(180), crate::clip_picker::paste);
    }

    pub fn open(&self) {
        self.recent.replace(load_recent());
        self.search.set_text("");
        self.refill();
        self.win.present();
        self.search.grab_focus();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn data_loads_with_people_merged_into_smileys() {
        let all = load();
        assert!(all.len() > 1000, "{}", all.len());
        assert!(all.iter().all(|(g, _, _)| *g != "People & Body"));
        let groups: std::collections::HashSet<&str> = all.iter().map(|e| e.0).collect();
        for (key, _, _) in &CATEGORIES[1..] {
            assert!(groups.contains(key), "{key}");
        }
        // a malformed line ends the list, as load()'s ValueError did
        assert_eq!(parse("A\t😀\tgrinning face\nbroken\nA\t😃\tx\n").len(), 1);
    }

    #[test]
    fn search_every_word_in_the_name() {
        let _e = crate::testenv::env();
        let all = parse("A\t😀\tgrinning face\nB\t😺\tgrinning cat\nB\t🐶\tdog face\n");
        assert_eq!(content(&all, "B", " Grinning  FACE ", &[]), ("Search Results".to_string(), vec!["😀".to_string()]));
        assert_eq!(content(&all, "B", "zebra", &[]).0, "No Results");
        assert_eq!(content(&all, "B", "", &[]).1, ["😺", "🐶"]);
        assert_eq!(content(&all, "Flags", "", &[]).0, "Flags");
        // Frequently Used keeps only emoji this data still has
        let recent = vec!["🐶".to_string(), "gone".to_string()];
        assert_eq!(content(&all, "recent", "", &recent), ("Frequently Used".to_string(), vec!["🐶".to_string()]));
    }

    #[test]
    fn recent_first_no_repeats_at_most_sixteen() {
        let r: Vec<String> = (0..RECENT_MAX).map(|i| i.to_string()).collect();
        let got = with_recent(&r, "5");
        assert_eq!((got[0].as_str(), got[1].as_str(), got.len()), ("5", "0", RECENT_MAX - 1 + 1));
        let got = with_recent(&r, "new");
        assert_eq!((got[0].as_str(), got.len(), got.last().unwrap().as_str()), ("new", RECENT_MAX, "14"));
    }

    #[test]
    fn recent_shared_with_the_python_picker() {
        // emoji.json is the Python picker's file: read from sonata2/ on
        // trial, written as config.save writes it
        let _e = crate::testenv::env();
        let py = sonata_config::config_home().join("sonata2");
        std::fs::create_dir_all(&py).unwrap();
        std::fs::write(py.join("emoji.json"), "{\"recent\": [\"\\ud83d\\ude00\", \"\\u2764\\ufe0f\"]}").unwrap();
        assert_eq!(load_recent(), ["😀", "❤️"]);
        save_recent(&with_recent(&load_recent(), "🐶"));
        assert_eq!(load_recent(), ["🐶", "😀", "❤️"]);
        let written = std::fs::read_to_string(sonata_config::path("emoji")).unwrap();
        assert!(written.contains("\"\\ud83d\\udc36\""), "{written}"); // json.dump's ASCII escapes
    }
}
