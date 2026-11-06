//! tests/test_gamepad.py, test_gamebar_controller.py's ServiceTests and
//! test_review_media.py's Gamepad* tests: never real devices, never the
//! real session (a fake System and fake Targets).

use super::*;
use crate::{main_loop_lock, settle};

#[derive(Debug, PartialEq, Clone)]
enum P {
    Move(i32, i32),
    Button(u32, bool),
    Scroll(f64, f64),
}

struct FakePointer(Rc<RefCell<Vec<P>>>);
impl Pointer for FakePointer {
    fn ok(&self) -> bool {
        true
    }
    fn move_by(&mut self, dx: i32, dy: i32) {
        self.0.borrow_mut().push(P::Move(dx, dy));
    }
    fn button(&mut self, b: u32, p: bool) {
        self.0.borrow_mut().push(P::Button(b, p));
    }
    fn scroll(&mut self, dx: f64, dy: f64) {
        self.0.borrow_mut().push(P::Scroll(dx, dy));
    }
    fn close(&mut self) {}
}

#[derive(Default)]
struct FakePad {
    name: String,
    grabs: RefCell<Vec<bool>>,
}
impl Pad for FakePad {
    fn name(&self) -> String {
        self.name.clone()
    }
    fn grab(&self, on: bool) -> bool {
        self.grabs.borrow_mut().push(on);
        true
    }
    /// raw in hundredths: 90 is 0.9
    fn norm(&self, _code: u16, raw: i32) -> f64 {
        raw as f64 / 100.0
    }
    fn close(&self) {}
}

#[derive(Default)]
struct Fake {
    log: Rc<RefCell<Vec<P>>>,
    actions: RefCell<Vec<(String, Option<String>)>>,
    spawned: RefCell<Vec<String>>,
    notes: RefCell<Vec<String>>,
    fullscreen: Cell<bool>,
    steam: Cell<bool>,
    found: RefCell<Vec<String>>,
    scans: Cell<u32>,
    made: RefCell<Vec<Rc<FakePad>>>,
}
impl System for Fake {
    fn find_gamepads(&self) -> Vec<String> {
        self.scans.set(self.scans.get() + 1);
        self.found.borrow().clone()
    }
    fn find_guide_devices(&self, _names: &[String]) -> Vec<String> {
        Vec::new()
    }
    fn open(&self, _path: &str, _on_event: OnEvent) -> io::Result<Rc<dyn Pad>> {
        let pad = Rc::new(FakePad::default());
        self.made.borrow_mut().push(pad.clone());
        Ok(pad)
    }
    fn steam_running(&self) -> bool {
        self.steam.get()
    }
    fn fullscreen(&self) -> bool {
        self.fullscreen.get()
    }
    fn activate(&self, action: &str, param: Option<&str>) {
        self.actions.borrow_mut().push((action.into(), param.map(String::from)));
    }
    fn spawn(&self, component: &str) {
        self.spawned.borrow_mut().push(component.into());
    }
    fn notify(&self, text: &str) {
        self.notes.borrow_mut().push(text.into());
    }
    fn with_pointer(&self, f: &mut dyn FnMut(&mut dyn Pointer)) {
        f(&mut FakePointer(self.log.clone()));
    }
    fn close_pointer(&self) {}
}

#[derive(Default)]
struct Fakes {
    keys: RefCell<Vec<String>>,
    switcher: Cell<bool>,
    sw_calls: RefCell<Vec<String>>,
    bar: Cell<bool>,
    got: RefCell<Vec<String>>,
}
impl Targets for Fakes {
    fn switcher_open(&self) -> bool {
        self.switcher.get()
    }
    fn switcher_step(&self, d: i32) {
        self.sw_calls.borrow_mut().push(d.to_string());
    }
    fn switcher_switch(&self) {
        self.sw_calls.borrow_mut().push("switch".into());
    }
    fn switcher_close(&self) {
        if self.switcher.get() {
            self.sw_calls.borrow_mut().push("close".into());
        }
    }
    fn gamebar_open(&self) -> bool {
        self.bar.get()
    }
    fn gamebar_pad(&self, name: &str) {
        self.got.borrow_mut().push(name.into());
    }
    fn key(&self, name: &str) -> bool {
        self.keys.borrow_mut().push(name.into());
        true
    }
}

struct T {
    _lock: std::sync::MutexGuard<'static, ()>,
    sys: Rc<Fake>,
    tg: Rc<Fakes>,
    g: Rc<Gamepads>,
    pad: Rc<FakePad>,
}

impl T {
    /// Desktop control on, one controller "p" (GamepadTest's setUp).
    fn new() -> T {
        let t = T::off();
        t.g.cfg.borrow_mut().insert("enabled".into(), Value::Bool(true));
        t
    }

