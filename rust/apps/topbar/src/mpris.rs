//! Now Playing: media players over MPRIS (D-Bus), for the Control Center
//! module and the menu bar item (shell/mpris.py). Event-driven
//! (NameOwnerChanged and PropertiesChanged signals): no polling.

use gio::prelude::*;
use glib::Variant;
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

pub const PREFIX: &str = "org.mpris.MediaPlayer2.";
pub const PATH: &str = "/org/mpris/MediaPlayer2";
pub const IFACE: &str = "org.mpris.MediaPlayer2.Player";

/// The most recent player; listeners are called on any change. A cheap
/// handle: clones share one state.
#[derive(Clone)]
pub struct Players(Rc<Inner>);

struct Inner {
    bus: Option<gio::DBusConnection>,
    listeners: RefCell<Vec<(u32, Rc<dyn Fn()>)>>,
    next_id: Cell<u32>,
    /// bus names, most recently active last
    names: RefCell<Vec<String>>,
    proxy: RefCell<Option<gio::DBusProxy>>,
    wanted: RefCell<Option<String>>,
}

thread_local! {
    static SHARED: RefCell<Option<Players>> = const { RefCell::new(None) };
}

/// The process's one Players (made on first use).
pub fn players() -> Players {
    SHARED.with(|s| s.borrow_mut().get_or_insert_with(Players::new).clone())
}

/// NameOwnerChanged for `name` (now owned by `new_owner`, "" when gone):
/// the player list after it. False: not a player, nothing to pick again.
pub fn owner_changed(names: &mut Vec<String>, name: &str, new_owner: &str) -> bool {
    if !name.starts_with(PREFIX) {
        return false;
    }
    let at = names.iter().position(|n| n == name);
    match (new_owner.is_empty(), at) {
        (false, None) => names.push(name.to_string()),
        (true, Some(i)) => {
            names.remove(i);
        }
        _ => {}
    }
    true
}

/// The app's name from its bus name: "org.mpris.MediaPlayer2.spotify.instance12" -> "Spotify".
pub fn app_of(bus_name: &str) -> String {
    let rest = bus_name.strip_prefix(PREFIX).unwrap_or(bus_name);
    let first = rest.split('.').next().unwrap_or("");
    // str.capitalize(): the first letter up, the rest down
    let mut c = first.chars();
    c.next().map(|f| f.to_uppercase().chain(c.flat_map(char::to_lowercase)).collect()).unwrap_or_default()
}

fn meta_value(meta: Option<&Variant>, key: &str) -> Option<Variant> {
    let meta = meta?;
    if !meta.is_type(glib::VariantTy::VARDICT) {
        return None;
    }
    glib::VariantDict::new(Some(meta)).lookup_value(key, None)
}

/// xesam:title, "Not Playing" without one.
pub fn title_of(meta: Option<&Variant>) -> String {
    meta_value(meta, "xesam:title").and_then(|v| v.str().map(str::to_string)).filter(|t| !t.is_empty()).unwrap_or_else(|| tr!("Not Playing"))
}

/// xesam:artist, the artists joined; some players send a plain string.
pub fn artist_of(meta: Option<&Variant>) -> String {
    let Some(v) = meta_value(meta, "xesam:artist") else { return String::new() };
    if let Some(s) = v.str() {
        return s.to_string();
    }
    v.get::<Vec<String>>().map(|a| a.join(", ")).unwrap_or_default()
}

/// mpris:artUrl, "" without one.
pub fn art_of(meta: Option<&Variant>) -> String {
    meta_value(meta, "mpris:artUrl").and_then(|v| v.str().map(str::to_string)).unwrap_or_default()
}

/// The app playing, as a desktop id (no .desktop), best clue first: the
/// DesktopEntry the player names; the app whose window its process has
/// (Chrome names none and calls itself "Chrome" on the bus name
/// "chromium": only its window, "Google-chrome", says which app it is);
/// its Identity; its bus name's app part. `entry`: a name -> desktop id
/// (appmatch, as the Dock matches windows).
pub fn player_app(desktop_entry: &str, window_app: Option<&str>, identity: &str, bus_name: &str, entry: &dyn Fn(&str) -> Option<String>) -> Option<String> {
    let bus_app = bus_name.strip_prefix(PREFIX).and_then(|r| r.split('.').next()).unwrap_or("");
    [desktop_entry, window_app.unwrap_or(""), identity, bus_app].into_iter().filter(|n| !n.is_empty()).find_map(entry)
}

