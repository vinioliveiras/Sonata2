//! The Sound menu's per-app volumes (shell/mixer_ui.py): one row per app
//! playing sound -- its icon (click: mute), its name, a slider, and the
//! output it plays on (the Sound module's output button, for that app
//! alone). Rows come and go with a short slide (gtk::Revealer, no relayout
//! of the others' content); a slider's level is applied and saved a moment
//! after it stops moving.
//!
//! The streams and what is done with them (backend/mixer.py: pactl, the
//! saved levels, the output picker) aren't ported yet: AppMixer takes them
//! as closures (`apply`, `pick`, and `shown` for an app's name and icon),
//! which the menu bar passes in.

use crate::controls::{self, ModuleSlider};
use gtk::prelude::*;
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};
use std::time::Duration;

pub const SLIDE_MS: u32 = 160;
/// a moving slider: one pactl call per pause, not per step
pub const APPLY_MS: u64 = 120;
/// the Sound module's output button's
pub const OUTPUT_ICON: &str = "sonata-audio-output-symbolic";

// backend/mixer.py's Stream and group(): one definition, sonata-system's
pub use sonata_system::mixer::{group, Stream};


/// apply(key, percent, muted, every stream): set an app's level / mute.
pub type ApplyFn = Rc<dyn Fn(&str, Option<i32>, Option<bool>, &[Stream])>;
/// pick(output button, key, every stream): the output picker for an app.
pub type PickFn = Rc<dyn Fn(&gtk::Button, &str, &[Stream])>;
/// shown(stream): the app's name and icon.
pub type ShownFn = Rc<dyn Fn(&Stream) -> (String, gio::Icon)>;

/// The app's name and icon (mixer_ui.shown): its desktop entry by the
/// stream's names (matched as the Dock matches windows); else by the
/// process playing it -- a Steam game (as in the Dock and Alt+Tab) or the
/// app whose window that process (or its parent) has; else the stream's
/// own. Vini: Control Center's mixer showed no app icons -- the names were
/// only looked up as they were.
pub fn shown(stream: &Stream) -> (String, gio::Icon) {
    let entry = |name: &str| (!name.is_empty()).then(|| sonata_desktop::appmatch::match_app_id(name)).flatten().and_then(|did| sonata_ui::apps::lookup(&did)).map(|i| i.upcast::<gio::AppInfo>());
    shown_with(stream, &entry, &|pid| sonata_system::mixer::whose(pid, None, "/proc").map(|(k, id)| (k.to_string(), id)), &sonata_desktop::steamgames::shown)
}

/// shown, with the desktop entry match, whose process it is and the Steam
/// game's look given (tests).
pub fn shown_with(stream: &Stream, entry: &dyn Fn(&str) -> Option<gio::AppInfo>, whose: &dyn Fn(i64) -> Option<(String, String)>, game: &dyn Fn(&str, &str) -> Option<(String, gio::Icon)>) -> (String, gio::Icon) {
    if let Some(info) = entry(&stream.key).or_else(|| entry(&stream.icon)) {
        return (stream.name.clone(), sonata_ui::icons::app_icon(&info));
    }
    match whose(stream.pid) {
        Some((kind, id)) if kind == "steam" => {
            if let Some(g) = game(&format!("steam_app_{id}"), &stream.name) {
                return g;
            }
        }
        Some((_, app_id)) => {
            if let Some(info) = entry(&app_id) {
                return (sonata_ui::apps::display_name(&info), sonata_ui::icons::app_icon(&info));
            }
            if let Some(g) = game(&app_id, &stream.name) {
                return g;
            }
        }
        None => {}
    }
    let names: Vec<&str> = [stream.icon.as_str(), "audio-x-generic"].into_iter().filter(|n| !n.is_empty()).collect();
    (stream.name.clone(), gio::ThemedIcon::from_names(&names).upcast())
}

/// What a slider stopped at sends: the level, and an unmute when the app
/// was muted (moving the level unmutes, macOS).
pub fn level_change(value: f64, muted: bool) -> (Option<i32>, Option<bool>) {
    (Some(value.round() as i32), if muted { Some(false) } else { None })
}

/// PipeWire's level for a row, to show unless the slider is being moved
/// or is already there (a change it reports is never sent back).
pub fn outside_level(slider: f64, volume: i32, moving: bool) -> Option<f64> {
    (!moving && (slider - volume as f64).abs() >= 1.0).then_some(volume as f64)
}

