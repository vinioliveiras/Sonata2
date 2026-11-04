use super::*;

const DDC_V2: &str = "Display 1\n   I2C bus:  /dev/i2c-7\n   DRM connector:           card1-HDMI-A-1\n   Monitor:                 AOC:Q27G2G4:1A2B3C\n\nInvalid display\n   I2C bus:  /dev/i2c-3\n   DRM connector:           card2-eDP-1\n";
const DDC_V1: &str = "Display 1\n   I2C bus:  /dev/i2c-5\n   Monitor:             AOC:Q27G2G4:1A2B3C\n";

fn edid(mfg: &str, name: &str, serial: &str) -> Vec<u8> {
    let mut e = vec![0u8; 128];
    e[0..8].copy_from_slice(b"\x00\xff\xff\xff\xff\xff\xff\x00");
    let v: u32 = mfg.chars().zip([10, 5, 0]).map(|(c, s)| (c as u32 - 64) << s).sum();
    e[8] = (v >> 8) as u8;
    e[9] = (v & 0xFF) as u8;
    for (off, tag, text) in [(72, 0xFC, name), (90, 0xFF, serial)] {
        e[off..off + 5].copy_from_slice(&[0, 0, 0, tag, 0]);
        let mut t = format!("{text}\n").into_bytes();
        t.resize(13, b' ');
        e[off + 5..off + 18].copy_from_slice(&t);
    }
    e
}

#[test]
fn nmcli_escapes() {
    assert_eq!(split_nmcli(r"yes:My\:Net:80"), ["yes", "My:Net", "80"]);
    assert_eq!(joined("ethernet:unavailable:\nwifi:connected:Casa\n"), "Casa");
    assert!(wired("ethernet:connected:Wired 1\n"));
}

#[test]
fn wifi_list_keeps_the_strongest_and_the_joined_one_first() {
    let out = " :Vizinho:90:WPA2\n*:Casa:40:WPA2\n :Casa:70:WPA2\n :Cafe:50:--\n :::\n";
    let nets = parse_wifi_list(out);
    assert_eq!(nets.iter().map(|n| n.ssid.as_str()).collect::<Vec<_>>(), ["Casa", "Vizinho", "Cafe"]);
    assert_eq!((nets[0].signal, nets[0].connected, nets[0].secure), (70, true, true));
    assert!(!nets[2].secure);
}

#[test]
fn ddc_detect_both_versions() {
    assert_eq!(parse_ddc_detect(DDC_V2), [DdcDisplay { bus: 7, connector: "card1-HDMI-A-1".into(), mfg: "AOC".into(), model: "Q27G2G4".into(), serial: "1A2B3C".into() }]);
    let v1 = parse_ddc_detect(DDC_V1);
    assert_eq!((v1[0].bus, v1[0].connector.as_str()), (5, ""));
}

#[test]
fn edid_identity() {
    assert_eq!(edid_id_of(&edid("AOC", "Q27G2G4", "1A2B3C")), Some(("AOC".into(), "Q27G2G4".into(), "1A2B3C".into())));
    assert_eq!(edid_id_of(&[0; 10]), None);
}

#[test]
fn names_of_the_machine_and_its_gpus() {
    assert_eq!(machine_name("ASUSTeK COMPUTER INC.", "ASUS TUF Gaming A15", "ASUS TUF Gaming A15 FA507NV_FA507NV"), "ASUS TUF Gaming A15 FA507NV");
    assert_eq!(machine_name("LENOVO", "ThinkPad X1", "20XW"), "LENOVO ThinkPad X1 20XW");
    assert_eq!(gpu_name("NVIDIA Corporation", "AD107M [GeForce RTX 4060 Max-Q / Mobile]"), "NVIDIA GeForce RTX 4060 Max-Q / Mobile");
    assert_eq!(gpu_name("Advanced Micro Devices, Inc. [AMD/ATI]", "Phoenix1"), "AMD Phoenix1");
}

#[test]
fn brightness_levels_keep_the_panel_lit() {
    assert_eq!(to_level(5.0), 0);
    assert_eq!(to_level(100.0), 100);
    assert_eq!(to_percent(0.0), MIN_BRIGHTNESS);
    assert_eq!(to_level(to_percent(50.0)), 50);
    assert!(is_builtin(None) && is_builtin(Some("eDP-1")) && !is_builtin(Some("HDMI-A-1")));
}

