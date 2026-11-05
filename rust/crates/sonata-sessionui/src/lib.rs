//! The session's background helpers (sonata2/shell/): what the menu bar
//! process runs besides the bar itself, what the Dock runs besides the
//! Dock, and two small commands of their own.
//!
//! | Python | here | runs in |
//! |---|---|---|
//! | idlelock.py | [`idlelock`] | the menu bar ([`idlelock::IdleLock`]); `lock-wait` ([`idlelock::run_lock_wait`]) |
//! | idlepolicy.py | [`idlepolicy`] | the menu bar, under IdleLock |
//! | blackout.py | [`blackout`] | the menu bar (IdleLock's darkening) |
//! | screencorners.py | [`screencorners`] | the menu bar ([`screencorners::ScreenCorners`]); lock / login screens (sonata-shell's `screencorners::corners_overlay`) |
//! | nightshift.py | [`nightshift`] | the menu bar ([`nightshift::NightShift`]); Control Center / Settings ([`nightshift::set_manual`], [`nightshift::is_on`]) |
//! | quitapps.py | [`quitapps`] | the menu bar and Settings ([`quitapps::end_session`]); Dock and app menu ([`quitapps::quit_app`]) |
//! | quitonclose.py | [`quitonclose`] | the Dock ([`quitonclose::QuitOnClose`]); every launcher ([`quitonclose::mark_launch`]) |
//! | monitors.py | [`monitors`] | wallpaper, menu bar, Dock |
//! | alarmservice.py | [`alarmservice`] | the menu bar ([`alarmservice::AlarmService`]) |
//! | updatenotify.py | [`updatenotify`] | the menu bar ([`updatenotify::UpdateNotifier`]) |
//! | setup.py | [`setup`] | `sonata2-rs setup [--preview]` ([`setup::run_setup`]) |
//! | fullscreen.py | [`fullscreen`] | the menu bar ([`fullscreen::Rules`]: Ctrl+Super+F, games) |
//! | gamemode.py's Watcher | [`fullscreenfirst`] | the menu bar ([`fullscreenfirst::FullscreenFirst`]) |
//! | vram.py | [`vram`] | the menu bar ([`vram::VramWatch`]) |
//!
//! The last three come together in [`start_fullscreen_services`].
//!
//! welcome.py is `sonata-welcome` (apps/welcome).
//!
//! # Side by side (trial)
//!
//! While the Python shell runs, its menu bar already keeps swayidle, the
//! idle darkening, wlsunset, the screen corners, the alarms and the update
//! check; its Dock quits apps on close. A second copy would fight it (two
//! idle timers, two gamma clients, every alarm ringing twice). So on trial
//! (`sonata_config::side_by_side()`) the services' `start()` return None and
//! change nothing; `sonata2-rs session-try <what>` ([`run_try`]) shows the
//! parts that can be looked at safely.

pub mod alarmservice;
pub mod blackout;
pub mod fullscreen;
pub mod fullscreenfirst;
pub mod idlelock;
pub mod idlepolicy;
pub mod monitors;
pub mod nightshift;
pub mod quitapps;
pub mod quitonclose;
pub mod screencorners;
pub mod setup;
pub mod trial;
pub mod updatenotify;
pub mod vram;
mod wfevents;

pub use trial::run_try;

use std::rc::Rc;

/// What [`start_fullscreen_services`] started; keep it for the process' life.
pub struct FullscreenServices {
    /// Ctrl+Super+F (the menu bar's "fullscreen" action: `rules.toggle()`);
    /// None on trial
    pub rules: Option<Rc<fullscreen::Rules>>,
    /// fullscreen first: `active()`, `output()`, `connect(|full| ...)` --
    /// the menu bar pauses its polling while it's on
    pub first: Rc<fullscreenfirst::FullscreenFirst>,
    /// a full NVIDIA card; None without one, and on trial
    pub vram: Option<Rc<vram::VramWatch>>,
}

/// The menu bar's full-screen helpers, wired as topbar.py does: the rules
/// (Ctrl+Super+F, games), fullscreen first (with its energy mode and
/// lighter effects), the screen corners following it (none over a
/// full-screen game), the VRAM watch feeding the lighter effects and
/// warning through a notification, and the GPU notices at login. Call it
/// once, with the menu bar's ScreenCorners (None when it has none). On
/// trial only fullscreen first runs, as a listener (see each `start()`).
pub fn start_fullscreen_services(corners: Option<&Rc<screencorners::ScreenCorners>>) -> FullscreenServices {
    let rules = fullscreen::Rules::start();
    let first = fullscreenfirst::FullscreenFirst::start();
    if let Some(c) = corners {
        let (c, f) = (Rc::downgrade(c), Rc::downgrade(&first));
        let follow = move |full: bool| {
            if let (Some(c), Some(f)) = (c.upgrade(), f.upgrade()) {
                c.fullscreen_on(if full { f.output() } else { None }.as_deref());
            }
        };
        follow(first.active());
        first.connect(follow);
    }
    vram::start_gpu_notices(); // crashed on the discrete GPU last time, or started on the other one
    let vram = vram::VramWatch::start(Rc::new(|title: &str, body: &str| sonata_tweaks::gpu::notify(title, body, "video-display")));
    if let Some(v) = &vram {
        // lighter effects while a game fills the card
        let f = Rc::downgrade(&first);
        v.connect(move |used, total| {
            if let Some(f) = f.upgrade() {
                f.memory(used, total);
            }
        });
    }
    FullscreenServices { rules, first, vram }
}

use serde_json::Value;

