//! What the menu bar uses that lives in Python modules not ported to Rust
//! yet, or in crates being written beside this one: each is a closure or a
//! trait here, set by whoever starts the bar (`run_with`). Left unset, the
//! bar does without (the item hidden, the switch insensitive) or uses a
//! plain fallback, said at each field.

use crate::mixer_ui::{ApplyFn, PickFn, Stream};
use std::rc::Rc;

/// An open window (wl/toplevels.py's Toplevel, as far as the bar needs it).
#[derive(Clone, Debug, PartialEq, Default)]
pub struct Toplevel {
    pub id: i64,
    pub app_id: String,
    pub title: String,
    pub activated: bool,
    pub minimized: bool,
    pub maximized: bool,
}

/// The windows (wlr-foreign-toplevel in Python; the Dock's
/// `toplevels::Manager` over Wayfire's IPC in Rust is one).
pub trait Windows {
    fn toplevels(&self) -> Vec<Toplevel>;
    /// listener() after every change; the id undoes it.
    fn add_listener(&self, f: Rc<dyn Fn()>) -> u64;
    fn remove_listener(&self, id: u64);
    fn activate(&self, t: &Toplevel);
    fn minimize(&self, t: &Toplevel);
    fn unminimize(&self, t: &Toplevel);
    fn close(&self, t: &Toplevel);
    fn set_maximized(&self, t: &Toplevel, on: bool);
}

/// The apps' own shortcuts for windows and tabs (shell/apptabs.py).
pub trait AppTabs {
    /// The app's kind of tabs (None: no tabs: no tab items in Window).
    fn kind(&self, app_id: &str) -> Option<String>;
    /// Whether the app has a shortcut for `action` (new_window, new_tab...).
    fn has(&self, app_id: &str, action: &str) -> bool;
    fn press(&self, app_id: &str, action: &str);
}

/// backend/mixer.py: each app's volume (pactl) and the saved levels.
#[derive(Clone)]
pub struct Mixer {
    pub available: Rc<dyn Fn() -> bool>,
    /// The streams playing now (blocks: run in a thread).
    pub streams: std::sync::Arc<dyn Fn() -> Vec<Stream> + Send + Sync>,
    pub apply: ApplyFn,
    pub pick: PickFn,
    /// listener(streams) whenever apps start / stop playing (MixerService); the id undoes it.
    pub listen: Option<Rc<dyn Fn(Rc<dyn Fn(&[Stream])>) -> u64>>,
    pub unlisten: Option<Rc<dyn Fn(u64)>>,
}

/// The seams. `Services::default()`: none of them.
#[derive(Clone, Default)]
pub struct Services {
    pub windows: Option<Rc<dyn Windows>>,
    pub apptabs: Option<Rc<dyn AppTabs>>,
    pub mixer: Option<Mixer>,
    /// quiet.py's paused(): locked, or a fullscreen game has the focus (no polling then). Unset: never.
    pub quiet_paused: Option<Rc<dyn Fn() -> bool>>,
    /// Night Shift: (is_on, set_manual) -- sonata_sessionui::nightshift. Unset: its button insensitive.
    pub night_shift: Option<(Rc<dyn Fn() -> bool>, Rc<dyn Fn(bool)>)>,
    /// system.set_dark_mode (color-scheme, GTK 3 theme, gtkstyle). Unset:
    /// only Sonata's own color-scheme preference, through gsettings.
    pub set_dark_mode: Option<std::sync::Arc<dyn Fn(bool) + Send + Sync>>,
    /// quitapps.end_session(kind, reopen) -- sonata_sessionui::quitapps. Unset: system.power_action straight away.
    pub end_session: Option<Rc<dyn Fn(&str, bool)>>,
    /// quitapps.quit_app(windows, app key). Unset: each window closed.
    pub quit_app: Option<Rc<dyn Fn(&[Toplevel], &str)>>,
    /// files.open_folder(uri). Unset: `sonata2 files <uri>` (the Python Files).
    pub open_folder: Option<Rc<dyn Fn(&str)>>,
    /// files.ops.new_folder on the desktop. Unset: the item is disabled.
    pub new_desktop_folder: Option<Rc<dyn Fn()>>,
    /// capture.Capture.show_toolbar (Control Center's Screenshot). Unset: `sonata2-rs screenshot area`.
    pub capture_toolbar: Option<Rc<dyn Fn()>>,
    /// gamemode.boosted() / set_boosted(False) for the Energy Mode menu.
    pub power_boosted: Option<(Rc<dyn Fn() -> bool>, Rc<dyn Fn(bool)>)>,
    /// powerprofile.remember(key): back to it when plugged in again.
    pub remember_profile: Option<Rc<dyn Fn(&str)>>,
    /// Control Center modules made elsewhere: "fpslimit" (fpsmodule.py),
    /// "keyboard" (kbdmodule.py, gamebar crate). Each builds a new widget.
    pub extra_modules: Vec<(String, Rc<dyn Fn() -> gtk::Widget>)>,
}