#[test]
fn wpctl_volume_and_status() {
    assert_eq!(parse_wpctl_volume("Volume: 0.45"), Some((45, false)));
    assert_eq!(parse_wpctl_volume("Volume: 0.30 [MUTED]"), Some((30, true)));
    assert_eq!(parse_wpctl_volume("nothing"), None);
    let status = "PipeWire 'pipewire-0' [1.2.0]\n └─ Clients:\n        33. WirePlumber\n\nAudio\n ├─ Devices:\n │      50. Built-in Audio                      [alsa]\n │  \n ├─ Sinks:\n │  *   56. Built-in Audio Analog Stereo        [vol: 0.45]\n │      60. HDMI Output                          [vol: 1.00]\n │  \n ├─ Sources:\n │  *   57. Built-in Audio Analog Stereo        [vol: 1.00]\n\nVideo\n ├─ Sinks:\n │      90. Not audio\n";
    let sinks = parse_wpctl_status(status, "Sinks");
    assert_eq!(sinks, [AudioSink { id: 56, name: "Built-in Audio Analog Stereo".into(), default: true }, AudioSink { id: 60, name: "HDMI Output".into(), default: false }]);
    assert_eq!(parse_wpctl_status(status, "Sources").len(), 1);
}

#[test]
fn pactl_devices_one_per_port() {
    let json = r#"[{"name":"alsa_output.pci","description":"Built-in Audio","active_port":"analog-output-headphones",
        "ports":[{"name":"analog-output-speaker","description":"Speakers","availability":"available"},
                 {"name":"analog-output-headphones","description":"Headphones","availability":"available"},
                 {"name":"hdmi","description":"HDMI","availability":"not available"}]},
        {"name":"sonata-eq-sink","description":"EQ","ports":[]},
        {"name":"bluez_output.x","description":"Buds","ports":[]}]"#;
    let devs = parse_pactl_devices(json, "alsa_output.pci", "sinks").unwrap();
    assert_eq!(devs.iter().map(|d| d.name.as_str()).collect::<Vec<_>>(), ["Speakers", "Headphones", "Buds"]);
    assert!(devs[1].default && !devs[0].default);
    assert_eq!(devs[2].key, "bluez_output.x|");
}

#[test]
fn printers_from_lpstat() {
    let p = parse_printers("system default destination: HP\n", "printer HP is idle.  enabled since x\nprinter Canon disabled since y\nprinter Epson now printing Epson-3.\n");
    assert_eq!(p.iter().map(|x| (x.name.as_str(), x.state.as_str(), x.default)).collect::<Vec<_>>(), [("HP", "Idle", true), ("Canon", "Disabled", false), ("Epson", "Printing", false)]);
}

#[test]
fn package_families() {
    assert_eq!(package_family_from("ID=cachyos\nID_LIKE=arch\n"), Some("arch"));
    assert_eq!(package_family_from("ID=linuxmint\nID_LIKE=\"ubuntu debian\"\n"), Some("debian"));
    assert_eq!(package_family_from("ID=nixos\n"), None);
    assert_eq!(install_command("ddcutil", Some("arch")).as_deref(), Some("sudo pacman -S --needed ddcutil"));
}

#[test]
fn wlr_randr_outputs() {
    let out = "eDP-1 \"BOE 0x0BCA (eDP-1)\"\n  Enabled: yes\n  Modes:\n    1920x1080 px, 60.000000 Hz\n    1920x1080 px, 144.003006 Hz (preferred, current)\n  Position: 0,0\n  Transform: 90\n  Scale: 1.250000\n";
    let d = &parse_wlr_randr(out)[0];
    assert_eq!((d.name.as_str(), d.description.as_str(), d.modes.len()), ("eDP-1", "BOE 0x0BCA (eDP-1)", 2));
    assert_eq!(d.current, "1920x1080@144.003006");
    assert_eq!((d.scale, d.transform.as_str()), (1.25, "90"));
    assert_eq!(d.size(), (864, 1536));
}

#[test]
fn layouts_and_host_names() {
    assert_eq!(join_layouts("br,us", ",intl"), ["br", "us(intl)"]);
    assert_eq!(split_layouts(&["br".into(), "us(intl)".into()]), ("br,us".into(), ",intl".into()));
    assert_eq!(host_name_of("Vini's Laptop"), "vini-s-laptop");
    assert_eq!(host_name_of("!!"), "computer");
}

#[test]
fn battery_and_mains() {
    let d = tempfile::tempdir().unwrap();
    let ps = d.path().to_str().unwrap();
    std::fs::create_dir_all(d.path().join("BAT0")).unwrap();
    std::fs::write(d.path().join("BAT0/capacity"), "73\n").unwrap();
    std::fs::write(d.path().join("BAT0/status"), "Discharging\n").unwrap();
    assert_eq!(battery(ps), (Some(73), "Discharging".into()));
    assert!(!on_ac(ps));
    std::fs::create_dir_all(d.path().join("AC0")).unwrap();
    std::fs::write(d.path().join("AC0/online"), "1\n").unwrap();
    assert!(on_ac(ps));
    let empty = tempfile::tempdir().unwrap();
    assert_eq!(battery(empty.path().to_str().unwrap()), (None, String::new()));
}
