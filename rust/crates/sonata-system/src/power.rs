//! Battery / power events (backend/power.py): plugging in, charging, level
//! and Low Power Mode changes are pushed (UPower and power-profiles-daemon
//! D-Bus signals), so every battery icon updates at once; readers still use
//! system::battery(), on_ac() and power_profile_fast().

use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

const PATHS: [(&str, &str); 4] = [
    ("org.freedesktop.UPower", "/org/freedesktop/UPower"),
    ("org.freedesktop.UPower", "/org/freedesktop/UPower/devices/DisplayDevice"),
    ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
    ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles"),
];

/// callback() after any change (a burst of property changes -> one
/// update). False when the system bus isn't reachable (callers keep
/// polling). Main thread; the subscriptions last as long as the process.
pub fn watch(callback: impl Fn() + 'static) -> bool {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) else { return false };
    let callback = Rc::new(callback);
    let src: Rc<RefCell<Option<glib::SourceId>>> = Rc::default();
    for (name, path) in PATHS {
        let (cb, src) = (callback.clone(), src.clone());
        let id = bus.signal_subscribe(Some(name), Some("org.freedesktop.DBus.Properties"), Some("PropertiesChanged"), Some(path), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, _| {
            if src.borrow().is_some() {
                return;
            }
            let (cb, s2) = (cb.clone(), src.clone());
            *src.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(150), move || {
                s2.borrow_mut().take();
                cb();
            }));
        });
        // kept for the process's life, like Python's module-level list
        std::mem::forget(id);
    }
    true
}

/// Big Sur battery states: charging (bolt), on power but holding -- full
/// or a charge limit -- (plug), Low Power Mode (yellow), low (red in the
/// icon); `missing` when there's a battery but no reading.
pub fn icon_name(pct: Option<i64>, status: &str, ac: bool, profile: Option<&str>) -> String {
    let Some(pct) = pct else { return "sonata-battery-missing-symbolic".into() };
    let level = ((pct + 5).div_euclid(10) * 10).clamp(0, 100);
    let state = if status == "Charging" {
        "-charging"
    } else if ac {
        "-plugged"
    } else if profile == Some("power-saver") {
        "-saver"
    } else {
        ""
    };
    format!("sonata-battery-{level}{state}-symbolic")
}

#[cfg(test)]
mod tests {
    #[test]
    fn battery_icons() {
        use super::icon_name;
        assert_eq!(icon_name(None, "", false, None), "sonata-battery-missing-symbolic");
        assert_eq!(icon_name(Some(64), "Charging", true, None), "sonata-battery-60-charging-symbolic");
        assert_eq!(icon_name(Some(95), "Full", true, None), "sonata-battery-100-plugged-symbolic");
        assert_eq!(icon_name(Some(14), "Discharging", false, Some("power-saver")), "sonata-battery-10-saver-symbolic");
        assert_eq!(icon_name(Some(4), "Discharging", false, None), "sonata-battery-0-symbolic");
    }
}
