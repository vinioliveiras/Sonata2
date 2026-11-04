//! Wi-Fi, Network, Bluetooth, Printers & Scanners, Sharing (app.py's
//! _page_wifi ... _page_sharing). Everything here asks the system
//! (nmcli, BlueZ, CUPS, hostnamectl): read and written off the main loop.

use crate::kit::{clear_group, group, install_row, loading_row, run_async, show_quietly, switch_row, Install};
use crate::window::{Ctx, Settings};
use adw::prelude::*;
use gtk::{gio, glib};
use sonata_backend::system;
use sonata_i18n::{fmt, gettext, tr};
use sonata_ui::progress::Spinner;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::{Rc, Weak};

// -- pure helpers -------------------------------------------------------------------------------------
/// A network's bars, from its signal (0..100).
fn signal_icon(signal: i64) -> String {
    let sig = if signal > 75 {
        "excellent"
    } else if signal > 50 {
        "good"
    } else if signal > 25 {
        "ok"
    } else {
        "weak"
    };
    format!("network-wireless-signal-{sig}-symbolic")
}

/// Python's str.title(): "wireguard" -> "Wireguard", "ip-tunnel" -> "Ip-Tunnel".
fn title_case(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut start = true;
    for c in s.chars() {
        if start {
            out.extend(c.to_uppercase());
        } else {
            out.extend(c.to_lowercase());
        }
        start = !c.is_alphabetic();
    }
    out
}

/// A network service's kind as Network shows it.
fn kind_label(kind: &str) -> String {
    match kind {
        "ethernet" => "Ethernet".into(),
        "vpn" => "VPN".into(),
        "bridge" => tr!("Bridge"),
        "bond" => tr!("Bond"),
        "mobile" => tr!("Mobile Broadband"),
        "bluetooth" => tr!("Bluetooth PAN"),
        other => title_case(other),
    }
}

/// Where Screen Sharing is reached, one address a line (Tailscale's named).
#[cfg_attr(not(feature = "system"), allow(dead_code))]
fn addresses_text(addrs: &[(String, String)]) -> String {
    addrs.iter().map(|(a, i)| format!("{a}  ({})", if i.starts_with("tailscale") { "Tailscale" } else { i })).collect::<Vec<_>>().join("\n")
}

/// What Printers shows before its list.
#[derive(Debug, PartialEq)]
enum Printing {
    /// CUPS running: the printers
    Ready,
    /// installed but not running: Turn On…
    Off,
    /// not installed: the command that installs and starts it (None:
    /// unknown package manager)
    Missing(Option<String>),
}

/// installed: lpstat answered (printers() isn't None). family: the
/// package family (None: this system's).
fn printing(installed: bool, running: bool, family: Option<&str>) -> Printing {
    if !installed {
        return Printing::Missing(system::install_command("cups", family).map(|c| format!("{c} && {}", system::CUPS_ON)));
    }
    if !running {
        return Printing::Off;
    }
    Printing::Ready
}

/// The saved devices' connected state (the poll compares it with what
/// the page shows).
fn paired_state(devs: &[system::BtDevice]) -> HashMap<String, bool> {
    devs.iter().filter(|d| d.paired).map(|d| (d.mac.clone(), d.connected)).collect()
}

fn reload(s: &Weak<Settings>, sid: &str) {
    if let Some(s) = s.upgrade() {
        s.reload_page(sid);
    }
}

fn toast(s: &Weak<Settings>, text: &str) {
    if let Some(s) = s.upgrade() {
        s.toast(text);
    }
}

fn header_button(icon: &str, tooltip: &str) -> gtk::Button {
    gtk::Button::builder().icon_name(icon).css_classes(["flat"]).valign(gtk::Align::Center).tooltip_text(tooltip).build()
}

// -- Wi-Fi ----------------------------------------------------------------------------------------
/// Wi-Fi's networks, filled again in place (a scan, the radio switched).
struct Wifi {
    settings: Weak<Settings>,
    me: Weak<Wifi>,
    switch: adw::SwitchRow,
    nets: adw::PreferencesGroup,
    // Vini: the list took a moment to come -- a spinner while it's read
    spin: Spinner,
    rows: RefCell<Vec<adw::ActionRow>>,
}