/// player_app for a bus name, read live: the player's own properties
/// (Identity, DesktopEntry: org.mpris.MediaPlayer2's, not the Player
/// interface the proxy follows), the process owning the name and that
/// process's window (Wayfire). Blocking: off the main thread.
pub fn app_of_player(bus_name: &str) -> Option<String> {
    if bus_name.is_empty() {
        return None;
    }
    let bus = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE).ok()?;
    let call = |dest: &str, path: &str, iface: &str, method: &str, args: Variant| bus.call_sync(Some(dest), path, iface, method, Some(&args), None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE).ok();
    let root = |name: &str| {
        call(bus_name, PATH, "org.freedesktop.DBus.Properties", "Get", ("org.mpris.MediaPlayer2", name).to_variant())
            .and_then(|r| r.child_value(0).as_variant())
            .and_then(|v| v.str().map(str::to_string))
            .unwrap_or_default()
    };
    let (entry, identity) = (root("DesktopEntry"), root("Identity"));
    let pid = call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetConnectionUnixProcessID", (bus_name,).to_variant()).and_then(|r| r.get::<(u32,)>()).map_or(0, |(p,)| p as i64);
    let window = match sonata_system::mixer::whose(pid, None, "/proc") {
        Some(("app", app_id)) => Some(app_id),
        _ => None,
    };
    player_app(&entry, window.as_deref(), &identity, bus_name, &|n| sonata_desktop::appmatch::match_app_id(n))
}