    /// The settings as they come (ServiceTests' setUp).
    fn off() -> T {
        let lock = main_loop_lock();
        let (sys, tg) = (Rc::new(Fake::default()), Rc::new(Fakes::default()));
        let g = Gamepads::with(sys.clone(), tg.clone());
        g.cfg.replace(defaults().as_object().unwrap().clone());
        let pad = Rc::new(FakePad { name: "Xbox Wireless Controller".into(), ..Default::default() });
        g.pads.borrow_mut().insert("p".into(), pad.clone());
        T { _lock: lock, sys, tg, g, pad }
    }

    fn ev(&self, typ: u16, code: u16, value: i32) {
        self.g.event("p", Some((typ, code, value)));
    }

    fn press(&self, code: u16, value: i32) {
        self.ev(e::EV_KEY, code, value);
    }

    fn chord(&self, down: bool) {
        for b in TOGGLE_CHORD {
            self.press(b, down as i32);
        }
    }

    fn log(&self) -> Vec<P> {
        self.sys.log.borrow().clone()
    }

    fn keys(&self) -> Vec<String> {
        self.tg.keys.borrow().clone()
    }

    fn set(&self, key: &str, v: Value) {
        self.g.cfg.borrow_mut().insert(key.into(), v);
    }
}

impl Drop for T {
    fn drop(&mut self) {
        self.g.stop(); // no source of this test left for the next one
    }
}

// -- test_gamepad.py ------------------------------------------------------------------------------
#[test]
fn test_dead_zone_and_curve() {
    assert_eq!(curve(0.1), 0.0);
    assert!(curve(1.0) > 0.99);
    assert!(curve(-0.6) < 0.0);
}

#[test]
fn test_buttons() {
    let t = T::new();
    t.press(e::BTN_SOUTH, 1);
    t.press(e::BTN_SOUTH, 0);
    t.press(e::BTN_NORTH, 1);
    t.press(e::BTN_EAST, 1);
    t.press(e::BTN_DPAD_UP, 1);
    assert_eq!(t.log()[..3], [P::Button(BTN_LEFT, true), P::Button(BTN_LEFT, false), P::Button(BTN_RIGHT, true)]);
    assert_eq!(t.keys(), ["Escape", "Up"]);
}

#[test]
fn test_launchpad_search_and_the_switcher_buttons() {
    let t = T::new();
    t.press(e::BTN_WEST, 1);
    t.press(e::BTN_START, 1);
    t.press(e::BTN_TR, 1);
    t.press(e::BTN_TL, 1);
    t.press(e::BTN_SELECT, 1);
    assert_eq!(*t.sys.spawned.borrow(), ["launchpad", "spotlight"]);
    assert_eq!(*t.sys.actions.borrow(), [("switcher".into(), Some("next".into())), ("switcher".into(), Some("prev".into()))]);
    assert_eq!(t.keys(), ["F3"]);
}

#[test]
fn test_paused_in_fullscreen_games_and_with_steam() {
    let t = T::new();
    t.sys.fullscreen.set(true); // a fullscreen window has the focus
    t.press(e::BTN_SOUTH, 1);
    assert_eq!(t.log(), []);
    t.sys.fullscreen.set(false);
    t.g.steam.set(true);
    t.press(e::BTN_EAST, 1);
    assert!(t.keys().is_empty());
}

#[test]
fn test_off_by_default_and_ignores_buttons_when_off() {
    let t = T::new();
    assert_eq!(defaults()["enabled"], Value::Bool(false));
    t.set("enabled", Value::Bool(false));
    t.press(e::BTN_SOUTH, 1);
    t.press(e::BTN_EAST, 1);
    assert!(t.log().is_empty() && t.keys().is_empty());
}

/// Vini: the on / off shortcut was the Xbox / PS button 5 times quickly;
/// now LB + RB + L3 + R3 held together (L1 + R1 + L3 + R3).
#[test]
fn test_toggle_is_l1_r1_l3_r3_held_together() {
    let t = T::new();
    t.set("enabled", Value::Bool(false));
    t.chord(true);
    assert!(t.g.flag("enabled", false));
    assert_eq!(sonata_config::load(CONFIG, &defaults())["enabled"], Value::Bool(true)); // saved
    assert_eq!(*t.sys.notes.borrow(), [tr!("Controller: desktop control on")]);
    t.press(e::BTN_THUMBR, 2); // key repeat while held: still once
    assert!(t.g.flag("enabled", false));
    t.chord(false);
    t.chord(true);
    assert!(!t.g.flag("enabled", true));
    t.chord(false);
    for _ in 0..5 {
        // the old shortcut does nothing now
        t.press(e::BTN_MODE, 1);
        t.press(e::BTN_MODE, 0);
    }
    assert!(!t.g.flag("enabled", true));
}