pub fn page_wifi(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let top = group("", "");
    let nets = group(&tr!("Networks"), "");
    let w = Rc::new_cyclic(|me: &Weak<Wifi>| {
        let me2 = me.clone();
        let switch = switch_row("Wi-Fi", false, move |on| {
            let me = me2.clone();
            run_async(move || system::set_wifi_enabled(on), move |_| {
                if let Some(w) = me.upgrade() {
                    w.fill(false);
                }
            });
        }, "");
        let spin = Spinner::new(true);
        spin.set_valign(gtk::Align::Center);
        spin.set_visible(false);
        Wifi { settings: Rc::downgrade(ctx), me: me.clone(), switch, nets: nets.clone(), spin, rows: RefCell::new(Vec::new()) }
    });
    top.add(&w.switch);
    let rescan = header_button("view-refresh-symbolic", &tr!("Scan again"));
    let me = Rc::downgrade(&w);
    rescan.connect_clicked(move |_| {
        if let Some(w) = me.upgrade() {
            w.fill(true);
        }
    });
    let head = gtk::Box::builder().spacing(6).valign(gtk::Align::Center).build();
    head.append(&w.spin);
    head.append(&rescan);
    nets.set_header_suffix(Some(&head));
    w.fill(false);
    ctx.keep("wifi", w); // (the closures hold it weakly: a page built again drops the old one)
    vec![top, nets]
}

impl Wifi {
    fn fill(&self, rescan: bool) {
        self.spin.set_visible(true);
        if self.rows.borrow().is_empty() {
            // the first time: a row saying so
            let row = loading_row(&tr!("Looking for networks…"));
            self.nets.add(&row);
            self.rows.borrow_mut().push(row);
        }
        let me = self.me.clone();
        run_async(move || (system::wifi_enabled(), system::wifi_scan(rescan)), move |(enabled, networks)| {
            if let Some(w) = me.upgrade() {
                w.show(enabled, &networks);
            }
        });
    }

    fn show(&self, enabled: bool, networks: &[system::WifiNetwork]) {
        self.spin.set_visible(false);
        show_quietly(&self.switch, enabled); // read back: no second `nmcli radio wifi`
        for r in self.rows.borrow_mut().drain(..) {
            self.nets.remove(&r);
        }
        let mut rows = Vec::new();
        for n in networks.iter().take(20) {
            let row = adw::ActionRow::builder().title(&n.ssid).activatable(!n.connected).use_markup(false).subtitle(if n.connected { tr!("Connected") } else { String::new() }).build();
            if n.secure {
                row.add_suffix(&gtk::Image::builder().icon_name("system-lock-screen-symbolic").pixel_size(12).build());
            }
            row.add_suffix(&gtk::Image::from_icon_name(&signal_icon(n.signal)));
            let (me, n) = (self.me.clone(), n.clone());
            row.connect_activated(move |_| {
                if let Some(w) = me.upgrade() {
                    w.join(&n);
                }
            });
            self.nets.add(&row);
            rows.push(row);
        }
        if networks.is_empty() {
            let row = adw::ActionRow::builder().title(if enabled { tr!("No networks found") } else { tr!("Wi-Fi is off") }).build();
            self.nets.add(&row);
            rows.push(row);
        }
        *self.rows.borrow_mut() = rows;
    }

    fn join(&self, n: &system::WifiNetwork) {
        let (me, ssid) = (self.me.clone(), n.ssid.clone());
        let connect = move |pw: String| {
            let (me, ssid) = (me.clone(), ssid.clone());
            let name = ssid.clone();
            run_async(move || system::wifi_connect(&ssid, &pw), move |(ok, msg)| {
                let Some(w) = me.upgrade() else { return };
                let text = if ok {
                    fmt(&tr!("Connected to {name}"), &[("name", &name)])
                } else if msg.is_empty() {
                    tr!("Couldn't connect")
                } else {
                    msg
                };
                toast(&w.settings, &text);
                w.fill(false);
            });
        };
        if !n.secure {
            connect(String::new());
            return;
        }
        let entry = sonata_ui::controls::secret_field("", "");
        let weak = entry.downgrade(); // (the dialog holds the field: no cycle)
        let parent = self.settings.upgrade().and_then(|s| s.parent());
        let dlg = sonata_ui::dialog::alert(
            &fmt(&tr!("The Wi-Fi network “{name}” requires a password."), &[("name", &n.ssid)]),
            "",
            &[("cancel", tr!("Cancel"), ""), ("join", tr!("Join"), "default")],
            move |r| {
                if r == "join" {
                    connect(weak.upgrade().map(|e| e.text().to_string()).unwrap_or_default());
                }
            },
            parent.as_ref(),
        );
        dlg.set_extra_child(Some(&entry));
    }
}

