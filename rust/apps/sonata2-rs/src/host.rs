//! What the menu bar's process hosts besides the bar (__main__.py's
//! run_topbar and topbar.py's TopBarWindow): the actions Wayfire's key
//! bindings call over D-Bus -- the app switcher (Alt+Tab), screenshots
//! and recording (Super+Shift+3/4/5, Print), the Character Viewer, the
//! clipboard history (Super+V), drawing on the screen, the Game Bar
//! (Super+G), the instant replay -- and Mission Control's backdrop.
//!
//! The pieces live in their own crates, which the menu bar crate can't
//! depend on (some depend on it); this binary depends on them all, so it
//! hands them to the menu bar here. Without this the Rust session's keys
//! asked a menu bar that had none of these actions and nothing happened
//! (Vini: Super+V, the screenshots).

use gtk::{gio, glib, prelude::*};
use sonata_mission::{switcher, Wayfire};
use std::cell::RefCell;
use std::rc::Rc;

/// The actions the menu bar answers (config/wayfire.ini's gdbus calls and
/// `sonata2-rs screenshot`), besides its own "osd" and "input-next".
#[cfg_attr(not(test), allow(dead_code))] // (the test holds the bindings to it)
pub const ACTIONS: &[&str] = &["switcher", "fullscreen", "capture", "draw", "replay-save", "emoji", "clipboard", "gamebar"];

/// Everything above, once, on the main display's menu bar (never in a
/// preview: a second backdrop or clipboard watcher would double up).
pub fn topbar_started(app: &gtk::Application, win: &Rc<sonata_topbar::window::TopBarWindow>) {
    if !win.layered() {
        return;
    }
    // full screen remembered per app (Ctrl+Super+F), the game in front
    // followed (lighter effects, no screen corners over it), the GPU's memory
    let corners = sonata_sessionui::screencorners::ScreenCorners::start(app);
    let full = sonata_sessionui::start_fullscreen_services(corners.as_ref());
    install_actions(app, full.rules.clone());
    std::mem::forget((corners, full));
    // Mission Control's dark backdrop and the desktops bar follow Wayfire's
    // scale plugin from the menu bar (topbar.py: MissionBackdrop(app))
    sonata_ui::css::register_module("shell/mission.py");
    sonata_ui::css::register_module("shell/spaces.py");
    std::mem::forget(sonata_mission::mission::MissionBackdrop::new(app));
    // displays run at their highest refresh rate (and auto scale), kept up
    // to date as they change -- run_topbar does it once the main bar is up;
    // nothing called it in the Rust session
    sonata_sessionui::monitors::ensure_refresh();
    start_services(app);
}

