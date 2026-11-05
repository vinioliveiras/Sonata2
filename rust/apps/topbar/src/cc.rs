//! Control Center (topbar.py's ControlCenter and QuickControls): modules on
//! a 4-column grid (ccgrid.rs) -- the connectivity module (Wi-Fi,
//! Bluetooth, Airplane Mode), Do Not Disturb, Dark Mode, Screenshot,
//! Display (brightness, Night Shift) and Sound sliders, Now Playing; on
//! request the Volume Mixer, FPS Limit, Keyboard and the performance
//! figures. Which ones and their order: the user's (Edit Controls…, like
//! Launchpad). Opens at once; states fill in from background reads.
//!
//! Not ported here: the Game Bar's copy of the quick controls (it builds
//! its own), the FPS Limit and Keyboard modules (passed in:
//! Services::extra_modules).

use crate::bar::{self, Bar};
use crate::ccgrid::{self, ModuleGrid};
use crate::controls::{round_button, slider_with_icon, speaker_icon, ModuleSlider};
use crate::kit::{self, run_async, Toggle};
use crate::logic;
use crate::statsui;
use gtk::{gdk, glib, prelude::*};
use sonata_backend::system;
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};
use std::sync::{Arc, Mutex};

thread_local! {
    /// The last levels read: {"vol", "mic", "bright:<output>"} -- a panel
    /// opens at them (no jump as it opens, Vini), then the live ones.
    static LEVELS: RefCell<HashMap<String, f64>> = RefCell::default();
}

pub fn level(key: &str) -> f64 {
    LEVELS.with(|l| l.borrow().get(key).copied()).unwrap_or(50.0)
}

pub fn remember_level(key: &str, v: f64) {
    LEVELS.with(|l| l.borrow_mut().insert(key.to_string(), v));
}

fn bright_key(output: &Option<String>) -> String {
    format!("bright:{}", output.as_deref().unwrap_or(""))
}

/// Wi-Fi on, the joined network, Bluetooth, Airplane Mode -- Control
/// Center's toggles in one read.
pub type CcState = (bool, (String, i64, bool), Option<bool>, Option<bool>);

pub fn cc_state() -> CcState {
    (system::wifi_enabled(), system::wifi_current(), system::bluetooth_state(), system::airplane_mode())
}

/// Only the newest value is applied, one at a time (a slider dragged over
/// a DDC/CI monitor that answers in seconds: system.run_latest).
#[derive(Clone, Default)]
struct Latest(Arc<Mutex<(Option<i64>, bool)>>);

impl Latest {
    fn send(&self, v: i64, apply: impl Fn(i64) + Send + 'static) {
        let mut st = self.0.lock().unwrap_or_else(|e| e.into_inner());
        st.0 = Some(v);
        if st.1 {
            return;
        }
        st.1 = true;
        let me = self.clone();
        kit::in_thread(move || loop {
            let next = {
                let mut st = me.0.lock().unwrap_or_else(|e| e.into_inner());
                let n = st.0.take();
                if n.is_none() {
                    st.1 = false;
                }
                n
            };
            match next {
                Some(v) => apply(v),
                None => break,
            }
        });
    }
}

/// The radios as read (Control Center's and the Game Bar's alike).
fn show_toggles(wifi: &Toggle, bt: &Toggle, plane: &Toggle, s: &CcState) {
    let (wifi_on, (ssid, _, _), bt_on, plane_on) = s;
    let cap = if !ssid.is_empty() { ssid.clone() } else if *wifi_on { tr!("Not Connected") } else { tr!("Off") };
    wifi.set(*wifi_on, Some(&cap));
    bt.set(*bt_on == Some(true), Some(&logic::on_off(*bt_on)));
    plane.set(*plane_on == Some(true), Some(&logic::on_off(*plane_on)));
    plane.button.set_sensitive(plane_on.is_some());
}

/// Airplane Mode pressed: shown at once, Wi-Fi and Bluetooth going off with it.
fn show_airplane(wifi: &Toggle, bt: &Toggle, plane: &Toggle, on: bool) {
    plane.set(on, Some(&logic::on_off(Some(on))));
    if on {
        // they go off with it, at once
        wifi.set(false, Some(&tr!("Off")));
        bt.set(false, Some(&tr!("Off")));
    }
}

