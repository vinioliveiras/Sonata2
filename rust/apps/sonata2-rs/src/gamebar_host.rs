//! The Game Bar's side of the menu bar (sonata_gamebar::gamebar::Host):
//! what the Python GameBar(app, bar) reaches through the bar -- the
//! capture and the instant replay, the Sound menu's mixer, Control
//! Center's quick controls, Now Playing and graphs, the recording pill's
//! pen, the game's picture for the glass, the app in front.
//!
//! The Game Bar is built inside the menu bar's process (host.rs), but the
//! menu bar crate can't depend on the Game Bar's, so the pieces are handed
//! over here. The controllers are the menu bar's gamepad service
//! (sonata-gamepad): grab_gamepads hands them to the Game Bar and back.

use gtk::prelude::*;
use gtk::{gdk, gsk};
use sonata_capture::capture::{self as cap, Capture};
use sonata_gamebar::gamebar::{self as gb, PenStart, QuickParts};
use sonata_topbar::cc::QuickControls;
use sonata_topbar::controls::ModuleSlider;
use sonata_topbar::mixer_ui::{AppMixer, Stream};
use sonata_topbar::seams::Mixer;
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};

/// What the Game Bar's controller code takes for one of Control Center's
/// capsule sliders (gamebar.rs: is_module_slider): the D-pad's left /
/// right move it, and it shows the focus ring.
const MODULE_SLIDER: &str = "sonata-module-slider";

pub fn host(app: &gtk::Application, capture: Weak<Capture>, grab: Rc<dyn Fn(bool)>) -> Rc<dyn gb::Host> {
    let mut h = MenuBarHost::new(app, capture);
    h.grab = Some(grab);
    Rc::new(h)
}

struct MenuBarHost {
    app: gtk::Application,
    capture: Weak<Capture>,
    /// backend/mixer.py through the menu bar's seam; None without pactl
    mixer: Option<Mixer>,
    apps: Rc<RefCell<Option<AppMixer>>>,
    /// the mixer service's listener while the Audio widget is on screen
    listening: Cell<Option<u64>>,
    quick: RefCell<Option<Rc<QuickControls>>>,
    /// the gamepad service's grab (on: the controllers drive the Game Bar)
    grab: Option<Rc<dyn Fn(bool)>>,
}

impl MenuBarHost {
    fn new(app: &gtk::Application, capture: Weak<Capture>) -> Self {
        let mixer = Some(sonata_topbar::seams::system_mixer()).filter(|m| (m.available)());
        MenuBarHost { app: app.clone(), capture, mixer, apps: Rc::default(), listening: Cell::new(None), quick: RefCell::default(), grab: None }
    }
}

/// A slider the controller stops on and moves (gamebar.py sets
/// focusable / can_focus on each).
fn reachable(s: &ModuleSlider) {
    s.add_css_class(MODULE_SLIDER);
    s.set_focusable(true);
    s.set_can_focus(true);
}

/// The apps playing sound in the Game Bar's mixer, their sliders and mute
/// buttons reachable with the controller (gamebar.py's _streams).
fn show_streams(apps: &AppMixer, streams: &[Stream]) {
    apps.set_streams(streams);
    for key in apps.keys() {
        if let Some(row) = apps.row(&key) {
            reachable(&row.slider);
            row.mute.set_can_focus(true);
        }
    }
}

impl gb::Host for MenuBarHost {
    fn grab_gamepads(&self, on: bool) {
        if let Some(g) = &self.grab {
            g(on);
        }
    }

    fn front_app(&self) -> Option<(String, String)> {
        // asked as the Game Bar opens, before it's on screen: the focused
        // window is still the game's
        cap::front_app(&cap::views())
    }

    fn capture(&self) -> Option<Rc<dyn gb::Capture>> {
        let c = self.capture.upgrade()?;
        Some(Rc::new(CaptureLink(c)))
    }

    fn app_mixer(&self) -> Option<gtk::Widget> {
        let m = self.mixer.as_ref()?;
        let apps = AppMixer::new(m.apply.clone(), m.pick.clone(), None);
        let w = apps.widget().clone().upcast();
        self.apps.replace(Some(apps));
        Some(w)
    }

    fn mixer_listen(&self, on: bool) {
        let Some(m) = self.mixer.as_ref() else { return };
        if self.apps.borrow().is_none() {
            return;
        }
        if !on {
            if let (Some(id), Some(un)) = (self.listening.take(), m.unlisten.as_ref()) {
                un(id);
            }
            return;
        }
        // the streams as they are now (each opening: changed meanwhile),
        // then followed while the Audio widget is on screen
        let (apps, f) = (Rc::downgrade(&self.apps), m.streams.clone());
        sonata_topbar::kit::run_async(move || f(), move |found| {
            if let Some(a) = apps.upgrade().and_then(|a| a.borrow().clone()) {
                show_streams(&a, &found);
            }
        });
        if let (None, Some(listen)) = (self.listening.get(), m.listen.as_ref()) {
            let apps = Rc::downgrade(&self.apps);
            self.listening.set(Some(listen(Rc::new(move |found: &[Stream]| {
                if let Some(a) = apps.upgrade().and_then(|a| a.borrow().clone()) {
                    show_streams(&a, found);
                }
            }))));
        }
    }