/// The menu bar's background services (topbar.py's TopBarWindow, main
/// display only). Each lives as long as the process; the ones holding a
/// child process or a sound stop with it.
fn start_services(app: &gtk::Application) {
    use sonata_sessionui as su;
    let mut stops: Vec<Box<dyn Fn()>> = Vec::new();
    // Night Shift's schedule and the display's warmth
    if let Some(ns) = su::nightshift::NightShift::start() {
        stops.push(Box::new(move || {
            ns.stop();
        }));
    }
    // locking when idle, the display going dark (swayidle)
    let hooks = su::idlelock::Hooks {
        compositor_off: Box::new(|| {
            sonata_tweaks::displaysleep::compositor_off();
        }),
        // the compositor's own idle (input only: media playing doesn't count),
        // else IdleLock keeps swayidle as before
        watch: Some(sonata_shell::idlewatch::IdleWatch::connect()).filter(|w| w.ok() && w.input_idle()).map(|w| Rc::new(w) as Rc<dyn su::idlepolicy::IdleWatch>),
    };
    if let Some(il) = su::idlelock::IdleLock::start(hooks) {
        stops.push(Box::new(move || il.stop()));
    }
    // a lock screen gone without unblocking USB: every 30 s, unless locked
    // or a game is in front (nothing stale then)
    let paused = || su::idlelock::is_locked() || sonata_tweaks::gamemode::active();
    std::mem::forget(sonata_backend::usbprotect::StaleCheck::start(paused, su::idlelock::is_locked));
    // old Trash items (when that's on); window styles still in place
    std::mem::forget(sonata_tweaks::housekeeping::Housekeeping::start());
    std::mem::forget(sonata_session::stylecheck::Watch::start(sonata_tweaks::gpu::notify));
    // an updated Sonata's blur rule at once (surfaces never blurred: Overview,
    // the Game Bar...), not only after the look is next changed
    glib::timeout_add_seconds_local_once(2, || {
        std::thread::spawn(sonata_tweaks::titlebars::refresh_blur_rule);
    });
    // what this session runs on, for bug reports (~/.cache/sonata2/env.txt)
    let ui = vec![format!("gtk {}.{}.{}", gtk::major_version(), gtk::minor_version(), gtk::micro_version()), format!("libadwaita {}.{}.{}", adw::major_version(), adw::minor_version(), adw::micro_version())];
    sonata_session::envreport::start(ui);
    // a browser sharing the screen: a pill in the menu bar
    sonata_ui::css::register_module("shell/sharing.py");
    let sharing = sonata_capture::sharing::SharingControl::new(app, Rc::new(ShareBars));
    let s = Rc::downgrade(&sharing);
    sonata_dock::toplevels::manager().add_event_listener(Rc::new(move |_| {
        if let Some(s) = s.upgrade() {
            s.update();
        }
    }));
    std::mem::forget(sharing);
    // Steam in Sonata's look
    std::mem::forget(sonata_tweaks::steamtheme::Watch::new(sonata_tweaks::steamtheme::SETTLE_S));
    // background apps give memory back; last resort: the alert
    std::mem::forget(sonata_gamebar::memorywatch::MemoryWatch::start(app));
    // Clock's alarms and timers ring from here
    if let Some(a) = su::alarmservice::AlarmService::start(app) {
        stops.push(Box::new(move || a.stop()));
    }
    // Notes' reminders notify with Notes closed (Vini)
    // (read-only, followed as Notes saves: a writable store here purged and
    // saved notes.json behind Notes' back, and a reminder added after login
    // never rang until the next session)
    let reminders = sonata_notes::notifier::ReminderWatch::start();
    stops.push(Box::new(move || reminders.stop()));
    // a new Sonata release: say so
    let check: su::updatenotify::Check = std::sync::Arc::new(|| sonata_system::selfupdate::available(20).map(|r| su::updatenotify::Release { tag: r.tag }));
    if let Some(u) = su::updatenotify::UpdateNotifier::start(check) {
        stops.push(Box::new(move || u.stop()));
    }
    // new headphones / headsets used at once (audiofollow.py)
    std::mem::forget(sonata_system::audiofollow::start());
    // plugged in again: back to the energy mode chosen for it (powerprofile.Follow)
    follow_power();
    // Settings > Sharing > Screen Sharing; the system-wide equalizer
    let share = sonata_system::screenshare::ScreenSharing::new();
    share.start();
    let eq = sonata_system::equalizer::Equalizer::new();
    eq.start();
    stops.push(Box::new(move || share.stop()));
    stops.push(Box::new(move || eq.stop()));
    // the title bars Wayfire draws (terminals, X11 apps) follow Dark Mode live
    let colors = |dark: bool| {
        std::thread::spawn(move || sonata_tweaks::titlebars::apply_colors(dark));
    };
    let sm = adw::StyleManager::default();
    colors(sm.is_dark());
    sm.connect_dark_notify(move |m| colors(m.is_dark()));
    app.connect_shutdown(move |_| stops.iter().for_each(|f| f()));
}

/// powerprofile.Follow on UPower's events (topbar.py: power.watch), a
/// burst of property changes looked at once.
fn follow_power() {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) else { return };
    let follow = Rc::new(RefCell::new(sonata_tweaks::powerprofile::Follow::new()));
    let pending: Rc<std::cell::Cell<bool>> = Rc::default();
    for (name, path) in [("org.freedesktop.UPower", "/org/freedesktop/UPower"), ("org.freedesktop.UPower", "/org/freedesktop/UPower/devices/DisplayDevice")] {
        let (f, p) = (follow.clone(), pending.clone());
        // (kept for the process: the bus holds the subscription)
        #[allow(deprecated)]
        bus.signal_subscribe(Some(name), Some("org.freedesktop.DBus.Properties"), Some("PropertiesChanged"), Some(path), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, _| {
            if p.replace(true) {
                return;
            }
            let (f, p) = (f.clone(), p.clone());
            glib::timeout_add_local_once(std::time::Duration::from_millis(150), move || {
                p.set(false);
                f.borrow_mut().changed();
            });
        });
    }
}