/// `output`'s brightness as read; None: it can't be changed from here.
fn show_brightness(bright: &ModuleSlider, output: &Option<String>, b: Option<i64>) {
    let Some(b) = b else {
        bright.set_sensitive(false);
        if !system::is_builtin(output.as_deref()) {
            bright.set_tooltip_text(Some(&tr!("This display's brightness can't be changed from the computer (turn on DDC/CI in its menu; needs ddcutil)")));
        }
        return;
    };
    bright.set_sensitive(true);
    bright.set_tooltip_text(None);
    remember_level(&bright_key(output), b as f64);
    bright.show_value(b as f64); // (not set again: only shown)
}

pub struct ControlCenter {
    pub root: gtk::Box,
    bar: Weak<Bar>,
    pub width: i32,
    pub output: Option<String>,
    pub wifi: Toggle,
    pub bt: Toggle,
    pub plane: Toggle,
    pub dnd: Toggle,
    pub bright: ModuleSlider,
    pub vol: ModuleSlider,
    pub mic: ModuleSlider,
    pub dark_btn: gtk::Button,
    ns: gtk::ToggleButton,
    ns_handler: RefCell<Option<glib::SignalHandlerId>>,
    pub modules: RefCell<HashMap<String, gtk::Widget>>,
    pub grid: ModuleGrid,
    scroller: gtk::ScrolledWindow,
    pub pages: gtk::Stack,
    pub edit_btn: gtk::Button,
    pub add_btn: gtk::Button,
    pub done_btn: gtk::Button,
    pub add_list: gtk::FlowBox,
    add_reveal: gtk::Revealer,
    warm: Cell<bool>,
    has_mixer: bool,
    /// (kept with it: the slider's applying thread)
    _brightness: Latest,
}

/// The display this menu bar is on (else the first one).
fn monitor_of(bar: &Bar) -> Option<gdk::Monitor> {
    bar.monitor.borrow().clone().or_else(|| sonata_shell::layer::monitors().into_iter().next())
}

fn max_height(mon: &Option<gdk::Monitor>) -> i32 {
    let h = mon.as_ref().map_or(900, |m| m.geometry().height());
    (h - bar::bar_h() - 120).max(300)
}

fn small(icon: &str, title: &str) -> gtk::Button {
    let b = gtk::Button::builder().css_classes(["panel-module", "cc-small"]).can_focus(false).build();
    let col = gtk::Box::new(gtk::Orientation::Vertical, 4);
    col.append(&gtk::Image::builder().icon_name(icon).pixel_size(18).build());
    col.append(&gtk::Label::builder().label(title).wrap(true).justify(gtk::Justification::Center).build());
    b.set_child(Some(&col));
    b
}

fn set_class(w: &impl IsA<gtk::Widget>, class: &str, on: bool) {
    if on {
        w.add_css_class(class);
    } else {
        w.remove_css_class(class);
    }
}