#[test]
fn test_three_of_the_four_dont_toggle() {
    let t = T::new();
    t.set("enabled", Value::Bool(false));
    for b in [e::BTN_TL, e::BTN_TR, e::BTN_THUMBL] {
        t.press(b, 1);
    }
    assert!(!t.g.flag("enabled", true));
}

#[test]
fn test_chord_closes_the_switcher_lb_rb_opened() {
    let t = T::new();
    t.tg.switcher.set(true);
    t.chord(true);
    assert_eq!(t.tg.sw_calls.borrow().last().map(String::as_str), Some("close"));
}

#[test]
fn test_one_guide_press_is_mission_control() {
    let t = T::new();
    t.set("guide_gamebar", Value::Bool(false)); // (the Game Bar's setting off)
    t.press(e::BTN_MODE, 1);
    t.press(e::BTN_MODE, 0);
    assert_eq!(t.keys(), ["F3"]); // at once: no burst to wait for
    t.press(e::KEY_HOMEPAGE, 1); // the Xbox button over Bluetooth
    assert_eq!(t.keys(), ["F3", "F3"]);
}

#[test]
fn test_steam_pause_can_be_turned_off() {
    let t = T::new();
    t.g.steam.set(true);
    assert!(t.g.paused());
    t.set("pause_steam", Value::Bool(false));
    assert!(!t.g.paused());
}

#[test]
fn test_switcher_driven_by_the_controller() {
    let t = T::new();
    t.tg.switcher.set(true);
    t.press(e::BTN_DPAD_RIGHT, 1);
    t.press(e::BTN_SOUTH, 1);
    t.press(e::BTN_EAST, 1);
    assert_eq!(*t.tg.sw_calls.borrow(), ["1", "switch", "close"]);
    assert_eq!(t.log(), []);
}

#[test]
fn test_stick_moves_the_pointer() {
    let t = T::new();
    t.ev(e::EV_ABS, e::ABS_X, 100);
    settle(120);
    t.ev(e::EV_ABS, e::ABS_X, 0);
    settle(40);
    let moves: Vec<P> = t.log().into_iter().filter(|p| matches!(p, P::Move(..))).collect();
    assert!(!moves.is_empty() && moves.iter().all(|m| matches!(m, P::Move(x, _) if *x > 0)));
    assert!(t.g.tick_src.borrow().is_none()); // stopped once the stick is back
}

#[test]
fn test_right_stick_scrolls() {
    let t = T::new();
    t.ev(e::EV_ABS, e::ABS_RY, 100);
    settle(60);
    t.ev(e::EV_ABS, e::ABS_RY, 0);
    settle(30);
    assert!(t.log().iter().any(|p| matches!(p, P::Scroll(x, y) if *x == 0.0 && *y > 0.0)));
}

// -- test_gamebar_controller.py: ServiceTests -----------------------------------------------------
#[test]
fn test_guide_opens_game_bar_with_desktop_control_off() {
    let t = T::off();
    assert!(!t.g.flag("enabled", true)); // off by default
    t.press(e::BTN_MODE, 1);
    assert_eq!(*t.sys.actions.borrow(), [("gamebar".into(), None)]);
}

#[test]
fn test_guide_opens_game_bar_in_a_fullscreen_game() {
    let t = T::off();
    t.sys.fullscreen.set(true);
    t.set("enabled", Value::Bool(true));
    assert!(t.g.paused());
    t.press(e::KEY_HOMEPAGE, 1); // Xbox over Bluetooth
    assert_eq!(*t.sys.actions.borrow(), [("gamebar".into(), None)]);
}

#[test]
fn test_guide_setting_off_gives_overview_back() {
    let t = T::off();
    t.set("guide_gamebar", Value::Bool(false));
    t.set("enabled", Value::Bool(true));
    t.press(e::BTN_MODE, 1);
    assert!(t.sys.actions.borrow().is_empty());
    assert_eq!(t.keys(), ["F3"]);
}

#[test]
fn test_open_bar_gets_the_buttons_not_the_desktop() {
    let t = T::off();
    t.set("enabled", Value::Bool(true));
    t.tg.bar.set(true);
    for code in [e::BTN_SOUTH, e::BTN_EAST, e::BTN_TR, e::BTN_TL, e::BTN_DPAD_DOWN, e::BTN_MODE] {
        t.press(code, 1);
        t.press(code, 0); // releases are not presses
    }
    t.ev(e::EV_ABS, e::ABS_HAT0X, 1);
    assert_eq!(*t.tg.got.borrow(), ["primary", "back", "switch_next", "switch_prev", "down", "guide", "right"]);
    assert!(t.keys().is_empty()); // no Esc / arrows to the game
    assert!(t.sys.actions.borrow().is_empty()); // no app switcher, no second open
}