/// The browsers' "… is sharing your screen" bars (Wayfire's views: the
/// Dock's tracker leaves them out of the apps).
struct ShareBars;

impl ShareBars {
    fn bars() -> Vec<(i64, String)> {
        let views = sonata_backend::wfipc::call("window-rules/list-views", serde_json::json!({})).unwrap_or_default();
        views
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|v| {
                let (app_id, title) = (v.get("app-id")?.as_str().unwrap_or(""), v.get("title")?.as_str().unwrap_or(""));
                let id = v.get("id")?.as_i64()?;
                sonata_dock::toplevels::is_share_bar(app_id, title).then(|| (id, title.to_string()))
            })
            .collect()
    }
}

impl sonata_capture::sharing::ShareBars for ShareBars {
    fn titles(&self) -> Vec<String> {
        Self::bars().into_iter().map(|b| b.1).collect()
    }
    fn close_all(&self) {
        for (id, _) in Self::bars() {
            sonata_backend::wfipc::call("window-rules/close-view", serde_json::json!({"id": id}));
        }
    }
    fn front(&self) {
        for (id, _) in Self::bars() {
            sonata_backend::wfipc::call("window-rules/focus-view", serde_json::json!({"id": id}));
        }
    }
}

/// What `cell` holds, made by make() the first time. (The cell is let go
/// before make() runs: holding it while the closure stored into it panicked
/// -- the menu bar went down on the first Ctrl+Super+Space.)
fn built<T: Clone>(cell: &RefCell<Option<T>>, make: impl FnOnce() -> T) -> T {
    let got = cell.borrow().clone();
    if let Some(v) = got {
        return v;
    }
    let v = make();
    cell.replace(Some(v.clone()));
    v
}

fn make_switcher(app: &gtk::Application) -> Rc<switcher::Switcher> {
    sonata_ui::css::register_module("shell/switcher.py");
    switcher::Switcher::new(app, Rc::new(switcher::IpcWindows(Wayfire)), true)
}

/// The login's warm-up starts this long after the menu bar's actions...
pub const WARM_START_MS: u64 = 8000;
/// ... and takes one step this often.
pub const WARM_STEP_MS: u64 = 1500;

/// Warm-up after login (__main__.py's warm(): Vini, no lag the first
/// time): the app switcher, then the volume OSD made and drawn once
/// unseen, then the Control Center built once (its icons and modules
/// loaded), one step every 1.5 s so the login itself stays light. The
/// emoji and clipboard pickers are not among them, as in Python: kept for
/// the whole session they held the emoji font's glyphs and the
/// clipboard's previews though most days neither is opened.
fn warm_up(app: &gtk::Application, sw: Rc<RefCell<Option<Rc<switcher::Switcher>>>>) {
    fn step(app: glib::WeakRef<gtk::Application>, sw: Rc<RefCell<Option<Rc<switcher::Switcher>>>>, n: u32) {
        let Some(a) = app.upgrade() else { return };
        match n {
            // (one opened already -- an Alt+Tab came first -- is warm)
            0 if sw.borrow().is_none() => {
                let s = built(&sw, || make_switcher(&a));
                sonata_shell::layer::prewarm(&s.win, 1, 3, None, None);
            }
            1 => sonata_topbar::osd::prewarm(&a),
            // (kept by the menu bar: once, it may have made it already)
            2 => {
                if let Some(b) = sonata_topbar::window::first_bar() {
                    b.prewarm_control_center();
                }
            }
            _ => {}
        }
        if n < 2 {
            glib::timeout_add_local_once(std::time::Duration::from_millis(WARM_STEP_MS), move || step(app, sw, n + 1));
        }
    }
    let app = app.downgrade();
    glib::timeout_add_local_once(std::time::Duration::from_millis(WARM_START_MS), move || step(app, sw, 0));
}