impl ControlCenter {
    pub fn new(bar: &Rc<Bar>, warm: bool) -> Rc<ControlCenter> {
        let mon = monitor_of(bar);
        let output = mon.as_ref().and_then(|m| m.connector()).map(|c| c.to_string());
        let width = ccgrid::width_for(mon.as_ref().map_or(0, |m| m.geometry().width()));
        let me = Rc::new_cyclic(|weak: &Weak<ControlCenter>| {
            let b = Rc::downgrade(bar);
            let wifi = Toggle::new("network-wireless-symbolic", &tr!("Wi-Fi"), false, |on| run_async(move || system::set_wifi_enabled(on), |_| poll_all()), "…");
            let bt = Toggle::new("bluetooth-active-symbolic", &tr!("Bluetooth"), false, |on| kit::in_thread(move || {
                system::set_bluetooth(on);
            }), "…");
            // where macOS has AirDrop (Vini): Airplane Mode -- every radio off, and back as they were
            let w = weak.clone();
            let plane = Toggle::new("airplane-mode-symbolic", &tr!("Airplane Mode"), false, move |on| {
                if let Some(cc) = w.upgrade() {
                    cc.set_airplane(on);
                }
            }, "…");
            let nc = bar.notifications.borrow().clone();
            let bw = b.clone();
            let dnd = Toggle::new("weather-clear-night-symbolic", &tr!("Do Not Disturb"), nc.as_ref().is_some_and(|n| n.dnd()), move |on| {
                if let Some(nc) = bw.upgrade().and_then(|b| b.notifications.borrow().clone()) {
                    nc.set_dnd(on);
                }
            }, "");
            // the brightness of the display this menu bar is on: the laptop
            // panel, or an external monitor over DDC/CI
            let latest = Latest::default();
            let (l, out) = (latest.clone(), output.clone());
            let w = weak.clone();
            let disp_btn = round_button("video-display-symbolic", &tr!("Displays Preferences"), move |_| {
                if let Some(cc) = w.upgrade() {
                    cc.close();
                }
                kit::open_settings("displays");
            });
            let (disp, bright) = slider_with_icon(|_| "display-brightness-symbolic".into(), level(&bright_key(&output)), move |v| {
                let out = out.clone();
                l.send(v as i64, move |v| {
                    system::set_brightness(v, out.as_deref());
                });
            }, true, Some(&disp_btn));
            let conn = kit::module(&[wifi.root.upcast_ref(), bt.root.upcast_ref(), plane.root.upcast_ref()], 2);
            conn.add_css_class("cc-conn"); // three rows in its 2x2 cells,
            conn.set_homogeneous(true); // spread evenly over its height
            for t in [&wifi, &bt, &plane] {
                t.root.set_valign(gtk::Align::Center); // (Vini: packed at the top, a gap below)
            }
            conn.set_valign(gtk::Align::Fill);
            let dnd_mod = kit::module(&[dnd.root.upcast_ref()], 8);
            kit::click_anywhere(&dnd_mod, &dnd);
            let dark_btn = small("sonata-dark-mode-symbolic", &tr!("Dark Mode"));
            set_class(&dark_btn, "on", adw::StyleManager::default().is_dark());
            let (w, setter) = (weak.clone(), bar.services.set_dark_mode.clone());
            dark_btn.connect_clicked(move |_| {
                let on = !adw::StyleManager::default().is_dark();
                if let Some(cc) = w.upgrade() {
                    set_class(&cc.dark_btn, "on", on);
                }
                // Dark Mode is a Linux setting (freedesktop colour-scheme), so
                // every app follows -- and Sonata with them
                let s = setter.clone();
                kit::in_thread(move || match s {
                    Some(f) => f(on),
                    None => {
                        sonata_backend::run::ok(&["gsettings", "set", "org.gnome.desktop.interface", "color-scheme", if on { "prefer-dark" } else { "default" }], 5);
                    }
                });
            });
            let shot = small("sonata-screenshot-symbolic", &tr!("Screenshot")); // opens the toolbar
            let (w, cap) = (weak.clone(), bar.services.capture_toolbar.clone());
            shot.connect_clicked(move |_| {
                if let Some(cc) = w.upgrade() {
                    cc.close();
                }
                // after the panel closes
                let cap = cap.clone();
                glib::timeout_add_local_once(std::time::Duration::from_millis(300), move || match cap {
                    Some(f) => f(),
                    None => kit::spawn_self(&["screenshot", "area"]),
                });
            });
            // Night Shift under the brightness slider (Big Sur's expanded Display module)
            let ns_seam = bar.services.night_shift.clone();
            let ns = gtk::ToggleButton::builder().css_classes(["cc-ns"]).can_focus(false).active(ns_seam.as_ref().is_some_and(|(on, _)| on())).halign(gtk::Align::Start).build();
            let ns_box = gtk::Box::new(gtk::Orientation::Horizontal, 8);
            ns_box.append(&gtk::Image::builder().icon_name("night-light-symbolic").css_classes(["cc-ns-icon"]).build());
            ns_box.append(&gtk::Label::new(Some(&tr!("Night Shift"))));
            ns.set_child(Some(&ns_box));
            let set_ns = ns_seam.as_ref().map(|s| s.1.clone());
            let ns_handler = ns.connect_toggled(move |b| {
                if let Some(f) = set_ns.as_ref() {
                    f(b.is_active());
                }
            });
            ns.set_sensitive(ns_seam.is_some() && sonata_backend::run::which("wlsunset").is_some());
            let display = kit::module(&[kit::module_title(&tr!("Display")).upcast_ref(), &disp, ns.upcast_ref()], 8);
            let bv = b.clone();
            let out_btn = round_button("sonata-audio-output-symbolic", &tr!("Output"), |btn| bar::device_menu(btn, &tr!("Output"), false));
            let (snd, vol) = slider_with_icon(|v| speaker_icon(v).to_string(), level("vol"), move |v| bv.upgrade().iter().for_each(|b| b.set_volume(v)), true, Some(&out_btn));
            let in_btn = round_button("audio-input-microphone-symbolic", &tr!("Input"), |btn| bar::device_menu(btn, &tr!("Input"), true));
            let (mic_row, mic) = slider_with_icon(
                |v| if v <= 0.0 { "microphone-disabled-symbolic".into() } else { "audio-input-microphone-symbolic".into() },
                level("mic"),
                |v| kit::in_thread(move || {
                    system::set_input_volume(Some(v as i64), Some(false));
                }),
                true,
                Some(&in_btn),
            );
            let sound = kit::module(&[kit::module_title(&tr!("Sound")).upcast_ref(), &snd, &mic_row], 8);
            let mut modules: HashMap<String, gtk::Widget> = HashMap::from([
                ("connectivity".to_string(), conn.upcast::<gtk::Widget>()),
                ("dnd".into(), dnd_mod.upcast()),
                ("darkmode".into(), dark_btn.clone().upcast()),
                ("screenshot".into(), shot.upcast()),
                ("display".into(), display.upcast()),
                ("sound".into(), sound.upcast()),
                // always, like Big Sur ("Not Playing")
                ("nowplaying".into(), bar::now_playing_module(&crate::mpris::players(), false).upcast()),
            ]);
            let mixer = bar.services.mixer.clone().filter(|m| (m.available)());
            let has_mixer = mixer.is_some();
            if let Some(m) = mixer {
                modules.insert("mixer".into(), mixer_module(m, weak.clone()).upcast());
            }
            for (id, make) in &bar.services.extra_modules {
                modules.insert(id.clone(), make());
            }
            for kind in statsui::kinds() {
                modules.insert(format!("stat_{kind}"), statsui::module(kind).upcast());
            }
            let order = ccgrid::load();
            let grid = ModuleGrid::new(modules.clone(), &order, width);
            // fixed width (EXTERNAL keeps a too-wide child from widening it,
            // and shows no bar); the height follows the modules, and scrolls
            // only past what the screen holds
            let scroller = gtk::ScrolledWindow::builder().child(&grid).hscrollbar_policy(gtk::PolicyType::External).vscrollbar_policy(gtk::PolicyType::Automatic).overlay_scrolling(true).propagate_natural_width(false).propagate_natural_height(true).max_content_height(max_height(&mon)).width_request(width).css_classes(["cc-scroller"]).build();
            let main = gtk::Box::new(gtk::Orientation::Vertical, 8);
            main.append(&scroller);
            let w = weak.clone();
            let edit_btn = sonata_ui::controls::push_button(&tr!("Edit Controls…"), move || w.upgrade().iter().for_each(|c| c.grid.set_editing(true)), "");
            let w = weak.clone();
            let add_btn = sonata_ui::controls::push_button(&tr!("Add Controls"), move || w.upgrade().iter().for_each(|c| c.toggle_add_list()), "");
            let w = weak.clone();
            let done_btn = sonata_ui::controls::push_button(&tr!("Done"), move || w.upgrade().iter().for_each(|c| c.grid.set_editing(false)), "default");
            let edit_bar = gtk::Box::builder().spacing(8).halign(gtk::Align::Center).css_classes(["cc-edit-bar"]).build();
            for btn in [&edit_btn, &add_btn, &done_btn] {
                edit_bar.append(btn);
            }
            // what was taken out, to put back: a list in the panel itself (a
            // menu opened from the panel was a pop-up inside a pop-up: once it
            // closed, Control Center couldn't be closed, Vini). Two per row,
            // names cut short if need be: never wider than the panel
            let add_list = gtk::FlowBox::builder().selection_mode(gtk::SelectionMode::None).homogeneous(true).column_spacing(6).row_spacing(6).min_children_per_line(2).max_children_per_line(2).halign(gtk::Align::Fill).css_classes(["cc-add-list"]).build();
            let add_reveal = gtk::Revealer::builder().child(&add_list).reveal_child(false).transition_type(gtk::RevealerTransitionType::SlideUp).transition_duration(200).build();
            let edit_box = gtk::Box::new(gtk::Orientation::Vertical, 6);
            edit_box.append(&add_reveal);
            edit_box.append(&edit_bar);
            main.append(&edit_box);
            let pages = gtk::Stack::builder().transition_type(gtk::StackTransitionType::SlideLeftRight).transition_duration(220).vhomogeneous(false).interpolate_size(true).build();
            pages.add_named(&main, Some("main"));
            let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).width_request(320).build();
            root.append(&pages);
            ControlCenter {
                root,
                bar: b,
                width,
                output,
                wifi,
                bt,
                plane,
                dnd,
                bright,
                vol,
                mic,
                dark_btn,
                ns,
                ns_handler: RefCell::new(Some(ns_handler)),
                modules: RefCell::new(modules),
                grid,
                scroller,
                pages,
                edit_btn,
                add_btn,
                done_btn,
                add_list,
                add_reveal,
                warm: Cell::new(warm),
                has_mixer,
                _brightness: latest,
            }
        });
        let w = Rc::downgrade(&me);
        me.grid.set_on_change(move || {
            if let Some(cc) = w.upgrade() {
                cc.edit_bar_update();
                cc.fit_height();
            }
        });
        let w = Rc::downgrade(&me);
        me.grid.set_on_height(move || {
            if let Some(cc) = w.upgrade() {
                cc.fit_height(); // its size changes smoothly
            }
        });
        me.fit_height();
        me.edit_bar_update();
        for (t, kind) in [(&me.wifi, "wifi"), (&me.bt, "bluetooth")] {
            me.opens_details(t, kind);
        }
        me.read_states();
        me
    }

    fn close(&self) {
        if let Some(p) = self.root.ancestor(gtk::Popover::static_type()).and_downcast::<gtk::Popover>() {
            p.popdown();
        }
    }

    fn read_states(self: &Rc<Self>) {
        // Wi-Fi / Bluetooth: last known at once (unseen: what the menu bar's
        // prefetch just read is enough; its first opening reads them again)
        let w = Rc::downgrade(self);
        kit::cached("cc", cc_state, move |s: &CcState| w.upgrade().iter().for_each(|c| c.fill_toggles(s)), !self.warm.replace(false));
        // levels: the live ones, sound apart from brightness (a DDC/CI
        // monitor answers in seconds; the sound sliders don't wait for it)
        let w = Rc::downgrade(self);
        run_async(|| (system::volume(), system::input_volume()), move |(v, m)| w.upgrade().iter().for_each(|c| c.fill_sound(v, m)));
        let (w, out) = (Rc::downgrade(self), self.output.clone());
        run_async(move || system::brightness(out.as_deref()), move |b| w.upgrade().iter().for_each(|c| c.fill_brightness(b)));
    }

    pub fn fill_toggles(&self, s: &CcState) {
        show_toggles(&self.wifi, &self.bt, &self.plane, s);
    }

    /// Every radio off (or back as it was); Wi-Fi and Bluetooth show it.
    fn set_airplane(self: &Rc<Self>, on: bool) {
        show_airplane(&self.wifi, &self.bt, &self.plane, on);
        let w = Rc::downgrade(self);
        run_async(move || system::set_airplane_mode(on), move |_| {
            poll_all();
            run_async(cc_state, move |s| w.upgrade().iter().for_each(|c| c.fill_toggles(&s)));
        });
    }

    pub fn fill_brightness(&self, b: Option<i64>) {
        show_brightness(&self.bright, &self.output, b);
    }

    pub fn fill_sound(&self, vol: Option<(i64, bool)>, mic: Option<(i64, bool)>) {
        for (slider, key, v) in [(&self.vol, "vol", vol), (&self.mic, "mic", mic)] {
            match v {
                None => slider.set_sensitive(false),
                Some((v, _)) => {
                    remember_level(key, v as f64);
                    slider.show_value(v as f64);
                }
            }
        }
    }

    /// Up to date for another opening, as a new one would open: every state
    /// read again, on its controls (not a Wi-Fi list or Edit Controls left
    /// open). False: it no longer fits -- its layout changed elsewhere
    /// (Settings), another display or size -- and a new one is needed.
    pub fn refresh(self: &Rc<Self>) -> bool {
        let Some(bar) = self.bar.upgrade() else { return false };
        let mon = monitor_of(&bar);
        let modules = self.modules.borrow().clone();
        let has_mixer_now = bar.services.mixer.as_ref().is_some_and(|m| (m.available)());
        if ccgrid::load().into_iter().filter(|m| modules.contains_key(m)).collect::<Vec<_>>() != self.grid.order()
            || ccgrid::width_for(mon.as_ref().map_or(0, |m| m.geometry().width())) != self.width
            || max_height(&mon) != self.scroller.max_content_height()
            || mon.as_ref().and_then(|m| m.connector()).map(|c| c.to_string()) != self.output
            || has_mixer_now != self.has_mixer
        {
            return false;
        }
        if self.pages.visible_child_name().as_deref() != Some("main") {
            self.pages.set_transition_type(gtk::StackTransitionType::None);
            self.pages.set_visible_child_name("main");
            self.pages.set_transition_type(gtk::StackTransitionType::SlideLeftRight);
        }
        if let Some(old) = self.pages.child_by_name("details") {
            self.pages.remove(&old);
        }
        self.grid.set_editing(false);
        self.add_reveal.set_reveal_child(false);
        self.add_btn.remove_css_class("on");
        self.edit_bar_update();
        self.scroller.vadjustment().set_value(0.0);
        let nc = bar.notifications.borrow().clone();
        self.dnd.set(nc.is_some_and(|n| n.dnd()), None);
        set_class(&self.dark_btn, "on", adw::StyleManager::default().is_dark());
        if let Some((is_on, _)) = bar.services.night_shift.as_ref() {
            if let Some(h) = self.ns_handler.borrow().as_ref() {
                self.ns.block_signal(h); // shown, not set again
                self.ns.set_active(is_on());
                self.ns.unblock_signal(h);
            }
        }
        // the sliders at the last levels read, then the live ones
        for (slider, key) in [(&self.bright, bright_key(&self.output)), (&self.vol, "vol".into()), (&self.mic, "mic".into())] {
            slider.set_sensitive(true);
            slider.show_value(level(&key));
        }
        self.bright.set_tooltip_text(None);
        self.read_states();
        true
    }

    /// The row beside its toggle (its name and state) shows the list; the
    /// round button still turns it on and off.
    fn opens_details(self: &Rc<Self>, t: &Toggle, kind: &'static str) {
        t.texts.add_css_class("cc-more");
        t.texts.set_hexpand(true);
        t.texts.set_cursor(gdk::Cursor::from_name("pointer", None).as_ref());
        let click = gtk::GestureClick::builder().button(1).build();
        let w = Rc::downgrade(self);
        click.connect_released(move |_, _, _, _| w.upgrade().iter().for_each(|c| c.show_details(kind)));
        t.texts.add_controller(click);
    }

    /// Wi-Fi's networks or Bluetooth's devices in the panel, a back button above them.
    pub fn show_details(self: &Rc<Self>, kind: &str) {
        let Some(bar) = self.bar.upgrade() else { return };
        let back = gtk::Button::builder().icon_name("go-previous-symbolic").css_classes(["cc-back"]).halign(gtk::Align::Start).can_focus(false).tooltip_text(tr!("Control Center")).build();
        let w = Rc::downgrade(self);
        back.connect_clicked(move |_| w.upgrade().iter().for_each(|c| c.hide_details()));
        let w = Rc::downgrade(self);
        let close: Rc<dyn Fn()> = Rc::new(move || w.upgrade().iter().for_each(|c| c.close()));
        let page = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(2).css_classes(["cc-details"]).build();
        page.append(&back);
        page.append(&if kind == "wifi" { bar.wifi_column(close) } else { bar.bluetooth_column(close) });
        if let Some(old) = self.pages.child_by_name("details") {
            self.pages.remove(&old);
        }
        self.pages.add_named(&page, Some("details"));
        self.pages.set_visible_child_name("details");
    }

    pub fn hide_details(&self) {
        self.pages.set_transition_type(gtk::StackTransitionType::SlideRight);
        self.pages.set_visible_child_name("main");
        self.pages.set_transition_type(gtk::StackTransitionType::SlideLeftRight);
    }

    /// As tall as the modules (never squeezed by the panel), up to the screen.
    fn fit_height(&self) {
        let h = self.grid.measure(gtk::Orientation::Vertical, -1).1;
        self.scroller.set_min_content_height(h.min(self.scroller.max_content_height()));
    }

    fn hidden_modules(&self) -> Vec<String> {
        let modules = self.modules.borrow();
        ccgrid::hidden(&self.grid.order()).into_iter().filter(|m| modules.contains_key(m)).collect()
    }

    fn edit_bar_update(self: &Rc<Self>) {
        let on = self.grid.editing();
        self.edit_btn.set_visible(!on);
        self.done_btn.set_visible(on);
        self.add_btn.set_visible(on);
        let left = self.hidden_modules();
        self.add_btn.set_sensitive(!left.is_empty());
        if !on || left.is_empty() {
            self.add_reveal.set_reveal_child(false);
            self.add_btn.remove_css_class("on");
        }
        if self.add_reveal.reveals_child() {
            self.fill_add_list();
        }
    }

    fn toggle_add_list(self: &Rc<Self>) {
        let show = !self.add_reveal.reveals_child();
        if show {
            self.fill_add_list();
        }
        self.add_reveal.set_reveal_child(show);
        set_class(&self.add_btn, "on", show);
    }

    /// One button per control that can come back (at the end of the grid).
    fn fill_add_list(self: &Rc<Self>) {
        while let Some(c) = self.add_list.first_child() {
            self.add_list.remove(&c);
        }
        for m in self.hidden_modules() {
            let w = Rc::downgrade(self);
            let id = m.clone();
            let b = sonata_ui::controls::push_button("", move || {
                if let Some(c) = w.upgrade() {
                    c.grid.add_module(&id);
                    c.edit_bar_update();
                }
            }, "");
            let title = ccgrid::title(&m);
            b.set_tooltip_text(Some(&title));
            b.set_child(Some(&gtk::Label::builder().label(&title).ellipsize(gtk::pango::EllipsizeMode::End).width_chars(4).build()));
            self.add_list.append(&b);
        }
    }
}