// -- Network --------------------------------------------------------------------------------------
/// Big Sur Network: the services (Ethernet, VPN, ...) with their status
/// and address; VPNs are imported from .ovpn / WireGuard files.
pub fn page_network(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let svc = group(&tr!("Services"), "");
    svc.add(&adw::ActionRow::builder().title(tr!("Loading…")).build());
    let add = header_button("list-add-symbolic", &tr!("Import VPN Configuration…"));
    let s = Rc::downgrade(ctx);
    add.connect_clicked(move |_| import_vpn(&s));
    svc.set_header_suffix(Some(&add));
    let (s, g) = (Rc::downgrade(ctx), svc.clone());
    run_async(system::net_services, move |lst| fill_services(&s, &g, &lst));
    vec![svc]
}

fn fill_services(s: &Weak<Settings>, svc: &adw::PreferencesGroup, lst: &[system::NetService]) {
    clear_group(svc);
    for n in lst {
        let state = if n.active { tr!("Connected") } else { tr!("Not Connected") };
        let row = adw::ExpanderRow::builder().title(&n.name).use_markup(false).subtitle(format!("{} · {state}", kind_label(&n.kind))).build();
        let dot = gtk::Box::builder().css_classes(["st-status", if n.active { "on" } else { "off" }]).valign(gtk::Align::Center).build();
        row.add_prefix(&dot);
        let (s2, uuid) = (s.clone(), n.uuid.clone());
        row.add_row(&switch_row(&tr!("Connected"), n.active, move |on| {
            let (s, uuid) = (s2.clone(), uuid.clone());
            run_async(move || system::net_service_set(&uuid, on), move |(ok, msg)| {
                toast(&s, &if ok { tr!("Done") } else { msg });
                reload(&s, "network");
            });
        }, ""));
        for (title, val) in [(tr!("IP Address"), &n.address), (tr!("Router"), &n.gateway), (tr!("DNS Server"), &n.dns), (tr!("Device"), &n.device)] {
            if !val.is_empty() {
                row.add_row(&adw::ActionRow::builder().title(title).subtitle(val.as_str()).use_markup(false).subtitle_selectable(true).build());
            }
        }
        if n.kind == "vpn" {
            let rm = adw::ActionRow::builder().title(tr!("Remove VPN")).activatable(true).build();
            rm.add_css_class("error");
            let (s, uuid) = (s.clone(), n.uuid.clone());
            rm.connect_activated(move |_| {
                let (s, uuid) = (s.clone(), uuid.clone());
                run_async(move || system::net_service_delete(&uuid), move |_| reload(&s, "network"));
            });
            row.add_row(&rm);
        }
        svc.add(&row);
    }
    if lst.is_empty() {
        svc.add(&adw::ActionRow::builder().title(tr!("No network services")).subtitle(tr!("NetworkManager (nmcli) not available or nothing configured")).build());
    }
}

fn import_vpn(s: &Weak<Settings>) {
    let Some(settings) = s.upgrade() else { return };
    let f = gtk::FileFilter::new();
    f.set_name(Some(&tr!("VPN configurations")));
    for pat in ["*.ovpn", "*.conf"] {
        f.add_pattern(pat);
    }
    let filters = gio::ListStore::new::<gtk::FileFilter>();
    filters.append(&f);
    let dlg = gtk::FileDialog::builder().title(tr!("Import VPN Configuration")).modal(true).filters(&filters).build();
    let s = s.clone();
    dlg.open(Some(&settings.window), gio::Cancellable::NONE, move |res| {
        let Some(path) = res.ok().and_then(|f| f.path()) else { return };
        let path = path.to_string_lossy().into_owned();
        let s = s.clone();
        run_async(move || system::vpn_import(&path), move |(ok, err)| {
            toast(&s, &if ok { tr!("VPN added") } else { fmt(&tr!("Couldn't import: {error}"), &[("error", &err)]) });
            reload(&s, "network");
        });
    });
}

// -- Bluetooth ------------------------------------------------------------------------------------
// system.py's Bluetooth calls go to BlueZ over D-Bus when it's there
// (sonata_system::bluez; bluetoothctl in one-shot mode says yes to
// connections that never happen), else bluetoothctl. sonata-backend can't
// reach sonata-system, so the choice is made here.