/// The menu bar's actions, each built on first use and kept after, as
/// run_topbar does.
pub fn install_actions(app: &gtk::Application, rules: Option<Rc<sonata_sessionui::fullscreen::Rules>>) {
    // Full screen (Ctrl+Super+F): the focused window, remembered per app
    let act = gio::SimpleAction::new("fullscreen", None);
    act.connect_activate(move |_, _| {
        if let Some(r) = &rules {
            r.toggle();
        }
    });
    app.add_action(&act);

    // App switcher: Alt+Tab -> "next", Alt+Shift+Tab -> "prev"
    let sw: Rc<RefCell<Option<Rc<switcher::Switcher>>>> = Rc::default();
    let (sw_pads, sw_warm) = (sw.clone(), sw.clone());
    let act = gio::SimpleAction::new("switcher", Some(glib::VariantTy::STRING));
    let a = app.downgrade();
    act.connect_activate(move |_, param| {
        let Some(app) = a.upgrade() else { return };
        let back = param.and_then(|p| p.str()) == Some("prev");
        if switcher::mission_control_step(&Wayfire, back) {
            return; // Mission Control open: its highlight moved instead
        }
        let s = built(&sw, || make_switcher(&app));
        // pressed while it drew itself unseen: the warm-up ends first
        sonata_shell::layer::end_prewarm(&s.win);
        s.step(if back { -1 } else { 1 });
    });
    app.add_action(&act);
    warm_up(app, sw_warm);

    // Screenshots and recording ("capture"), drawing ("draw"), the instant replay ("replay-save")
    let cap = sonata_capture::capture::Capture::new(app);
    sonata_capture::capture::install_actions(app, &cap);
    std::mem::forget(cap.clone());

    // Character Viewer (Ctrl+Super+Space)
    let emoji: Rc<RefCell<Option<Rc<sonata_clipboard::emoji::EmojiPicker>>>> = Rc::default();
    let act = gio::SimpleAction::new("emoji", None);
    let a = app.downgrade();
    act.connect_activate(move |_, _| {
        let Some(app) = a.upgrade() else { return };
        let p = built(&emoji, || {
            sonata_ui::css::register_module("shell/emoji.py");
            let p = sonata_clipboard::emoji::EmojiPicker::new(&app);
            p
        });
        p.open();
    });
    app.add_action(&act);

    // Clipboard history (Super+V): the history from login on, the panel on first use
    let history = sonata_clipboard::clipboard::History::new(true);
    let h = history.clone();
    app.connect_shutdown(move |_| h.stop());
    let picker: Rc<RefCell<Option<Rc<sonata_clipboard::clip_picker::ClipboardPicker>>>> = Rc::default();
    let act = gio::SimpleAction::new("clipboard", None);
    let a = app.downgrade();
    act.connect_activate(move |_, _| {
        let Some(app) = a.upgrade() else { return };
        let p = built(&picker, || {
            sonata_ui::css::register_module("shell/clip_picker.py");
            let p = sonata_clipboard::clip_picker::ClipboardPicker::new(&app, history.clone());
            p
        });
        p.open();
    });
    app.add_action(&act);

    // Game Bar (Super+G): built on first use, kept after
    let bar: Rc<RefCell<Option<Rc<sonata_gamebar::gamebar::GameBar>>>> = Rc::default();
    let act = gio::SimpleAction::new("gamebar", None);
    let a = app.downgrade();
    let c = Rc::downgrade(&cap);
    let pads: Rc<RefCell<Option<std::rc::Weak<sonata_gamepad::Gamepads>>>> = Rc::default();
    let pads_c = pads.clone();
    let bar_c = bar.clone();
    act.connect_activate(move |_, _| {
        let Some(app) = a.upgrade() else { return };
        let gb = built(&bar, || {
            let p = pads_c.clone();
            let grab: Rc<dyn Fn(bool)> = Rc::new(move |on| {
                if let Some(p) = p.borrow().as_ref().and_then(|w| w.upgrade()) {
                    p.grab(on);
                }
            });
            let gb = sonata_gamebar::gamebar::GameBar::new(app.upcast_ref(), crate::gamebar_host::host(&app, c.clone(), grab));
            gb
        });
        gb.toggle();
    });
    app.add_action(&act);

    // Game controllers drive the desktop: the switcher, the Game Bar, a pointer
    let g = sonata_gamepad::Gamepads::start(app, Rc::new(PadTargets { switcher: sw_pads, gamebar: bar_c }));
    pads.replace(Some(Rc::downgrade(&g)));
    app.connect_shutdown(move |_| g.stop());
}