fn poll_all() {
    // (the bar's own poller hands the answer to every display's bar)
    if let Some(b) = crate::window::first_bar() {
        b.poll();
    }
}

/// The Sound menu's per-app volumes as a module: live while shown (the
/// menu bar's mixer service), as many grid rows as the apps playing need,
/// up to 6; past that the list scrolls inside.
fn mixer_module(m: crate::seams::Mixer, cc: Weak<ControlCenter>) -> gtk::Box {
    let apps = Rc::new(crate::mixer_ui::AppMixer::new(m.apply.clone(), m.pick.clone(), None));
    let scroller = gtk::ScrolledWindow::builder().child(apps.widget()).hscrollbar_policy(gtk::PolicyType::Never).vscrollbar_policy(gtk::PolicyType::Automatic).vexpand(true).propagate_natural_height(false).build();
    let bx = kit::module(&[kit::module_title(&tr!("Volume Mixer")).upcast_ref(), scroller.upcast_ref()], 4);
    bx.add_css_class("cc-mixer");
    let (a, b, s) = (apps.clone(), bx.downgrade(), scroller.clone());
    let streams: Rc<dyn Fn(&[crate::mixer_ui::Stream])> = Rc::new(move |found| {
        a.set_streams(found);
        let (Some(bx), Some(cc)) = (b.upgrade(), cc.upgrade()) else { return };
        let v = gtk::Orientation::Vertical;
        let inner = a.widget().measure(v, -1).1;
        let chrome = bx.measure(v, -1).1 - s.measure(v, -1).1;
        cc.grid.set_rows("mixer", ccgrid::rows_for((chrome + inner) as f64, 6));
    });
    let listening: Rc<Cell<Option<u64>>> = Rc::default();
    let (st, l, mm) = (streams.clone(), listening.clone(), m.clone());
    bx.connect_map(move |_| {
        let (st2, f) = (st.clone(), mm.streams.clone());
        run_async(move || f(), move |found| st2(&found));
        if let (None, Some(listen)) = (l.get(), mm.listen.as_ref()) {
            l.set(Some(listen(st.clone())));
        }
    });
    bx.connect_unmap(move |_| {
        if let (Some(id), Some(un)) = (listening.take(), m.unlisten.as_ref()) {
            un(id);
        }
    });
    bx
}