/// (keys gone, streams new) between the rows shown and the apps playing.
pub fn diff<'a>(rows: &[String], shown: &'a [Stream]) -> (Vec<String>, Vec<&'a Stream>) {
    let gone = rows.iter().filter(|k| !shown.iter().any(|s| &s.key == *k)).cloned().collect();
    let new = shown.iter().filter(|s| !rows.contains(&s.key)).collect();
    (gone, new)
}

pub struct MixerRow {
    pub revealer: gtk::Revealer,
    pub key: String,
    pub root: gtk::Box,
    pub mute: gtk::Button,
    pub image: gtk::Image,
    pub label: gtk::Label,
    pub output: gtk::Button,
    pub slider: ModuleSlider,
    pub muted: Cell<bool>,
    name: RefCell<String>,
    src: RefCell<Option<glib::SourceId>>,
    owner: Weak<Inner>,
}

impl MixerRow {
    fn new(owner: &Rc<Inner>, stream: &Stream) -> Rc<Self> {
        let revealer = gtk::Revealer::builder().transition_type(gtk::RevealerTransitionType::SlideDown).transition_duration(SLIDE_MS).build();
        let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(5).css_classes(["mixer-row"]).build();
        let title = gtk::Box::builder().spacing(6).css_classes(["mixer-title"]).build();
        let mute = gtk::Button::builder().css_classes(["mixer-mute"]).valign(gtk::Align::Center).can_focus(false).tooltip_text(tr!("Mute")).build();
        let (name, icon) = (owner.shown)(stream);
        let image = gtk::Image::builder().pixel_size(16).gicon(&icon).build();
        mute.set_child(Some(&image));
        title.append(&mute);
        let label = gtk::Label::builder().label(&name).xalign(0.0).hexpand(true).ellipsize(gtk::pango::EllipsizeMode::End).width_chars(1).build();
        title.append(&label);
        root.append(&title);
        let key = stream.key.clone();
        let weak_owner = Rc::downgrade(owner);
        let k = key.clone();
        let output = controls::round_button(OUTPUT_ICON, &tr!("Output"), move |b| {
            if let Some(o) = weak_owner.upgrade() {
                o.pick_output(b, &k);
            }
        });
        let moved: Rc<RefCell<Option<Weak<MixerRow>>>> = Rc::default();
        let m = moved.clone();
        let (capsule, slider) = controls::slider_with_icon(
            |v| controls::speaker_icon(v).to_string(),
            stream.volume as f64,
            move |_| {
                if let Some(row) = m.borrow().as_ref().and_then(Weak::upgrade) {
                    row.moved();
                }
            },
            true,
            Some(&output),
        );
        root.append(&capsule);
        revealer.set_child(Some(&root));
        let row = Rc::new(Self { revealer, key, root, mute, image, label, output, slider, muted: Cell::new(false), name: RefCell::new(stream.name.clone()), src: RefCell::default(), owner: Rc::downgrade(owner) });
        *moved.borrow_mut() = Some(Rc::downgrade(&row));
        let w = Rc::downgrade(&row);
        row.mute.connect_clicked(move |_| {
            if let Some(r) = w.upgrade() {
                r.toggle_mute();
            }
        });
        row.update(stream);
        row
    }

    /// New values from PipeWire (not while the slider is being moved).
    pub fn update(&self, stream: &Stream) {
        if *self.name.borrow() != stream.name {
            if let Some(o) = self.owner.upgrade() {
                let (name, icon) = (o.shown)(stream);
                self.label.set_label(&name);
                self.image.set_from_gicon(&icon);
            }
            self.name.replace(stream.name.clone());
        }
        if let Some(v) = outside_level(self.slider.value(), stream.volume, self.src.borrow().is_some()) {
            self.slider.show_value(v);
        }
        self.set_muted(stream.muted);
    }

    fn set_muted(&self, on: bool) {
        self.muted.set(on);
        if on {
            self.root.add_css_class("muted");
        } else {
            self.root.remove_css_class("muted");
        }
        self.mute.set_tooltip_text(Some(&if on { tr!("Unmute") } else { tr!("Mute") }));
    }

    pub fn toggle_mute(&self) {
        let on = !self.muted.get();
        self.set_muted(on);
        if let Some(o) = self.owner.upgrade() {
            o.apply(&self.key, None, Some(on));
        }
    }