    fn quick_controls(&self, open_displays: Rc<dyn Fn()>) -> Option<QuickParts> {
        let q = QuickControls::new(open_displays);
        for t in [&q.wifi, &q.bt, &q.plane, &q.dnd] {
            t.button.set_can_focus(true); // the controller reaches them
        }
        reachable(&q.bright);
        let parts = QuickParts { radios: q.radios_module().upcast(), dnd: q.dnd_module().upcast(), display: q.display.clone() };
        self.quick.replace(Some(q));
        Some(parts)
    }

    fn read_controls(&self, output: Option<&str>) {
        if let Some(q) = self.quick.borrow().as_ref() {
            q.read(output.map(String::from));
        }
    }

    fn now_playing(&self) -> Option<gtk::Widget> {
        // the process's players, the menu bar's too; follows them only while mapped
        Some(sonata_topbar::bar::now_playing_module(&sonata_topbar::mpris::players(), false).upcast())
    }

    fn stats_module(&self, kind: &str) -> Option<gtk::Widget> {
        Some(sonata_topbar::statsui::module(kind).upcast())
    }

    fn stats_kinds(&self) -> Vec<String> {
        sonata_topbar::statsui::kinds().to_vec()
    }

    fn pen_button(&self, start: PenStart) -> Option<gtk::Widget> {
        let b = sonata_capture::livedraw::pen_button_with(&self.app, Rc::new(cap::focused_output), "gb-toggle", 16, Some(start));
        b.set_focus_on_click(false);
        b.set_can_focus(true);
        Some(b.upcast())
    }

    fn step_slider(&self, slider: &gtk::Widget, delta: f64) -> bool {
        let Some(s) = slider.downcast_ref::<ModuleSlider>() else { return false };
        s.set_value(s.value() + delta);
        true
    }

    fn frost(&self, output: Option<&str>, done: Box<dyn FnOnce(Option<gdk::Texture>)>, renderer_of: Box<dyn Fn() -> Option<gsk::Renderer>>) -> bool {
        // (the menu bar hosts the Game Bar only on a layer-shell display:
        // host.rs installs it for the layered main bar alone). grim read
        // off the main loop (gb::output_off_loop: with frost::capture's
        // main-loop read, a busy moment of the menu bar made the picture
        // late and the Game Bar opened solid); decoded and blurred here
        use sonata_mission::frost;
        if !frost::available() {
            return false;
        }
        gb::output_off_loop(frost::frost_command(output), move |out| {
            let tex = frost::texture_from(out.as_ref());
            // never the picture unblurred: without a renderer, no glass this time
            let renderer = renderer_of();
            done(match (tex, renderer) {
                (Some(t), Some(r)) => Some(frost::blur_texture(Some(&r), &t)),
                _ => None,
            });
        })
    }
}

/// The menu bar's Capture as the Game Bar sees it.
struct CaptureLink(Rc<Capture>);

impl gb::Capture for CaptureLink {
    fn recording(&self) -> bool {
        self.0.recording()
    }

    fn recording_since_us(&self) -> i64 {
        self.0.recording_since_us()
    }

    fn screenshot(&self, output: Option<&str>) {
        self.0.shoot_output(output);
    }

    fn record(&self, output: Option<&str>) {
        self.0.record_output(output);
    }

    fn stop_recording(&self) {
        self.0.stop_recording();
    }

    fn replay_available(&self) -> bool {
        sonata_capture::replay::Replay::available()
    }

    fn replay_running(&self) -> bool {
        self.0.replay().running()
    }

    fn replay_start(&self, output: Option<&str>) {
        self.0.replay().start(output.map(String::from));
    }

    fn replay_stop(&self) {
        self.0.replay().stop();
    }

    fn replay_save(&self) {
        self.0.replay().save();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The Game Bar's controller moves Control Center's capsule sliders
    /// it's handed (the brightness, the apps' volumes): they carry the
    /// class its controller code looks for, and step_slider moves them.
    #[test]
    fn test_game_bar_sliders_reachable() {
        if gtk::init().is_err() {
            return; // no display: nothing to build
        }
        let s = ModuleSlider::new(40.0, 0.0, 100.0);
        reachable(&s);
        assert!(s.has_css_class(MODULE_SLIDER) && s.is_focusable());
        let h = MenuBarHost { app: gtk::Application::builder().build(), capture: Weak::new(), mixer: None, apps: Rc::default(), listening: Cell::new(None), quick: RefCell::default(), grab: None };
        assert!(gb::Host::step_slider(&h, s.upcast_ref(), 5.0));
        assert_eq!(s.value(), 45.0);
        assert!(!gb::Host::step_slider(&h, gtk::Label::new(None).upcast_ref(), 5.0));
        assert!(gb::Host::capture(&h).is_none()); // no menu bar capture: "not here"
    }
}