// -- the quick controls on their own (the Game Bar's) ---------------------------------------------
/// Do Not Disturb as saved (notifications.json): the Game Bar reaches it
/// without the menu bar's Notifications, whose server follows the file.
pub fn dnd_saved() -> bool {
    sonata_notifications::model::load_config().get("dnd").and_then(serde_json::Value::as_bool).unwrap_or(false)
}

/// topbar.py's QuickControls: Control Center's Wi-Fi, Bluetooth, Airplane
/// Mode, Do Not Disturb and display brightness, made apart from Control
/// Center for the Game Bar (Vini: the game's display's brightness, the
/// radios, without leaving the game). Its brightness follows `output`,
/// set at each read().
pub struct QuickControls {
    pub wifi: Toggle,
    pub bt: Toggle,
    pub plane: Toggle,
    pub dnd: Toggle,
    pub bright: ModuleSlider,
    /// the brightness capsule with its Displays Preferences button
    pub display: gtk::Widget,
    output: Rc<RefCell<Option<String>>>,
    _brightness: Latest,
}

impl QuickControls {
    /// on_displays(): the Displays Preferences button beside the brightness.
    pub fn new(on_displays: Rc<dyn Fn()>) -> Rc<QuickControls> {
        let output: Rc<RefCell<Option<String>>> = Rc::default();
        Rc::new_cyclic(|weak: &Weak<QuickControls>| {
            let wifi = Toggle::new("network-wireless-symbolic", &tr!("Wi-Fi"), false, |on| run_async(move || system::set_wifi_enabled(on), |_| poll_all()), "…");
            let bt = Toggle::new("bluetooth-active-symbolic", &tr!("Bluetooth"), false, |on| kit::in_thread(move || {
                system::set_bluetooth(on);
            }), "…");
            let w = weak.clone();
            let plane = Toggle::new("airplane-mode-symbolic", &tr!("Airplane Mode"), false, move |on| {
                if let Some(q) = w.upgrade() {
                    q.set_airplane(on);
                }
            }, "…");
            // saved where Settings saves it: the menu bar's notification
            // server follows the file (banners hidden, its toggle in step)
            let dnd = Toggle::new("weather-clear-night-symbolic", &tr!("Do Not Disturb"), dnd_saved(), |on| {
                let _ = sonata_config::update(sonata_notifications::model::CONFIG, &serde_json::json!({ "dnd": on }));
            }, "");
            let latest = Latest::default();
            let (l, out) = (latest.clone(), output.clone());
            let disp_btn = round_button("video-display-symbolic", &tr!("Displays Preferences"), move |_| on_displays());
            let (display, bright) = slider_with_icon(|_| "display-brightness-symbolic".into(), level(&bright_key(&None)), move |v| {
                let out = out.borrow().clone();
                l.send(v as i64, move |v| {
                    system::set_brightness(v, out.as_deref());
                });
            }, true, Some(&disp_btn));
            QuickControls { wifi, bt, plane, dnd, bright, display, output, _brightness: latest }
        })
    }