/// Python's int() of a config value: a number (a float cut toward zero),
/// a numeric string, a bool; None where int() raises.
pub(crate) fn py_int(v: &Value) -> Option<i64> {
    match v {
        Value::Bool(b) => Some(*b as i64),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// Python's truthiness of a config value.
pub(crate) fn truthy(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Null => false,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// A command's output, or None when it can't run or takes longer than
/// `timeout_ms` (killed then). stderr is dropped: only the answer is parsed.
pub(crate) fn output_within(cmd: &[&str], timeout_ms: u64) -> Option<String> {
    use std::io::Read;
    use std::process::{Command, Stdio};
    let mut child = Command::new(cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().ok()?;
    let mut out = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let mut s = String::new();
        let _ = out.read_to_string(&mut s);
        s
    });
    let end = std::time::Instant::now() + std::time::Duration::from_millis(timeout_ms);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if std::time::Instant::now() < end => std::thread::sleep(std::time::Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
    reader.join().ok()
}

/// What runs `sonata2-rs`: this binary (a component started from the
/// session), else the name on PATH.
pub(crate) fn self_exe() -> String {
    std::env::current_exe().ok().filter(|p| !p.to_string_lossy().contains("/deps/")).map(|p| p.display().to_string()).unwrap_or_else(|| "sonata2-rs".into())
}

#[cfg(test)]
pub(crate) mod testenv;

/// The parts with windows (tests/test_blackout.py, tests/test_setup.py),
/// on a display when there is one. One test: GTK lives on one thread.
#[cfg(test)]
mod gtk_tests {
    use gtk::prelude::*;

    fn spin(ms: u64) {
        let end = std::time::Instant::now() + std::time::Duration::from_millis(ms);
        let ctx = glib::MainContext::default();
        while std::time::Instant::now() < end {
            ctx.iteration(false);
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
    }

    #[test]
    fn windows() {
        // (the display first: testenv moves XDG_RUNTIME_DIR, where Broadway's socket is)
        let _lock = crate::testenv::env();
        let shown = crate::testenv::with_runtime_dir_of_the_session(|| !(gtk::is_initialized() && !gtk::is_initialized_main_thread()) && gtk::init().is_ok() && gtk::gdk::Display::default().is_some());
        if !shown {
            return; // (no display)
        }
        let _ = adw::init();
        blackout_fades_in_and_out();
        blackout_shown_again_during_its_fade_out();
        setup_flow();
    }

    /// Vini: after the idle time the screen only dimmed and the pointer
    /// stayed on it. Now it fades to black on every display, without the
    /// pointer, and fades out again.
    fn blackout_fades_in_and_out() {
        use crate::blackout as b;
        b::show();
        assert!(b::surfaces() > 0);
        assert!(!b::showing()); // a transparent frame first: the fade shows
        spin(150);
        assert!(b::showing());
        let n = b::surfaces();
        b::show(); // again: nothing new
        assert_eq!(b::surfaces(), n);
        b::hide();
        assert!(!b::showing());
        assert_eq!(b::surfaces(), n); // still there while it fades out
        spin(b::FADE_OUT_MS + 150);
        assert_eq!(b::surfaces(), 0);
    }

    fn blackout_shown_again_during_its_fade_out() {
        use crate::blackout as b;
        b::show();
        spin(150);
        b::hide();
        spin(100);
        b::show(); // input stopped again right away
        spin(b::FADE_OUT_MS + 200);
        assert!(b::showing()); // the old fade-out took nothing away
        b::hide();
        spin(b::FADE_OUT_MS + 150);
        assert_eq!(b::surfaces(), 0);
    }

    /// Pages navigate, choices save, Get Started marks it done.
    fn setup_flow() {
        use crate::setup as s;
        use std::rc::Rc;
        let app = adw::Application::builder().application_id("io.test.sessionui.setup").flags(gio::ApplicationFlags::NON_UNIQUE).build();
        app.register(gio::Cancellable::NONE).unwrap();
        assert!(!s::done());
        let dark = Rc::new(std::cell::Cell::new(None));
        let d2 = dark.clone();
        let hooks = s::Hooks { set_dark_mode: Rc::new(move |on| d2.set(Some(on))), apply_titlebars: Rc::new(|_| {}) };
        let finished = Rc::new(std::cell::Cell::new(false));
        let f2 = finished.clone();
        let a = s::SetupAssistant::new(app.upcast_ref(), hooks, move || f2.set(true));
        spin(300);
        for name in &s::PAGES[1..] {
            a.go(1);
            spin(500);
            assert_eq!(a.stack.visible_child_name().as_deref(), Some(*name));
        }
        a.go(-1);
        assert_eq!(a.stack.visible_child_name().as_deref(), Some("sonata"));
        // Vini: the first setup asks how to see the apps
        let mut kinds: Vec<&str> = a.app_cards.borrow().keys().copied().collect();
        kinds.sort();
        assert_eq!(kinds, ["fullscreen", "window"]);
        a.set_apps_style("window");
        assert_eq!(s::apps_style(), "window");
        assert!(a.app_cards.borrow()["window"].has_css_class("selected"));
        assert!(!a.app_cards.borrow()["fullscreen"].has_css_class("selected"));
        a.set_apps_style("fullscreen");
        assert_eq!(s::apps_style(), "fullscreen");
        a.set_accent("green");
        assert_eq!(sonata_config::load("appearance", &serde_json::json!({"accent": ""}))["accent"], "green");
        a.set_dark(true);
        assert_eq!(dark.get(), Some(true));
        assert!(a.cards.borrow()[&true].has_css_class("selected"));
        a.finish();
        assert!(s::done()); // saved at once
        // the fade runs on the window's frame clock: slower on a busy display
        for _ in 0..40 {
            if finished.get() {
                break;
            }
            spin(100);
        }
        assert!(finished.get());
        a.win.destroy();
    }
}