#[test]
fn test_left_stick_steps_and_repeats() {
    let t = T::off();
    t.tg.bar.set(true);
    t.ev(e::EV_ABS, e::ABS_X, 90);
    assert_eq!(*t.tg.got.borrow(), ["right"]);
    settle(NAV_FIRST_MS + NAV_REPEAT_MS + 60);
    assert!(t.tg.got.borrow().iter().filter(|g| *g == "right").count() >= 2);
    t.ev(e::EV_ABS, e::ABS_X, 0);
    let n = t.tg.got.borrow().len();
    settle(NAV_REPEAT_MS * 3);
    assert_eq!(t.tg.got.borrow().len(), n); // let go: it stops
    t.ev(e::EV_ABS, e::ABS_Y, -80);
    assert_eq!(t.tg.got.borrow().last().map(String::as_str), Some("up"));
}

#[test]
fn test_stick_doesnt_move_pointer_while_open() {
    let t = T::off();
    t.set("enabled", Value::Bool(true));
    t.tg.bar.set(true);
    t.ev(e::EV_ABS, e::ABS_X, 90);
    assert!(t.g.tick_src.borrow().is_none());
}

#[test]
fn test_grab_and_release() {
    let t = T::off();
    t.g.grab(true);
    t.g.grab(true); // once
    t.g.grab(false);
    assert_eq!(*t.pad.grabs.borrow(), [true, false]);
}

#[test]
fn test_pad_plugged_while_grabbed_is_grabbed() {
    let t = T::off();
    t.g.grab(true);
    t.sys.found.replace(vec!["/dev/input/event99".into()]);
    t.g.scan();
    assert_eq!(*t.sys.made.borrow()[0].grabs.borrow(), [true]);
    assert!(t.g.pads.borrow().contains_key("/dev/input/event99"));
}

// -- test_review_media.py: GamepadEdgeTest / GamepadFixTest ---------------------------------------
/// A hat d-pad sends one arrow per push (release ignored); held buttons'
/// auto-repeat (2) is ignored.
#[test]
fn test_hat_dpad_and_key_repeat() {
    let t = T::new();
    t.ev(e::EV_ABS, e::ABS_HAT0X, -1);
    t.ev(e::EV_ABS, e::ABS_HAT0X, 0);
    t.ev(e::EV_ABS, e::ABS_HAT0Y, 1);
    t.press(e::BTN_DPAD_UP, 1);
    t.press(e::BTN_DPAD_UP, 2);
    t.press(0x2ff, 1); // an unmapped button
    assert_eq!(t.keys(), ["Left", "Down", "Up"]);
}

/// Holding A (drag) when desktop control pauses must not leave the virtual
/// left button stuck down.
#[test]
fn test_button_released_when_paused_mid_drag() {
    let t = T::new();
    t.press(e::BTN_SOUTH, 1);
    t.sys.fullscreen.set(true); // a fullscreen game takes the focus
    t.press(e::BTN_SOUTH, 0);
    assert_eq!(t.log().last(), Some(&P::Button(BTN_LEFT, false)));
}

/// The burst of /dev/input changes one plug makes is debounced into a
/// single rescan.
#[test]
fn test_one_plug_one_scan() {
    let t = T::new();
    let before = t.sys.scans.get();
    for _ in 0..6 {
        t.g.scan_soon();
    }
    settle(800);
    assert_eq!(t.sys.scans.get() - before, 1);
}

/// Unplugging or turning desktop control off mid-drag lets go of the
/// virtual button.
#[test]
fn test_button_released_on_unplug_and_disable() {
    let t = T::new();
    t.press(e::BTN_SOUTH, 1);
    t.g.event("p", None); // unplugged
    assert_eq!(t.log().last(), Some(&P::Button(BTN_LEFT, false)));
    assert!(!t.g.pads.borrow().contains_key("p"));
    t.press(e::BTN_NORTH, 1);
    t.g.set_enabled(false);
    assert_eq!(t.log().last(), Some(&P::Button(BTN_RIGHT, false)));
    let n = t.log().len();
    t.g.release_held(); // nothing held: no stray release
    assert_eq!(t.log().len(), n);
}

// -- test_vkeyboard.py: test_menu_bar_and_controller_use_it -----------------------------------------
/// The keys go out through the menu bar's virtual keyboard (their real
/// keycodes), not wtype, when it has one.
#[test]
fn test_keys_go_through_the_virtual_keyboard() {
    let t = T::new();
    t.press(e::BTN_EAST, 1);
    assert_eq!(t.keys(), ["Escape"]);
}

#[test]
fn test_stop_lets_go_and_releases_the_grab() {
    let t = T::new();
    t.g.grab(true);
    t.press(e::BTN_SOUTH, 1);
    t.g.stop();
    assert_eq!(*t.pad.grabs.borrow(), [true, false]);
    assert_eq!(t.log().last(), Some(&P::Button(BTN_LEFT, false)));
    assert!(t.g.pads.borrow().is_empty());
}