    /// Every state as it is now: the radios, Do Not Disturb, and the
    /// brightness of `output` (the display it now stands for).
    pub fn read(self: &Rc<Self>, output: Option<String>) {
        let w = Rc::downgrade(self);
        run_async(cc_state, move |s| w.upgrade().iter().for_each(|q| show_toggles(&q.wifi, &q.bt, &q.plane, &s)));
        self.dnd.set(dnd_saved(), None);
        // the last level read for that display first: no jump as it opens (Vini)
        self.bright.show_value(level(&bright_key(&output)));
        *self.output.borrow_mut() = output.clone();
        let w = Rc::downgrade(self);
        run_async(move || (system::brightness(output.as_deref()), output), move |(b, out)| {
            if let Some(q) = w.upgrade() {
                // (another read since: the display it stands for is that one's)
                if *q.output.borrow() == out {
                    show_brightness(&q.bright, &out, b);
                }
            }
        });
    }

    fn set_airplane(self: &Rc<Self>, on: bool) {
        show_airplane(&self.wifi, &self.bt, &self.plane, on);
        let w = Rc::downgrade(self);
        run_async(move || system::set_airplane_mode(on), move |_| {
            poll_all();
            run_async(cc_state, move |s| w.upgrade().iter().for_each(|q| show_toggles(&q.wifi, &q.bt, &q.plane, &s)));
        });
    }

    /// A module of the radios, Do Not Disturb's (a click anywhere on it
    /// toggles), as Control Center lays them out.
    pub fn radios_module(&self) -> gtk::Box {
        kit::module(&[self.wifi.root.upcast_ref(), self.bt.root.upcast_ref(), self.plane.root.upcast_ref()], 2)
    }

    pub fn dnd_module(&self) -> gtk::Box {
        let m = kit::module(&[self.dnd.root.upcast_ref()], 8);
        kit::click_anywhere(&m, &self.dnd);
        m
    }
}