/// system.bluetooth_devices
fn bt_devices() -> Vec<system::BtDevice> {
    #[cfg(feature = "system")]
    if sonata_system::bluez::available() {
        return sonata_system::bluez::devices(true).iter().map(|d| d.basic()).collect();
    }
    system::bluetooth_devices()
}

/// system.bluetooth_connect_result: connect (pairing and trusting first
/// if needed) or disconnect, and whether it really happened.
fn bt_connect(mac: &str, on: bool) -> (bool, String) {
    #[cfg(feature = "system")]
    if sonata_system::bluez::available() {
        return if on { sonata_system::bluez::connect(mac) } else { sonata_system::bluez::disconnect(mac) };
    }
    system::bluetooth_connect_result(mac, on)
}

/// system.bluetooth_forget: it can be paired again from Nearby Devices.
fn bt_forget(mac: &str) -> (bool, String) {
    #[cfg(feature = "system")]
    if sonata_system::bluez::available() {
        return sonata_system::bluez::forget(mac);
    }
    system::bluetooth_forget(mac)
}

/// system.bluetooth_pair_again: forget, then pair and connect anew (the
/// device in pairing mode).
fn bt_pair_again(mac: &str) -> (bool, String) {
    let (ok, msg) = bt_forget(mac);
    if !ok {
        return (ok, msg);
    }
    #[cfg(feature = "system")]
    if sonata_system::bluez::available() {
        use sonata_system::bluez;
        bluez::discovery(true);
        for _ in 0..20 {
            // it shows up again once in pairing mode
            if bluez::devices(false).iter().any(|d| d.mac == mac) {
                return bluez::connect(mac);
            }
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
        return (false, tr!("Removed; put the device in pairing mode, then Connect it in Nearby Devices"));
    }
    (false, tr!("Removed; pair it again from Nearby Devices"))
}

fn bt_discovery(on: bool) {
    #[cfg(feature = "system")]
    run_async(move || sonata_system::bluez::discovery(on), |_| ());
    #[cfg(not(feature = "system"))]
    let _ = on;
}

/// The Bluetooth page's nearby devices and its search for them.
struct Bt {
    settings: Weak<Settings>,
    me: Weak<Bt>,
    near: adw::PreferencesGroup,
    near_rows: RefCell<HashMap<String, adw::ActionRow>>,
    /// the saved devices as the page shows them (connected or not)
    shown: RefCell<Option<HashMap<String, bool>>>,
    scan_src: RefCell<Option<glib::SourceId>>,
}

pub fn page_bluetooth(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let top = group("", "");
    let devs = group(&tr!("My Devices"), "");
    let near = group(&tr!("Nearby Devices"), &tr!("Put a device in pairing mode to see it here."));
    if let Some(old) = ctx.kept::<Bt>("bluetooth") {
        old.stop_polling(); // the page built again: one search, the new page's
    } else {
        // the search for devices ends with the window
        let s = Rc::downgrade(ctx);
        ctx.on_close(move || {
            if let Some(bt) = s.upgrade().and_then(|s| s.kept::<Bt>("bluetooth")) {
                bt.scan(false);
            }
        });
    }
    let bt = Rc::new_cyclic(|me| Bt { settings: Rc::downgrade(ctx), me: me.clone(), near: near.clone(), near_rows: RefCell::new(HashMap::new()), shown: RefCell::new(None), scan_src: RefCell::new(None) });
    ctx.keep("bluetooth", bt.clone());
    let me = Rc::downgrade(&bt);
    let (t, d) = (top.clone(), devs.clone());
    run_async(|| (system::bluetooth_state(), bt_devices()), move |(state, devices)| {
        if let Some(bt) = me.upgrade() {
            bt.fill(&t, &d, state, devices);
        }
    });
    vec![top, devs, near]
}

impl Bt {
    fn fill(&self, top: &adw::PreferencesGroup, devs: &adw::PreferencesGroup, state: Option<bool>, devices: Vec<system::BtDevice>) {
        let Some(state) = state else {
            top.add(&adw::ActionRow::builder().title("Bluetooth").subtitle(tr!("No Bluetooth adapter found")).build());
            devs.set_visible(false);
            self.near.set_visible(false);
            return;
        };
        let s = self.settings.clone();
        top.add(&switch_row("Bluetooth", state, move |on| {
            let s = s.clone();
            run_async(move || system::set_bluetooth(on), move |_| reload(&s, "bluetooth"));
        }, ""));
        let (paired, nearby): (Vec<_>, Vec<_>) = devices.into_iter().partition(|d| d.paired);
        *self.shown.borrow_mut() = Some(paired_state(&paired));
        for d in &paired {
            devs.add(&self.device_row(d));
        }
        if paired.is_empty() {
            devs.add(&adw::ActionRow::builder().title(tr!("No devices")).build());
        }
        self.fill_near(&nearby);
        if state {
            #[cfg(feature = "system")]
            sonata_system::bluez::register_agent(); // answers "pair?" (on this, the GTK thread)
            self.scan(true);
        } else {
            self.near.set_visible(false);
        }
    }

    /// A saved device: Connect / Disconnect and its (i) menu (macOS).
    fn device_row(&self, d: &system::BtDevice) -> adw::ActionRow {
        let sub = if d.connected {
            tr!("Connected")
        } else if d.paired {
            tr!("Not Connected")
        } else {
            String::new()
        };
        let row = adw::ActionRow::builder().title(&d.name).use_markup(false).subtitle(sub).build();
        let btn = sonata_ui::controls::push_button(&if d.connected { tr!("Disconnect") } else { tr!("Connect") }, || (), "");
        btn.set_valign(gtk::Align::Center);
        let (s, mac, on) = (self.settings.clone(), d.mac.clone(), !d.connected);
        btn.connect_clicked(move |b| {
            b.set_sensitive(false);
            b.set_label(&if on { tr!("Connecting…") } else { tr!("Disconnecting…") });
            let (s, mac) = (s.clone(), mac.clone());
            run_async(move || bt_connect(&mac, on), move |(_ok, msg)| {
                toast(&s, &msg);
                reload(&s, "bluetooth");
            });
        });
        row.add_suffix(&btn);
        if d.paired {
            let more = gtk::Button::builder().icon_name("view-more-horizontal-symbolic").valign(gtk::Align::Center).css_classes(["flat", "circular"]).tooltip_text(tr!("Options")).build();
            let (s, d) = (self.settings.clone(), d.clone());
            more.connect_clicked(move |b| device_menu(&s, b, &d));
            row.add_suffix(&more);
        }
        row
    }

    fn fill_near(&self, nearby: &[system::BtDevice]) {
        let mut rows = self.near_rows.borrow_mut();
        rows.retain(|mac, row| {
            let keep = nearby.iter().any(|d| &d.mac == mac);
            if !keep {
                self.near.remove(row);
            }
            keep
        });
        for d in nearby {
            if rows.contains_key(&d.mac) {
                continue;
            }
            let row = adw::ActionRow::builder().title(&d.name).use_markup(false).build();
            let btn = sonata_ui::controls::push_button(&tr!("Connect"), || (), "");
            btn.set_valign(gtk::Align::Center);
            let (me, mac) = (self.me.clone(), d.mac.clone());
            btn.connect_clicked(move |b| {
                b.set_sensitive(false);
                b.set_label(&tr!("Connecting…"));
                let (me, mac) = (me.clone(), mac.clone());
                run_async(move || bt_connect(&mac, true), move |(ok, msg)| {
                    let Some(bt) = me.upgrade() else { return };
                    toast(&bt.settings, &msg);
                    if ok {
                        reload(&bt.settings, "bluetooth");
                    } else {
                        let me = bt.me.clone();
                        glib::idle_add_local_once(move || {
                            if let Some(bt) = me.upgrade() {
                                bt.poll();
                            }
                        });
                    }
                });
            });
            row.add_suffix(&btn);
            self.near.add(&row);
            rows.insert(d.mac.clone(), row);
        }
    }

    /// Look for nearby devices while the Bluetooth section shows (macOS);
    /// the list follows every 3 s; stops when another section is chosen.
    fn scan(&self, on: bool) {
        self.stop_polling();
        bt_discovery(on);
        if on {
            let me = self.me.clone();
            let src = glib::timeout_add_seconds_local(3, move || me.upgrade().map_or(glib::ControlFlow::Break, |bt| bt.poll()));
            *self.scan_src.borrow_mut() = Some(src);
        }
    }

    fn stop_polling(&self) {
        if let Some(src) = self.scan_src.borrow_mut().take() {
            src.remove();
        }
    }

    fn poll(&self) -> glib::ControlFlow {
        if self.settings.upgrade().and_then(|s| s.current()) != Some("bluetooth") {
            // (from the timer itself: it ends with Break, not removed twice)
            self.scan_src.borrow_mut().take();
            self.scan(false);
            return glib::ControlFlow::Break;
        }
        let me = self.me.clone();
        run_async(bt_devices, move |devs| {
            let Some(bt) = me.upgrade() else { return };
            // a saved device that connected or dropped since the page was built: show it
            let changed = bt.shown.borrow().as_ref().is_some_and(|shown| *shown != paired_state(&devs));
            if changed {
                reload(&bt.settings, "bluetooth");
                return;
            }
            let nearby: Vec<_> = devs.into_iter().filter(|d| !d.paired).collect();
            bt.fill_near(&nearby);
        });
        glib::ControlFlow::Continue
    }
}

/// Forget This Device… / Forget and Pair Again…, each asked first.
fn device_menu(s: &Weak<Settings>, anchor: &gtk::Button, d: &system::BtDevice) {
    use sonata_ui::menu::{popup, Item, Options};
    let ask = |title: String, body: String, label: String, f: fn(&str) -> (bool, String)| {
        let (s, mac) = (s.clone(), d.mac.clone());
        move || {
            let (s2, mac) = (s.clone(), mac.clone());
            let parent = s.upgrade().and_then(|s| s.parent());
            sonata_ui::dialog::alert(&title, &body, &[("cancel", tr!("Cancel"), ""), ("go", label.clone(), "destructive")], move |r| {
                if r == "go" {
                    let (s, mac) = (s2.clone(), mac.clone());
                    run_async(move || f(&mac), move |(_ok, msg)| {
                        toast(&s, &msg);
                        reload(&s, "bluetooth");
                    });
                }
            }, parent.as_ref());
        }
    };
    let forget = ask(
        fmt(&tr!("Forget {name}?"), &[("name", &d.name)]),
        tr!("It won't connect by itself any more. To use it again, pair it from Nearby Devices."),
        tr!("Forget Device"),
        bt_forget,
    );
    let again = ask(
        fmt(&tr!("Pair {name} again?"), &[("name", &d.name)]),
        tr!("Put the device in pairing mode first. Sonata forgets it and pairs it anew (this fixes most devices that stopped connecting)."),
        tr!("Pair Again"),
        bt_pair_again,
    );
    popup(anchor, vec![vec![Item::new(&tr!("Forget This Device…"), forget), Item::new(&tr!("Forget and Pair Again…"), again)]], Options::below());
}

// -- Printers & Scanners ----------------------------------------------------------------------------
pub fn page_printers(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let g = group(&tr!("Printers"), "");
    g.add(&adw::ActionRow::builder().title(tr!("Loading…")).build());
    let add = header_button("list-add-symbolic", &tr!("Add Printer…"));
    add.connect_clicked(|_| system::add_printer());
    g.set_header_suffix(Some(&add));
    let (s, grp) = (Rc::downgrade(ctx), g.clone());
    run_async(|| (system::printers(), system::cups_running()), move |(lst, running)| {
        clear_group(&grp);
        let again = {
            let s = s.clone();
            Some(Rc::new(move || reload(&s, "printers")) as Rc<dyn Fn()>)
        };
        match printing(lst.is_some(), running, None) {
            Printing::Off => {
                let opts = Install { ready: Some(Rc::new(system::cups_running)), on_ready: again, label: Some(tr!("Turn On…")), confirm: None };
                grp.add(&install_row(&tr!("Printing is off"), &tr!("CUPS is installed but not running"), Some(system::CUPS_ON), opts).0);
                add.set_sensitive(false);
            }
            Printing::Missing(cmd) => {
                let opts = Install { ready: Some(Rc::new(|| system::printers().is_some())), on_ready: again, ..Default::default() };
                grp.add(&install_row(&tr!("Printing isn't set up"), &tr!("Install CUPS to add printers"), cmd.as_deref(), opts).0);
                add.set_sensitive(false);
            }
            Printing::Ready => {
                let lst = lst.unwrap_or_default();
                for p in &lst {
                    let mut sub = gettext(&p.state);
                    if p.default {
                        sub = format!("{sub} · {}", tr!("Default"));
                    }
                    let row = adw::ActionRow::builder().title(&p.name).use_markup(false).subtitle(sub).build();
                    row.add_prefix(&gtk::Image::builder().icon_name("printer-symbolic").pixel_size(24).build());
                    if !p.default {
                        let (s, name) = (s.clone(), p.name.clone());
                        let b = sonata_ui::controls::push_button(&tr!("Make Default"), move || {
                            let (s, name) = (s.clone(), name.clone());
                            run_async(move || system::set_default_printer(&name), move |_| reload(&s, "printers"));
                        }, "");
                        b.set_valign(gtk::Align::Center);
                        row.add_suffix(&b);
                    }
                    grp.add(&row);
                }
                if lst.is_empty() {
                    grp.add(&adw::ActionRow::builder().title(tr!("No printers available")).subtitle(tr!("Click + to add a printer")).build());
                }
            }
        }
    });
    vec![g]
}

// -- Sharing ----------------------------------------------------------------------------------------
pub fn page_sharing(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let g = group("", "");
    let entry = adw::EntryRow::builder().title(tr!("Computer Name")).show_apply_button(true).build();
    let host = glib::host_name().to_string();
    entry.set_text(&host);
    let e = entry.downgrade();
    // hostnamectl: off the main loop
    run_async(system::computer_name, move |n| {
        if let (false, Some(e)) = (n.is_empty(), e.upgrade()) {
            e.set_text(&n);
        }
    });
    let s = Rc::downgrade(ctx);
    entry.connect_apply(move |e| {
        let (s, name) = (s.clone(), e.text().trim().to_string());
        run_async(move || system::set_computer_name(&name), move |ok| toast(&s, &if ok { tr!("Computer name changed") } else { tr!("Couldn't change the name") }));
    });
    g.add(&entry);
    g.add(&adw::ActionRow::builder().title(tr!("Local hostname")).subtitle(format!("{host}.local")).use_markup(false).build());
    #[cfg(feature = "system")]
    return vec![g, screen_sharing_group(ctx)];
    #[cfg(not(feature = "system"))]
    vec![g]
}

/// See and control this screen from another computer or a phone
/// (sonata_system::screenshare: wayvnc). Vini: Chrome Remote Desktop
/// can't show a Wayfire session.
#[cfg(feature = "system")]
fn screen_sharing_group(ctx: &Ctx) -> adw::PreferencesGroup {
    use serde_json::{json, Value};
    use sonata_system::screenshare as S;
    let g = group(&tr!("Screen Sharing"), &tr!("Others can see and control this screen with a VNC viewer (RealVNC Viewer, TigerVNC) and the password below."));
    if !S::installed() {
        let s = Rc::downgrade(ctx);
        let opts = Install { ready: Some(Rc::new(S::installed)), on_ready: Some(Rc::new(move || reload(&s, "sharing"))), ..Default::default() };
        g.add(&install_row(&tr!("Screen Sharing needs wayvnc"), &tr!("Not installed"), system::install_command("wayvnc", None).as_deref(), opts).0);
        return g;
    }
    let cfg = sonata_config::load(S::NAME, &S::defaults());
    let on = cfg.get("screen").and_then(Value::as_bool).unwrap_or(false);
    let mut details: Vec<gtk::Widget> = Vec::new();
    let mut shown = vec![(String::new(), tr!("Main display"))];
    shown.extend(system::displays().into_iter().map(|d| {
        let label = if d.description.is_empty() { d.name.clone() } else { format!("{} — {}", d.name, d.description) };
        (d.name, label)
    }));
    let output = cfg.get("output").and_then(Value::as_str).unwrap_or("").to_string();
    let s = Rc::downgrade(ctx);
    let display = crate::kit::combo_row(&tr!("Display"), shown, &output, move |v: &String| {
        if let Some(s) = s.upgrade() {
            s.save(S::NAME, "output", json!(v));
        }
    }, &tr!("The one others see"));
    details.push(display.upcast());
    let place = adw::ActionRow::builder().title(tr!("Address")).use_markup(false).subtitle("…").build();
    place.set_subtitle_lines(0);
    let w = place.downgrade();
    run_async(S::addresses, move |addrs| {
        if let Some(place) = w.upgrade() {
            let text = addresses_text(&addrs);
            place.set_subtitle(&if text.is_empty() { tr!("No network") } else { text });
        }
    });
    details.push(place.upcast());
    let (user, pw) = S::credentials();
    let login = adw::ActionRow::builder().title(tr!("User and password")).use_markup(false).subtitle(format!("{user}  ·  {pw}")).build();
    let bx = gtk::Box::builder().spacing(8).valign(gtk::Align::Center).build();
    let s = Rc::downgrade(ctx);
    bx.append(&sonata_ui::controls::push_button(&tr!("Copy"), move || {
        if let Some(s) = s.upgrade() {
            s.window.clipboard().set_text(&S::credentials().1);
            s.toast(&tr!("Password copied"));
        }
    }, ""));
    let (s, l) = (Rc::downgrade(ctx), login.downgrade());
    bx.append(&sonata_ui::controls::push_button(&tr!("New Password"), move || {
        S::new_password();
        if let Some(l) = l.upgrade() {
            l.set_subtitle(&format!("{user}  ·  {}", S::credentials().1));
        }
        if let Some(s) = s.upgrade() {
            // wayvnc restarts
            let rev = sonata_config::load(S::NAME, &S::defaults()).get("rev").and_then(Value::as_i64).unwrap_or(0);
            s.save(S::NAME, "rev", json!(rev + 1));
            s.toast(&tr!("New password: viewers connected now are disconnected"));
        }
    }, ""));
    login.add_suffix(&bx);
    details.push(login.upcast());
    let far = adw::ActionRow::builder()
        .title(tr!("From outside your network"))
        .use_markup(false)
        .subtitle(tr!("Install Tailscale on this computer and on the other device, then use the Tailscale address. Never open port 5900 on your router."))
        .build();
    far.set_subtitle_lines(0);
    details.push(far.upcast());
    let s = Rc::downgrade(ctx);
    let rows = details.clone();
    g.add(&switch_row(&tr!("Screen Sharing"), on, move |on| {
        if let Some(s) = s.upgrade() {
            s.save(S::NAME, "screen", json!(on));
        }
        for d in &rows {
            d.set_visible(on);
        }
    }, ""));
    for d in &details {
        d.set_visible(on);
        g.add(d);
    }
    g
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn signal_bars() {
        assert_eq!(signal_icon(80), "network-wireless-signal-excellent-symbolic");
        assert_eq!(signal_icon(75), "network-wireless-signal-good-symbolic");
        assert_eq!(signal_icon(30), "network-wireless-signal-ok-symbolic");
        assert_eq!(signal_icon(0), "network-wireless-signal-weak-symbolic");
    }

    #[test]
    fn service_kinds() {
        assert_eq!(kind_label("ethernet"), "Ethernet");
        assert_eq!(kind_label("vpn"), "VPN");
        assert_eq!(kind_label("wireguard"), "Wireguard");
        assert_eq!(title_case("ip-tunnel"), "Ip-Tunnel");
    }

    // Vini: "Manage Printing" opened a localhost:631 that refused (CUPS not running).
    #[test]
    fn test_off_offers_turn_on() {
        assert_eq!(printing(true, false, Some("arch")), Printing::Off);
    }

    #[test]
    fn test_missing_offers_install() {
        assert_eq!(printing(false, false, Some("arch")), Printing::Missing(Some(format!("sudo pacman -S --needed cups && {}", system::CUPS_ON))));
        assert_eq!(printing(false, false, Some("")), Printing::Missing(None));
    }

    #[test]
    fn test_running_lists() {
        assert_eq!(printing(true, true, Some("arch")), Printing::Ready);
    }

    #[test]
    fn screen_sharing_addresses() {
        let a = vec![("100.1.2.3:5900".to_string(), "tailscale0".to_string()), ("192.168.0.2:5900".to_string(), "wlan0".to_string())];
        assert_eq!(addresses_text(&a), "100.1.2.3:5900  (Tailscale)\n192.168.0.2:5900  (wlan0)");
        assert_eq!(addresses_text(&[]), "");
    }

    #[test]
    fn saved_devices_compared() {
        let d = |mac: &str, paired, connected| system::BtDevice { mac: mac.into(), name: mac.into(), paired, connected };
        let st = paired_state(&[d("a", true, true), d("b", false, false)]);
        assert_eq!(st, HashMap::from([("a".to_string(), true)]));
    }

    /// The Bluetooth switch said it made this computer discoverable as
    /// long as Settings was open; nothing made it so.
    #[test]
    fn test_bluetooth_does_not_claim_discoverable() {
        assert!(!include_str!("network.rs").contains(concat!("discoverable", " while")));
    }

    /// Sharing shows Screen Sharing (the menu bar runs it), with the
    /// warning about port 5900.
    #[test]
    fn test_menu_bar_runs_it_and_settings_shows_it() {
        let src = include_str!("network.rs");
        assert!(src.contains(concat!("screen_sharing_group(ctx)", "]")));
        assert!(src.contains(concat!("Never open port ", "5900")));
    }
}