    fn moved(self: &Rc<Self>) {
        if let Some(s) = self.src.take() {
            s.remove();
        }
        let weak = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(APPLY_MS), move || {
            let Some(me) = weak.upgrade() else { return };
            me.src.take();
            let (percent, muted) = level_change(me.slider.value(), me.muted.get());
            if let Some(o) = me.owner.upgrade() {
                o.apply(&me.key, percent, muted);
            }
            if me.muted.get() {
                me.set_muted(false); // moving the level unmutes (macOS)
            }
        });
        self.src.replace(Some(id));
    }
}

struct Inner {
    root: gtk::Box,
    empty: gtk::Label,
    rows: RefCell<Vec<Rc<MixerRow>>>,
    streams: RefCell<Vec<Stream>>,
    apply_fn: ApplyFn,
    pick_fn: PickFn,
    shown: ShownFn,
}

impl Inner {
    fn apply(&self, key: &str, percent: Option<i32>, muted: Option<bool>) {
        let streams = self.streams.borrow().clone();
        (self.apply_fn)(key, percent, muted, &streams);
    }

    fn pick_output(&self, button: &gtk::Button, key: &str) {
        let streams = self.streams.borrow().clone();
        (self.pick_fn)(button, key, &streams);
    }
}

/// The list; set_streams() with what's playing now (diffed: rows that stay
/// keep their state, new ones slide in, gone ones slide out). Cheap to
/// clone: one list.
#[derive(Clone)]
pub struct AppMixer(Rc<Inner>);

impl AppMixer {
    /// The output button sits at each slider's right, in Control Center
    /// and the Sound menu alike (Vini). `shown`: None for [`shown`].
    pub fn new(apply: ApplyFn, pick: PickFn, shown: Option<ShownFn>) -> Self {
        sonata_ui::css::register_module("shell/mixer_ui.py");
        let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let empty = gtk::Label::builder().label(tr!("No apps playing sound")).xalign(0.0).css_classes(["mixer-empty"]).build();
        root.append(&empty);
        Self(Rc::new(Inner { root, empty, rows: RefCell::default(), streams: RefCell::default(), apply_fn: apply, pick_fn: pick, shown: shown.unwrap_or_else(|| Rc::new(self::shown)) }))
    }

    pub fn widget(&self) -> &gtk::Box {
        &self.0.root
    }

    /// The row of an app, by its key (tests, the menu bar).
    pub fn row(&self, key: &str) -> Option<Rc<MixerRow>> {
        self.0.rows.borrow().iter().find(|r| r.key == key).cloned()
    }

    /// The list's height with every app playing now fully shown: its rows'
    /// own heights (a row still sliding in counts whole, one sliding out
    /// not at all), or the "No apps playing sound" line. Control Center's
    /// module takes as many grid rows as this needs -- measured as it was
    /// drawn, a new app's row (not revealed yet) counted nothing and the
    /// module grew only at the next change (Vini: an app cut off).
    pub fn content_height(&self) -> i32 {
        let rows = self.0.rows.borrow();
        if rows.is_empty() {
            return self.0.empty.measure(gtk::Orientation::Vertical, -1).1;
        }
        rows.iter().map(|r| r.root.measure(gtk::Orientation::Vertical, -1).1).sum()
    }

    pub fn keys(&self) -> Vec<String> {
        self.0.rows.borrow().iter().map(|r| r.key.clone()).collect()
    }

    pub fn set_streams(&self, streams: &[Stream]) {
        *self.0.streams.borrow_mut() = streams.to_vec();
        let shown = group(streams);
        let (gone, _) = diff(&self.keys(), &shown);
        for key in gone {
            self.drop_row(&key);
        }
        for s in &shown {
            if let Some(row) = self.row(&s.key) {
                row.update(s);
                continue;
            }
            let row = MixerRow::new(&self.0, s);
            self.0.root.append(&row.revealer);
            self.0.rows.borrow_mut().push(row.clone());
            let r = row.revealer.clone();
            glib::idle_add_local_once(move || r.set_reveal_child(true)); // slides in
        }
        self.0.empty.set_visible(shown.is_empty());
    }