thread_local! {
    /// backend/mixer.py's MixerService: one for the process, whoever asks.
    static SERVICE: std::cell::RefCell<Option<sonata_system::mixer::MixerService>> = const { std::cell::RefCell::new(None) };
}

/// The process's MixerService (made at the first ask).
fn mixer_service() -> sonata_system::mixer::MixerService {
    SERVICE.with(|s| s.borrow_mut().get_or_insert_with(sonata_system::mixer::MixerService::new).clone())
}

/// The menu bar's mixer service from the start (topbar.py makes it with the
/// main display's bar): each app's saved volume given back to its new
/// streams at once, and a level set in the app learned. The Rust bar made
/// it only when the Sound menu or Control Center's mixer first opened --
/// until then apps started at whatever level (Vini: "volume per app,
/// always kept"). Stopped with the app.
pub fn start_mixer_service(app: &gtk::Application) {
    if !sonata_system::mixer::available() {
        return;
    }
    let svc = mixer_service();
    gtk::gio::prelude::ApplicationExt::connect_shutdown(app, move |_| svc.stop());
}

/// The Mixer seam filled by sonata-system (backend/mixer.py): pactl's
/// streams and saved levels, the output picker, MixerService's listeners.
/// Every Mixer shares the process's one service (the Game Bar's too).
pub fn system_mixer() -> Mixer {
    use sonata_system::mixer as m;
    Mixer {
        available: Rc::new(|| glib::find_program_in_path("pactl").is_some()),
        streams: std::sync::Arc::new(m::streams),
        apply: Rc::new(|key, percent, muted, all| {
            m::set_level(key, percent.map(i64::from), muted, Some(all));
        }),
        pick: Rc::new(|button, key, all| {
            let Some(stream) = all.iter().find(|s| s.key == key).cloned() else { return };
            let (outputs, current) = m::choices(&stream);
            let items: Vec<sonata_ui::menu::Item> = outputs
                .into_iter()
                .map(|o| {
                    let on = current.as_ref().is_some_and(|c| c.name == o.name);
                    let (key, all) = (key.to_string(), all.to_vec());
                    sonata_ui::menu::Item::check(&o.label, on, move |_| {
                        m::move_to(&key, &o.name, Some(&all));
                    })
                })
                .collect();
            if !items.is_empty() {
                sonata_ui::menu::popup(button, vec![items], sonata_ui::menu::Options::below());
            }
        }),
        listen: Some(Rc::new(move |f| mixer_service().add_listener(move |streams| f(streams)))),
        unlisten: Some(Rc::new(move |id| {
            if let Some(s) = SERVICE.with(|s| s.borrow().clone()) {
                s.remove_listener(id);
            }
        })),
    }
}

#[cfg(test)]
mod tests {
    /// Vini: an app's saved volume came back only after the Sound menu or
    /// Control Center's mixer had been opened once -- the service that
    /// gives it back was made on that first opening. The menu bar starts
    /// it with the main display's bar (run_with), as topbar.py does.
    #[test]
    fn test_saved_volumes_waited_for_the_mixer() {
        let src = include_str!("lib.rs");
        let at = src.find("if !opts.preview {\n            // the menu bar's services").unwrap();
        assert!(src[at..at + 800].contains("seams::start_mixer_service(app)"));
    }
}