/// What the controllers drive (topbar.py's Gamepads(app, switcher, gamebar)).
struct PadTargets {
    switcher: Rc<RefCell<Option<Rc<switcher::Switcher>>>>,
    gamebar: Rc<RefCell<Option<Rc<sonata_gamebar::gamebar::GameBar>>>>,
}

impl PadTargets {
    fn sw(&self) -> Option<Rc<switcher::Switcher>> {
        self.switcher.borrow().clone()
    }
    fn gb(&self) -> Option<Rc<sonata_gamebar::gamebar::GameBar>> {
        self.gamebar.borrow().clone()
    }
}

impl sonata_gamepad::Targets for PadTargets {
    fn switcher_open(&self) -> bool {
        self.sw().is_some_and(|s| s.is_open())
    }
    fn switcher_step(&self, direction: i32) {
        if let Some(s) = self.sw() {
            s.step(direction);
        }
    }
    fn switcher_switch(&self) {
        if let Some(s) = self.sw() {
            s.switch();
        }
    }
    fn switcher_close(&self) {
        if let Some(s) = self.sw() {
            s.close();
        }
    }
    fn gamebar_open(&self) -> bool {
        self.gb().is_some_and(|g| g.is_open())
    }
    fn gamebar_pad(&self, name: &str) {
        if let Some(g) = self.gb() {
            g.pad(name);
        }
    }
    fn key(&self, name: &str) -> bool {
        sonata_clipboard::vkeyboard::press(name, &[])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini's Rust session: displays stayed at their default refresh rate
    /// Notes' reminders from the menu bar: a store that could save over
    /// notes.json, never read again -- a reminder added after login didn't
    /// ring (release 0.15.0's fix, lost in the port).
    #[test]
    fn test_reminder_added_after_login_never_rang() {
        let src = include_str!("host.rs");
        let services = &src[src.find("fn start_services").unwrap()..src.find("#[cfg(test)]").unwrap()];
        assert!(services.contains("ReminderWatch::start()"));
        assert!(!services.contains("store::Store::new()"));
    }

    /// The first Alt+Tab and the first volume key after login lagged in
    /// the Rust session: the switcher and the OSD were built and drawn the
    /// first time they were used (Python's warm() draws them unseen from
    /// 8 s on, then the Control Center). Not the emoji or clipboard
    /// pickers: Python keeps them out of the warm-up on purpose.
    #[test]
    fn test_first_alt_tab_after_login_lagged() {
        let src = include_str!("host.rs");
        let actions = &src[src.find("pub fn install_actions").unwrap()..src.find("/// What the controllers drive").unwrap()];
        assert!(actions.contains("warm_up(app, sw_warm)"), "no warm-up at login");
        assert!(actions.contains("sonata_shell::layer::end_prewarm(&s.win)"), "Alt+Tab during the warm-up opened it see-through");
        let warm = &src[src.find("fn warm_up(").unwrap()..src.find("pub fn install_actions").unwrap()];
        for step in ["layer::prewarm(&s.win", "osd::prewarm(&a)", "prewarm_control_center()"] {
            assert!(warm.contains(step), "{step} not warmed");
        }
        assert!(!warm.contains("emoji") && !warm.contains("clip_picker"), "the pickers are built on first use");
        assert_eq!((WARM_START_MS, WARM_STEP_MS), (8000, 1500)); // __main__.py's
    }

    /// -- run_topbar's monitors.ensure_refresh() had no caller in Rust.
    #[test]
    fn test_displays_never_at_highest_refresh_rate() {
        let src = include_str!("host.rs");
        let started = &src[src.find("pub fn topbar_started").unwrap()..src.find("fn start_services").unwrap()];
        assert!(started.contains("monitors::ensure_refresh()"));
    }

    /// Vini: the menu bar crashed on the first Ctrl+Super+Space -- the
    /// picker was stored while its cell was still borrowed. The first
    /// call builds it once, the next ones get the same.
    #[test]
    fn test_menu_bar_crashed_opening_emoji() {
        let cell: RefCell<Option<Rc<u32>>> = RefCell::default();
        let made = std::cell::Cell::new(0);
        let a = built(&cell, || {
            made.set(made.get() + 1);
            Rc::new(7)
        });
        let b = built(&cell, || unreachable!("built once"));
        assert!(Rc::ptr_eq(&a, &b));
        assert_eq!(made.get(), 1);
    }

    /// Vini, in the Rust session: Super+V, the screenshot keys and the
    /// rest did nothing -- the Rust menu bar had only "osd" and
    /// "input-next" of the actions Wayfire's bindings call. Every action
    /// config/wayfire.ini calls on the menu bar is one it answers.
    #[test]
    fn test_menu_bar_keys_did_nothing() {
        let ini = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../config/wayfire.ini")).unwrap();
        let mut called: Vec<&str> = ini
            .lines()
            .filter(|l| l.contains("sonata2.topbar") && l.contains("ActivateAction"))
            .filter_map(|l| l.split("ActivateAction ").nth(1)?.split_whitespace().next())
            .collect();
        called.sort();
        called.dedup();
        assert!(called.len() >= 8, "{called:?}");
        let own = ["osd", "input-next"];
        for name in called {
            assert!(ACTIONS.contains(&name) || own.contains(&name), "nobody answers {name}");
        }
        // what `sonata2-rs screenshot` asks for
        assert!(ACTIONS.contains(&"capture"));
    }
}

// -- the menu bar's seams (sonata_topbar::seams::Services) -----------------------------------------
use sonata_topbar::seams;

/// The windows, from the Dock's tracker over Wayfire's IPC (one socket per
/// process): the menu bar's app name, its Window menu, Quit.
struct Windows(Rc<sonata_dock::toplevels::Manager>);

impl Windows {
    fn find(&self, t: &seams::Toplevel) -> Option<sonata_dock::toplevels::Toplevel> {
        self.0.get(t.id)
    }
}

fn seam_toplevel(t: &sonata_dock::toplevels::Toplevel) -> seams::Toplevel {
    seams::Toplevel { id: t.id, app_id: t.app_id.clone(), title: t.title.clone(), activated: t.activated, minimized: t.minimized, maximized: t.maximized }
}

impl seams::Windows for Windows {
    fn toplevels(&self) -> Vec<seams::Toplevel> {
        self.0.toplevels().iter().map(seam_toplevel).collect()
    }
    fn add_listener(&self, f: Rc<dyn Fn()>) -> u64 {
        self.0.add_listener(f)
    }
    fn remove_listener(&self, id: u64) {
        self.0.remove_listener(id)
    }
    fn activate(&self, t: &seams::Toplevel) {
        if let Some(t) = self.find(t) {
            self.0.activate(&t);
        }
    }
    fn minimize(&self, t: &seams::Toplevel) {
        if let Some(t) = self.find(t) {
            self.0.minimize(&t);
        }
    }
    fn unminimize(&self, t: &seams::Toplevel) {
        // (focusing a minimized window brings it back)
        self.activate(t);
    }
    fn close(&self, t: &seams::Toplevel) {
        if let Some(t) = self.find(t) {
            self.0.close(&t);
        }
    }
    fn set_maximized(&self, t: &seams::Toplevel, on: bool) {
        if let (true, Some(t)) = (on, self.find(t)) {
            self.0.set_maximized(&t);
        }
    }
}

/// The same windows as Quit / Log Out see them (quitapps).
struct QuitWindows(Rc<sonata_dock::toplevels::Manager>);

impl sonata_sessionui::quitapps::Windows for QuitWindows {
    fn toplevels(&self) -> Vec<sonata_sessionui::quitapps::Toplevel> {
        self.0.toplevels().iter().map(|t| sonata_sessionui::quitapps::Toplevel { id: t.id as u64, app_id: t.app_id.clone(), title: t.title.clone() }).collect()
    }
    fn close(&self, id: u64) {
        if let Some(t) = self.0.get(id as i64) {
            self.0.close(&t);
        }
    }
}

pub fn quit_windows() -> Rc<dyn sonata_sessionui::quitapps::Windows> {
    Rc::new(QuitWindows(sonata_dock::toplevels::manager()))
}

/// The apps' own shortcuts for windows and tabs (shell/apptabs.py),
/// pressed with the virtual keyboard as vkeyboard.press does.
struct AppTabs;

impl seams::AppTabs for AppTabs {
    fn kind(&self, app_id: &str) -> Option<String> {
        sonata_mission::apptabs::kind(app_id).map(|k| format!("{k:?}").to_lowercase())
    }
    fn has(&self, app_id: &str, action: &str) -> bool {
        sonata_mission::apptabs::keys(app_id, action).is_some()
    }
    fn press(&self, app_id: &str, action: &str) {
        sonata_mission::apptabs::press(app_id, action, sonata_clipboard::vkeyboard::press);
    }
}

/// Control Center's Screenshot: the toolbar of the capture this process hosts.
pub fn show_capture_toolbar() {
    if let Some(app) = gio::Application::default() {
        app.activate_action("capture", Some(&"toolbar".to_variant()));
    }
}

/// system.set_dark_mode: Sonata and libadwaita / GTK 4 apps (color-scheme),
/// GTK 3 apps (the theme's name), Flatpak apps (their theme).
fn set_dark_mode(on: bool) {
    const IFACE: &str = "org.gnome.desktop.interface";
    sonata_tweaks::prefs_write::set(IFACE, "color-scheme", if on { "prefer-dark" } else { "default" });
    sonata_tweaks::prefs_write::set(IFACE, "gtk-theme", if on { "Sonata-Dark" } else { "Sonata-Light" });
    sonata_tweaks::flatpak_theme::apply();
}

/// The seams sonata2-rs can fill beyond the mixer, Night Shift, Log Out and
/// the capture toolbar: without them the menu bar had no Window menu, no
/// app name, no Quit, no New Folder (Vini's Rust session).
pub fn fill_services(s: &mut seams::Services) {
    let m = sonata_dock::toplevels::manager();
    s.windows = Some(Rc::new(Windows(m.clone())));
    s.apptabs = Some(Rc::new(AppTabs));
    s.quiet_paused = Some(Rc::new(|| sonata_sessionui::idlelock::is_locked() || sonata_tweaks::gamemode::active()));
    s.set_dark_mode = Some(std::sync::Arc::new(set_dark_mode));
    s.quit_app = Some(Rc::new(move |these: &[seams::Toplevel], key: &str| {
        let wins: Vec<_> = these.iter().map(|t| sonata_sessionui::quitapps::Toplevel { id: t.id as u64, app_id: t.app_id.clone(), title: t.title.clone() }).collect();
        std::mem::forget(sonata_sessionui::quitapps::quit_app(Rc::new(QuitWindows(m.clone())), &wins, key));
    }));
    s.open_folder = Some(Rc::new(sonata_dock::dock_menu::open_folder));
    s.new_desktop_folder = Some(Rc::new(|| {
        let desk = gio::File::for_path(glib::user_special_dir(glib::UserDirectory::Desktop).unwrap_or_else(|| glib::home_dir().join("Desktop")));
        sonata_files::ops::new_folder(&desk, |_| {}, |_| {}, None);
    }));
    s.power_boosted = Some((Rc::new(sonata_tweaks::gamemode::boosted), Rc::new(sonata_tweaks::gamemode::set_boosted)));
    s.remember_profile = Some(Rc::new(sonata_tweaks::powerprofile::remember));
    s.extra_modules.push(("fpslimit".into(), Rc::new(sonata_gamebar::fpsmodule::module)));
    s.extra_modules.push((
        "keyboard".into(),
        Rc::new(|| {
            let km = sonata_gamebar::kbdmodule::KeyboardModule::new(None, None);
            let root: gtk::Widget = km.root.clone().upcast();
            // the module's clicks reach it through a weak reference: it lives as long as its widget
            unsafe { root.set_data("sonata-kbd-module", km) };
            root
        }),
    ));
}
