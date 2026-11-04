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

/// The Mixer seam filled by sonata-system (backend/mixer.py): pactl's
/// streams and saved levels, the output picker, MixerService's listeners.
pub fn system_mixer() -> Mixer {
    use sonata_system::mixer as m;
    let service: Rc<std::cell::RefCell<Option<m::MixerService>>> = Rc::default();
    let s2 = service.clone();
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
        listen: Some(Rc::new(move |f| {
            let mut s = service.borrow_mut();
            let svc = s.get_or_insert_with(m::MixerService::new);
            svc.add_listener(move |streams| f(streams))
        })),
        unlisten: Some(Rc::new(move |id| {
            if let Some(s) = s2.borrow().as_ref() {
                s.remove_listener(id);
            }
        })),
    }
}