    fn drop_row(&self, key: &str) {
        let row = {
            let mut rows = self.0.rows.borrow_mut();
            let Some(i) = rows.iter().position(|r| r.key == key) else { return };
            rows.remove(i)
        };
        let rev = row.revealer.clone();
        rev.set_reveal_child(false);
        let root = self.0.root.clone();
        rev.connect_child_revealed_notify(move |r| {
            if !r.is_child_revealed() && r.parent().as_ref() == Some(root.upcast_ref()) {
                root.remove(r);
            }
        });
        if !rev.is_mapped() {
            self.0.root.remove(&rev);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stream(index: i64, key: &str, volume: i32, muted: bool) -> Stream {
        Stream { index, key: key.into(), name: key.into(), icon: String::new(), volume, muted, corked: false, pid: 0, sink: -1 }
    }

    /// test_mixer.py's PACTL: Firefox's two tabs, then Spotify (muted).
    fn pactl() -> Vec<Stream> {
        vec![stream(1, "firefox", 40, false), stream(2, "firefox", 70, false), stream(3, "com.spotify.client", 55, true)]
    }

    #[test]
    fn one_row_per_app() {
        let g = group(&pactl());
        assert_eq!(g.iter().map(|s| s.key.as_str()).collect::<Vec<_>>(), ["firefox", "com.spotify.client"]);
        assert_eq!(g[0].index, 1); // the first stream stands for the app
    }

    #[test]
    fn test_rows_follow_the_streams() {
        let ss = pactl();
        let (all, rest) = (group(&ss), group(&ss[2..]));
        let (gone, new) = diff(&[], &all);
        assert!(gone.is_empty());
        assert_eq!(new.len(), 2);
        let rows: Vec<String> = ["firefox".into(), "com.spotify.client".into()].into();
        let (gone, new) = diff(&rows, &rest); // Firefox stopped
        assert_eq!(gone, ["firefox"]);
        assert!(new.is_empty());
        let (gone, new) = diff(&rows[1..], &rest); // same again: the row stays
        assert!(gone.is_empty() && new.is_empty());
    }

    #[test]
    fn test_outside_updates_dont_echo() {
        assert_eq!(outside_level(40.0, 90, false), Some(90.0)); // shown, never sent back (show_value)
        assert_eq!(outside_level(40.0, 90, true), None); // not while it is being moved
        assert_eq!(outside_level(40.4, 40, false), None);
    }

    #[test]
    fn test_mute_and_unmute_by_moving() {
        assert_eq!(level_change(60.0, false), (Some(60), None));
        assert_eq!(level_change(29.6, true), (Some(30), Some(false))); // moving unmutes
    }

    /// Vini: Control Center's mixer showed no app icons. A stream says
    /// "spotify" while the entry is spotify-launcher, or only its process
    /// is the app's: matched, and asked whose it is, as mixer_ui.shown.
    #[test]
    fn test_mixer_showed_no_app_icons() {
        let _env = crate::testenv::env();
        let app = |id: &str| {
            let kf = glib::KeyFile::new();
            kf.load_from_data(&format!("[Desktop Entry]\nType=Application\nName={id}\nExec=/bin/sh\nIcon={id}\n"), glib::KeyFileFlags::NONE).unwrap();
            gio::DesktopAppInfo::from_keyfile(&kf).unwrap().upcast::<gio::AppInfo>()
        };
        let entry = |name: &str| (name == "spotify" || name == "org.app.Player").then(|| app(if name == "spotify" { "Spotify" } else { "Player" }));
        let no_game = |_: &str, _: &str| None;
        let mut s = stream(1, "spotify", 10, false);
        s.name = "Spotify".into();
        let (_, icon) = shown_with(&s, &entry, &|_| None, &no_game);
        assert_ne!(icon.to_string().map(|s| s.to_string()).as_deref(), Some("audio-x-generic"));
        // a stream named after its library: the window's app of its process
        let mut s = stream(2, "ALSA plug-in [player]", 10, false);
        s.pid = 42;
        let (name, _) = shown_with(&s, &entry, &|pid| (pid == 42).then(|| ("app".into(), "org.app.Player".into())), &no_game);
        assert_eq!(name, "Player");
    }

    #[test]
    fn the_name_and_icon_without_an_entry() {
        let _env = crate::testenv::env();
        let mut s = stream(1, "nothing-installed-here", 10, false);
        s.name = "WINE".into();
        let (name, icon) = shown(&s);
        assert_eq!(name, "WINE");
        assert_eq!(icon.to_string().map(|s| s.to_string()).as_deref(), Some("audio-x-generic"));
    }
}
