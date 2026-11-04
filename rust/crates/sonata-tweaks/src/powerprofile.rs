//! The energy mode you chose comes back when the power adapter does
//! (sonata2/powerprofile.py; Vini: unplugged, the laptop went to Low Power
//! -- right -- but plugged in again it stayed there instead of going back
//! to Automatic, his choice).
//!
//! Another tool (power-profiles-daemon, asusd, TLP...) may lower it on
//! battery; on the adapter again, the chosen one is set back -- unless a
//! full-screen game raised it meanwhile (gamemode::PowerBoost).

use serde_json::json;
use sonata_backend::system::POWER_PROFILES;

const NAME: &str = "power";

fn known(p: &str) -> bool {
    POWER_PROFILES.iter().any(|(k, _)| *k == p)
}

pub fn chosen() -> String {
    let cfg = crate::util::load(NAME, json!({"profile": "balanced"}));
    match cfg.get("profile").and_then(|v| v.as_str()) {
        Some(p) if known(p) => p.to_string(),
        _ => "balanced".into(),
    }
}

/// Chosen in the menu bar or Settings.
pub fn remember(profile: &str) {
    if known(profile) {
        let _ = sonata_config::update(NAME, &json!({"profile": profile}));
    }
}

/// On every power event (the menu bar): `changed()`.
pub struct Follow {
    pub on_ac: Box<dyn Fn() -> bool>,
    pub current: Box<dyn Fn() -> Option<String>>,
    pub apply: Box<dyn Fn(&str) -> bool>,
    pub boosted: Box<dyn Fn() -> bool>,
    ac: bool,
}

impl Follow {
    pub fn new() -> Self {
        Self::with(
            Box::new(|| sonata_backend::system::on_ac("/sys/class/power_supply")),
            Box::new(sonata_backend::system::power_profile_fast),
            Box::new(sonata_backend::system::set_power_profile),
            Box::new(crate::gamemode::boosted),
        )
    }

    pub fn with(on_ac: Box<dyn Fn() -> bool>, current: Box<dyn Fn() -> Option<String>>, apply: Box<dyn Fn(&str) -> bool>, boosted: Box<dyn Fn() -> bool>) -> Self {
        let ac = on_ac();
        Follow { on_ac, current, apply, boosted, ac }
    }

    /// A power event: true if the chosen mode was set back.
    pub fn changed(&mut self) -> bool {
        let (ac, was) = ((self.on_ac)(), self.ac);
        self.ac = ac;
        if !ac || was {
            return false; // only the moment it's plugged in again
        }
        let (cur, want) = ((self.current)().unwrap_or_default(), chosen());
        if cur.is_empty() || cur == want || (self.boosted)() {
            return false;
        }
        (self.apply)(&want)
    }
}

impl Default for Follow {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::rc::Rc;

    struct Rig {
        ac: Rc<RefCell<bool>>,
        cur: Rc<RefCell<String>>,
        boost: Rc<RefCell<bool>>,
        set: Rc<RefCell<Vec<String>>>,
    }

    fn follow() -> (Follow, Rig) {
        let r = Rig { ac: Rc::new(RefCell::new(true)), cur: Rc::new(RefCell::new("balanced".into())), boost: Rc::default(), set: Rc::default() };
        let (a, c, b, s) = (r.ac.clone(), r.cur.clone(), r.boost.clone(), r.set.clone());
        let f = Follow::with(Box::new(move || *a.borrow()), Box::new(move || Some(c.borrow().clone())), Box::new(move |p| {
            s.borrow_mut().push(p.into());
            true
        }), Box::new(move || *b.borrow()));
        (f, r)
    }

    #[test]
    fn power_modes_follow_the_adapter() {
        let _l = crate::util::test_lock();
        let _ = std::fs::remove_file(sonata_config::write_dir().join("power.json"));
        // back to Automatic when plugged in
        let (mut f, r) = follow();
        *r.ac.borrow_mut() = false;
        *r.cur.borrow_mut() = "power-saver".into(); // unplugged: something lowered it
        assert!(!f.changed()); // (left as it is on battery)
        *r.ac.borrow_mut() = true;
        assert!(f.changed());
        assert_eq!(*r.set.borrow(), ["balanced"]);
        assert!(!f.changed()); // still plugged in: nothing again
        // a game boost is left alone
        let (mut f, r) = follow();
        *r.ac.borrow_mut() = false;
        f.changed();
        *r.ac.borrow_mut() = true;
        *r.cur.borrow_mut() = "performance".into();
        *r.boost.borrow_mut() = true;
        assert!(!f.changed());
        assert!(r.set.borrow().is_empty());
        // the mode you chose
        remember("performance");
        let (mut f, r) = follow();
        *r.ac.borrow_mut() = false;
        *r.cur.borrow_mut() = "power-saver".into();
        f.changed();
        *r.ac.borrow_mut() = true;
        f.changed();
        assert_eq!(*r.set.borrow(), ["performance"]);
        remember("nonsense"); // unknown: kept as before
        assert_eq!(chosen(), "performance");
        let _ = std::fs::remove_file(sonata_config::write_dir().join("power.json"));
    }
}