impl Players {
    fn new() -> Self {
        let bus = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE).ok();
        let p = Self::with_bus(bus.clone());
        let Some(bus) = bus else { return p };
        let weak = Rc::downgrade(&p.0);
        #[allow(deprecated)] // signal_subscribe: what the other crates use, the replacement needs gio 0.21
        bus.signal_subscribe(Some("org.freedesktop.DBus"), Some("org.freedesktop.DBus"), Some("NameOwnerChanged"), Some("/org/freedesktop/DBus"), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, params| {
            let Some(inner) = weak.upgrade() else { return };
            let Some((name, _old, new)) = params.get::<(String, String, String)>() else { return };
            let me = Players(inner);
            let relevant = owner_changed(&mut me.0.names.borrow_mut(), &name, &new);
            if relevant {
                me.pick();
            }
        });
        let weak = Rc::downgrade(&p.0);
        bus.call(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames", None, None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE, move |r| {
            let (Some(inner), Ok(v)) = (weak.upgrade(), r) else { return };
            let Some((names,)) = v.get::<(Vec<String>,)>() else { return };
            *inner.names.borrow_mut() = names.into_iter().filter(|n| n.starts_with(PREFIX)).collect();
            Players(inner).pick();
        });
        p
    }

    /// No bus: nothing plays (tests, a session without D-Bus).
    pub fn with_bus(bus: Option<gio::DBusConnection>) -> Self {
        Self(Rc::new(Inner { bus, listeners: RefCell::default(), next_id: Cell::new(1), names: RefCell::default(), proxy: RefCell::default(), wanted: RefCell::default() }))
    }

    /// Call `f` on any change (a player came or went, a property changed).
    pub fn connect<F: Fn() + 'static>(&self, f: F) -> u32 {
        let id = self.0.next_id.get();
        self.0.next_id.set(id + 1);
        self.0.listeners.borrow_mut().push((id, Rc::new(f)));
        id
    }

    pub fn disconnect(&self, id: u32) {
        self.0.listeners.borrow_mut().retain(|(i, _)| *i != id);
    }

    fn pick(&self) {
        let name = self.0.names.borrow().last().cloned();
        let current = self.0.proxy.borrow().as_ref().and_then(|p| p.name()).map(|n| n.to_string());
        if current.is_some() && current == name {
            return;
        }
        *self.0.proxy.borrow_mut() = None;
        *self.0.wanted.borrow_mut() = name.clone();
        if let (Some(name), Some(bus)) = (name, self.0.bus.as_ref()) {
            // async: a hung player must not freeze the menu bar
            let weak = Rc::downgrade(&self.0);
            let wanted = name.clone();
            gio::DBusProxy::new(bus, gio::DBusProxyFlags::NONE, None, Some(&name), PATH, IFACE, gio::Cancellable::NONE, move |r| {
                let (Some(inner), Ok(proxy)) = (weak.upgrade(), r) else { return };
                if inner.wanted.borrow().as_deref() != Some(wanted.as_str()) {
                    return; // another player took over meanwhile
                }
                let weak = Rc::downgrade(&inner);
                proxy.connect_local("g-properties-changed", false, move |_| {
                    if let Some(inner) = weak.upgrade() {
                        Players(inner).changed();
                    }
                    None
                });
                *inner.proxy.borrow_mut() = Some(proxy);
                Players(inner).changed();
            });
        }
        self.changed();
    }

    fn changed(&self) {
        let listeners: Vec<Rc<dyn Fn()>> = self.0.listeners.borrow().iter().map(|(_, f)| f.clone()).collect();
        for f in listeners {
            f();
        }
    }

    // -- state ------------------------------------------------------------------------------
    pub fn active(&self) -> bool {
        self.0.proxy.borrow().is_some()
    }

    fn prop(&self, name: &str) -> Option<Variant> {
        self.0.proxy.borrow().as_ref().and_then(|p| p.cached_property(name))
    }

    pub fn playing(&self) -> bool {
        self.prop("PlaybackStatus").and_then(|v| v.str().map(|s| s == "Playing")).unwrap_or(false)
    }

    pub fn title(&self) -> String {
        title_of(self.prop("Metadata").as_ref())
    }

    pub fn artist(&self) -> String {
        artist_of(self.prop("Metadata").as_ref())
    }

    pub fn art(&self) -> String {
        art_of(self.prop("Metadata").as_ref())
    }

    /// "Spotify", "" with no player.
    pub fn app(&self) -> String {
        self.0.proxy.borrow().as_ref().and_then(|p| p.name()).map(|n| app_of(&n)).unwrap_or_default()
    }

    /// The player's bus name, "" with no player.
    pub fn bus_name(&self) -> String {
        self.0.proxy.borrow().as_ref().and_then(|p| p.name()).map(|n| n.to_string()).unwrap_or_default()
    }

    /// PlayPause, Next, Previous...
    pub fn call(&self, method: &str) {
        if let Some(p) = self.0.proxy.borrow().as_ref() {
            p.call(method, None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE, |_| {});
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn meta(pairs: &[(&str, Variant)]) -> Variant {
        let d = glib::VariantDict::new(None);
        for (k, v) in pairs {
            d.insert_value(k, v);
        }
        d.end()
    }

    /// Title / artist / art / app name come from the player's metadata.
    #[test]
    fn test_properties() {
        let _env = crate::testenv::env();
        let m = meta(&[("xesam:title", "Song".to_variant()), ("xesam:artist", vec!["A", "B"].to_variant()), ("mpris:artUrl", "file:///a.png".to_variant())]);
        assert_eq!((title_of(Some(&m)), artist_of(Some(&m)), art_of(Some(&m))), ("Song".into(), "A, B".into(), "file:///a.png".into()));
        assert_eq!(app_of("org.mpris.MediaPlayer2.spotify.instance12"), "Spotify");
        assert_eq!(app_of("org.mpris.MediaPlayer2.VLC"), "Vlc");
        assert_eq!((title_of(None), artist_of(None), art_of(None)), ("Not Playing".into(), String::new(), String::new()));
        let p = Players::with_bus(None);
        assert!(!p.active() && !p.playing());
        assert_eq!(p.app(), "");
    }

    /// NameOwnerChanged: a new player becomes current, a closed one hands back to the previous.
    #[test]
    fn test_newest_player_wins() {
        let mut names = Vec::new();
        let mut picks = Vec::new();
        for (n, new) in [("org.mpris.MediaPlayer2.vlc", ":1.5"), ("org.mpris.MediaPlayer2.firefox", ":1.6"), ("org.other.Name", ":1.7"), ("org.mpris.MediaPlayer2.firefox", "")] {
            if owner_changed(&mut names, n, new) {
                picks.push(names.last().cloned());
            }
        }
        assert_eq!(picks, [Some("org.mpris.MediaPlayer2.vlc".to_string()), Some("org.mpris.MediaPlayer2.firefox".into()), Some("org.mpris.MediaPlayer2.vlc".into())]);
    }

    /// A string artist is shown as is.
    #[test]
    fn test_artist_as_plain_string() {
        let m = meta(&[("xesam:artist", "Muse".to_variant())]);
        assert_eq!(artist_of(Some(&m)), "Muse");
    }

    /// Vini: Netflix in Chrome showed an empty square with a note in Now
    /// Playing -- Chrome sends no art, names no DesktopEntry and calls
    /// itself "Chrome" on the bus name "chromium". Its window
    /// ("Google-chrome") finds the app, before the bus name's Chromium.
    #[test]
    fn test_no_chrome_icon_in_now_playing() {
        let entry = |n: &str| match n.to_lowercase().as_str() {
            "google-chrome" => Some("google-chrome".to_string()),
            "chromium" => Some("chromium".to_string()),
            "spotify" => Some("spotify-launcher".to_string()),
            _ => None,
        };
        let chrome = "org.mpris.MediaPlayer2.chromium.instance7754";
        assert_eq!(player_app("", Some("Google-chrome"), "Chrome", chrome, &entry).as_deref(), Some("google-chrome"));
        // a DesktopEntry named wins; without a window, the Identity, then the bus name
        assert_eq!(player_app("spotify", Some("Google-chrome"), "", chrome, &entry).as_deref(), Some("spotify-launcher"));
        assert_eq!(player_app("", None, "Spotify", "org.mpris.MediaPlayer2.x", &entry).as_deref(), Some("spotify-launcher"));
        assert_eq!(player_app("", None, "Chrome", chrome, &entry).as_deref(), Some("chromium"));
        assert_eq!(player_app("", None, "Mystery", "org.mpris.MediaPlayer2.mystery", &entry), None);
        assert_eq!(app_of_player(""), None);
    }

    #[test]
    fn listeners_come_and_go() {
        let p = Players::with_bus(None);
        let hits = Rc::new(Cell::new(0));
        let h = hits.clone();
        let id = p.connect(move || h.set(h.get() + 1));
        p.pick();
        assert_eq!(hits.get(), 1);
        p.disconnect(id);
        p.pick();
        assert_eq!(hits.get(), 1);
    }
}
