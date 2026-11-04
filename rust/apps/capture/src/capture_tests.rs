//! capture.rs' tests: tests/test_capture_frozen.py, test_print_screen.py,
//! test_shot_clipboard.py, test_capture_entry.py and the capture cases of
//! test_regressions.py / test_review_topbar.py / test_gamebar_complete.py.

use super::*;
use gtk::gdk_pixbuf::Colorspace;

fn outs() -> Vec<Value> {
    vec![json!({"name": "HDMI-A-1", "geometry": {"x": 0, "y": 0, "width": 40, "height": 20}}), json!({"name": "eDP-1", "geometry": {"x": 40, "y": 0, "width": 20, "height": 20}})]
}

/// Left display red, right display blue.
fn screen(scale: i32) -> Pixbuf {
    let pb = Pixbuf::new(Colorspace::Rgb, false, 8, 60 * scale, 20 * scale).unwrap();
    pb.fill(0xff0000ff);
    pb.new_subpixbuf(40 * scale, 0, 20 * scale, 20 * scale).fill(0x0000ffff);
    pb
}

fn rgb(pb: &Pixbuf, x: i32, y: i32) -> (u8, u8, u8) {
    let px = pb.read_pixel_bytes();
    let i = (y * pb.rowstride() + x * pb.n_channels()) as usize;
    (px[i], px[i + 1], px[i + 2])
}

fn cfg(v: Value) -> Cfg {
    v.as_object().unwrap().clone()
}

#[test]
fn test_crop_and_display() {
    let f = Frozen::new(screen(1), &outs());
    let part = f.crop("35,5 10x10").unwrap(); // across both displays
    assert_eq!((part.width(), part.height()), (10, 10));
    assert_eq!(rgb(&part, 0, 0), (255, 0, 0));
    assert_eq!(rgb(&part, 9, 9), (0, 0, 255));
    let edp = f.output("eDP-1").unwrap();
    assert_eq!((edp.width(), rgb(&edp, 0, 0)), (20, (0, 0, 255)));
    assert!(f.crop("100,100 5x5").is_none()); // off screen
    assert!(f.crop("nonsense").is_none());
}

#[test]
fn test_scaled_screen() {
    let f = Frozen::new(screen(2), &outs()); // grim: the largest display scale
    let part = f.crop("38,0 4x2").unwrap();
    assert_eq!((part.width(), part.height()), (8, 4));
    assert_eq!((rgb(&part, 0, 0), rgb(&part, 7, 0)), ((255, 0, 0), (0, 0, 255)));
}

/// The screen is frozen before anything takes the keyboard, and the shot
/// is cut from it; a recording stays live.
#[test]
fn test_frozen_before_the_list_and_the_selection() {
    let none = || Vec::new();
    let p = plan("display", &Cfg::new(), &outs(), &none, &|| None);
    assert!(p.freeze);
    assert_eq!(p.step, Step::PickDisplay(vec!["HDMI-A-1".into(), "eDP-1".into()]));
    let p = plan("area", &Cfg::new(), &[], &none, &|| None);
    assert!(p.freeze && p.step == Step::Area);
    assert!(!plan("rec-area", &Cfg::new(), &[], &none, &|| None).freeze);
    assert!(!plan("screen", &Cfg::new(), &[], &none, &|| None).freeze);
    // recording pickers take it too: one grim for every thumbnail
    assert!(plan("rec-display", &Cfg::new(), &outs(), &none, &|| None).freeze);
}

/// Vini: with a timer, the list of displays / windows (and the portion)
/// came after the countdown; it comes first now, the countdown after the
/// choice, and the screen is taken after it (run_now: a timer shoots live).
#[test]
fn test_timer_came_before_choosing_what_to_capture() {
    let none = || Vec::new();
    let p = plan("display", &cfg(json!({"timer": 5})), &outs(), &none, &|| None);
    assert!(matches!(p.step, Step::PickDisplay(_)));
    assert_eq!(p.timer, 5);
    let p = plan("rec-display", &cfg(json!({"timer": 10})), &outs(), &none, &|| None);
    assert!(p.rec && matches!(p.step, Step::PickDisplay(_)) && p.timer == 10);
    assert_eq!(plan("area", &cfg(json!({"timer": 5})), &[], &none, &|| None).step, Step::Area);
}

/// Record a display / a window: pick it from a list (was: click on the screen).
#[test]
fn test_display_and_window_come_from_a_list() {
    let outs = vec![json!({"id": 1, "name": "HDMI-A-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}}), json!({"id": 2, "name": "eDP-1", "geometry": {"x": 1920, "y": 0, "width": 1920, "height": 1080}})];
    let views = || vec![json!({"role": "toplevel", "mapped": true, "layer": "workspace", "output-id": 2, "title": "Notes", "app-id": "x", "geometry": {"x": 10, "y": 20, "width": 300, "height": 200}})];
    let p = plan("rec-display", &Cfg::new(), &outs, &views, &|| None);
    assert_eq!(p.step, Step::PickDisplay(vec!["HDMI-A-1".into(), "eDP-1".into()]));
    let p = plan("window", &Cfg::new(), &outs, &views, &|| None);
    let Step::PickWindow(w) = p.step else { panic!("a list") };
    assert_eq!((w[0].title.as_str(), w[0].geo.as_str()), ("Notes", "1930,20 300x200"));
    // one display: no list
    let p = plan("display", &Cfg::new(), &outs[..1], &views, &|| Some("X".into()));
    assert_eq!(p.step, Step::Go { geo: None, output: Some("HDMI-A-1".into()) });
    // no window to pick: nothing
    assert_eq!(plan("window", &Cfg::new(), &outs, &Vec::new, &|| None).step, Step::Nothing);
}

/// Print / SysRq: the focused display at once, without toolbar or timer.
#[test]
fn test_print_shoots_the_focused_display_without_toolbar_or_timer() {
    let one = vec![json!({"id": 1, "name": "HDMI-A-1", "geometry": {}}), json!({"id": 2, "name": "DP-2", "geometry": {}})];
    let p = plan("edit", &cfg(json!({"timer": 5, "mode": "display"})), &one, &Vec::new, &|| Some("HDMI-A-1".into()));
    assert!(p.edit && !p.rec);
    assert_eq!(p.step, Step::Go { geo: None, output: Some("HDMI-A-1".into()) }); // never a list
    assert_eq!(p.cfg["timer"], json!(0));
    assert_eq!(p.timer, 0);
}

/// Vini: Print uses what was used last -- Capture a Window: the window in front.
#[test]
fn test_print_remembers_the_toolbars_last_mode() {
    let views = || {
        vec![
            json!({"app-id": "old", "last-focus-timestamp": 1, "output-id": 1, "geometry": {"x": 0, "y": 0, "width": 100, "height": 100}}),
            json!({"app-id": "front", "last-focus-timestamp": 9, "output-id": 1, "geometry": {"x": 10, "y": 20, "width": 300, "height": 200}}),
        ]
    };
    let outs = vec![json!({"id": 1, "name": "HDMI-A-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}})];
    let p = plan("edit", &cfg(json!({"mode": "window"})), &outs, &views, &|| None);
    assert_eq!(p.step, Step::Go { geo: Some("10,20 300x200".into()), output: None });
    assert!(p.edit);
    // no window: its display
    let p = plan("edit", &cfg(json!({"mode": "window"})), &outs, &Vec::new, &|| Some("HDMI-A-1".into()));
    assert_eq!(p.step, Step::Go { geo: None, output: Some("HDMI-A-1".into()) });
}

#[test]
fn test_print_selection_mode_draws_a_portion() {
    let p = plan("edit", &cfg(json!({"mode": "area"})), &[], &Vec::new, &|| None);
    assert!(p.edit && p.step == Step::Area);
}

#[test]
fn test_print_after_recording_mode_takes_a_still() {
    assert_eq!(print_mode("rec-window"), "window");
    assert_eq!(print_mode("rec-display"), "display");
    assert_eq!(print_mode("rec-area"), "area");
    assert_eq!(print_mode("???"), "display");
    let p = plan("edit", &cfg(json!({"mode": "rec-display"})), &[], &Vec::new, &|| Some("DP-1".into()));
    assert!(p.edit && !p.rec);
}

#[test]
fn test_print_keeps_folder_and_clipboard_choices() {
    let p = plan("edit", &cfg(json!({"mode": "display", "shots_to": "clipboard", "shots_copy": true})), &[], &Vec::new, &|| None);
    assert_eq!((&p.cfg["shots_to"], &p.cfg["shots_copy"]), (&json!("clipboard"), &json!(true)));
}

/// Vini: after Print the capture toolbar stays open, each shot opening in
/// the editor; closed (or never kept) it's the thumbnail again.
#[test]
fn test_toolbar_stays_after_print() {
    assert!(shot_opens_editor(true, false)); // Print: the editor
    assert!(shot_opens_editor(false, true)); // from the kept toolbar
    assert!(!shot_opens_editor(false, false)); // an ordinary shot: the thumbnail
}

/// Print / SysRq run `screenshot edit`, not the toolbar (config/wayfire.ini).
#[test]
fn test_print_key_captures_and_edits() {
    let ini = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../config/wayfire.ini")).unwrap();
    let line = |k: &str| ini.lines().find(|l| l.trim_start().starts_with(k)).map(|l| l.split_once('=').unwrap().1.trim().to_string()).unwrap();
    assert!(line("binding_capture_print").contains("KEY_PRINT"));
    assert_eq!(line("command_capture_print"), "sonata2 screenshot edit");
}

#[test]
fn test_window_list_filters_and_orders() {
    let o = vec![json!({"id": 1, "name": "eDP-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}}), json!({"id": 2, "name": "HDMI-A-1", "geometry": {"x": 1920, "y": 0, "width": 2560, "height": 1440}})];
    let v = |extra: Value| {
        let mut base = json!({"role": "toplevel", "mapped": true, "output-id": 1, "geometry": {"x": 10, "y": 20, "width": 300, "height": 200}});
        base.as_object_mut().unwrap().extend(extra.as_object().unwrap().clone());
        base
    };
    let views = vec![
        v(json!({"title": "old", "last-focus-timestamp": 1})),
        v(json!({"title": "new", "last-focus-timestamp": 9, "output-id": 2, "app-id": "kitty"})),
        v(json!({"title": "min", "minimized": true})),
        v(json!({"title": "panel", "role": "desktop-environment"})),
        v(json!({"title": "bg", "layer": "background"})),
        v(json!({"title": "unmapped", "mapped": false})),
        v(json!({"title": "other ws", "geometry": {"x": 1920, "y": 0, "width": 300, "height": 200}})),
        v(json!({"title": "zero", "geometry": {"x": 0, "y": 0, "width": 0, "height": 10}})),
        json!("junk"),
    ];
    let wins = window_list(&views, &o);
    assert_eq!(wins.iter().map(|w| w.title.as_str()).collect::<Vec<_>>(), ["new", "old"]);
    assert_eq!(wins[0].geo, "1930,20 300x200");
    assert_eq!(wins[0].app, "kitty");
    assert_eq!(window_boxes(&views, &o), ["1930,20 300x200", "10,20 300x200"]);
}

#[test]
fn test_window_boxes() {
    let o = vec![json!({"id": 1, "name": "HDMI-A-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}}), json!({"id": 2, "name": "eDP-1", "geometry": {"x": 1920, "y": 0, "width": 1920, "height": 1080}})];
    let v = |extra: Value| {
        let mut base = json!({"role": "toplevel", "mapped": true, "layer": "workspace", "output-id": 1, "geometry": {"x": 100, "y": 50, "width": 800, "height": 600}});
        base.as_object_mut().unwrap().extend(extra.as_object().unwrap().clone());
        base
    };
    let views = vec![
        v(json!({"last-focus-timestamp": 1})),
        v(json!({"output-id": 2, "last-focus-timestamp": 5})),                       // on the second display
        v(json!({"geometry": {"x": 2000, "y": 50, "width": 800, "height": 600}})), // another workspace
        v(json!({"minimized": true})),
        v(json!({"role": "desktop-environment"})),
    ];
    assert_eq!(window_boxes(&views, &o), ["2020,50 800x600", "100,50 800x600"]);
}

/// "x,y wxh" geometry parsing and the display holding a region.
#[test]
fn test_box_parse_and_output_at() {
    assert_eq!(parse_box("1.5,2 30x40"), Some((1.5, 2.0, 30.0, 40.0)));
    assert_eq!(parse_box("garbage"), None);
    assert_eq!(parse_box(""), None);
    let o = vec![json!({"id": 1, "name": "eDP-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}}), json!({"id": 2, "name": "HDMI-A-1", "geometry": {"x": 1920, "y": 0, "width": 2560, "height": 1440}})];
    assert_eq!(output_at("2000,100 50x50", &o).as_deref(), Some("HDMI-A-1"));
    assert_eq!(output_at("5,5 50x50", &o).as_deref(), Some("eDP-1"));
    assert_eq!(output_at("9000,0 1x1", &o), None);
    assert_eq!(output_at("nope", &o), None);
}

/// The screen recorder "didn't work well": with two displays wf-recorder
/// asked on a terminal which one and quit; 180 Hz recordings were heavy and
/// choppy; some players couldn't open them.
#[test]
fn test_command() {
    let cmd = recorder_command_with(Vec::new(), "/tmp/x.mp4", None, Some("HDMI-A-1"), None, "x264");
    let at = |k: &str| cmd[cmd.iter().position(|a| a == k).unwrap() + 1].clone();
    assert_eq!(at("-o"), "HDMI-A-1"); // one display, never a prompt
    assert_eq!(at("-r"), "60");
    assert_eq!(at("-x"), "yuv420p");
    let area = recorder_command_with(Vec::new(), "/tmp/x.mp4", Some("10,10 200x100"), Some("HDMI-A-1"), None, "x264");
    assert!(area.contains(&"-g".into()));
    assert!(!area.contains(&"-o".into())); // an area needs no display
}

/// wf-recorder gets geometry before output, audio inline; the last good
/// encoder is tried first.
#[test]
fn test_recorder_command_and_encoder_order() {
    let cmd = recorder_command_with(Vec::new(), "/v.mp4", Some("0,0 10x10"), Some("HDMI-A-1"), Some("sink.monitor"), "vaapi");
    assert_eq!(cmd[..6], ["wf-recorder", "-y", "-f", "/v.mp4", "-r", "60"]);
    assert!(cmd.contains(&"h264_vaapi".into()));
    assert!(cmd.contains(&"-g".into()) && !cmd.contains(&"-o".into()));
    assert_eq!(cmd.last().unwrap(), "--audio=sink.monitor");
    let niced = recorder_command_with(vec!["nice".into(), "-n".into(), "10".into()], "/v.mp4", None, None, None, "x264");
    assert_eq!(niced[..4], ["nice", "-n", "10", "wf-recorder"]);
    let no = |_: &str| false;
    let yes = |_: &str| true;
    assert_eq!(encoders_with(None, &no, &yes), ["nvenc", "vaapi", "x264"]);
    assert_eq!(encoders_with(Some("x264"), &no, &yes), ["x264", "nvenc", "vaapi"]);
    assert_eq!(encoders_with(Some("nvenc"), &no, &no), ["x264"]);
}

#[test]
fn test_sound() {
    assert_eq!(recorder_command_with(Vec::new(), "/tmp/x.mp4", None, None, Some("dev"), "x264").last().unwrap(), "--audio=dev");
    assert!(!recorder_command_with(Vec::new(), "/tmp/x.mp4", None, None, None, "x264").iter().any(|a| a.starts_with("--audio")));
    // what the speakers play; the microphone; none when pactl fails
    assert_eq!(device_name("system", 0, "alsa_output.hdmi\n").as_deref(), Some("alsa_output.hdmi.monitor"));
    assert_eq!(device_name("system", 0, "x.monitor").as_deref(), Some("x.monitor"));
    assert_eq!(device_name("mic", 0, "alsa_input.mic\n").as_deref(), Some("alsa_input.mic"));
    assert_eq!(device_name("system", 1, ""), None);
    assert_eq!(audio_device("none"), None);
}

/// Vini: the drawing palette stays out of recordings -- wl-screenrec
/// (ext-image-copy-capture) goes first when installed; wf-recorder after.
#[test]
fn test_screenrec_first() {
    let all = |_: &str| true;
    let order = encoders_with(None, &all, &all);
    assert_eq!(order[..2], ["screenrec", "screenrec-sw"]);
    assert_eq!(order.last().unwrap(), "x264");
    let no_screenrec = |n: &str| n != "wl-screenrec";
    assert!(!encoders_with(None, &no_screenrec, &all).contains(&"screenrec".into()));
    let cmd = recorder_command_with(Vec::new(), "/tmp/x.mp4", None, Some("HDMI-A-1"), Some("spk.monitor"), "screenrec-sw");
    let at = |k: &str| cmd[cmd.iter().position(|a| a == k).unwrap() + 1].clone();
    assert!(cmd.contains(&"wl-screenrec".into()) && cmd.contains(&"--no-hw".into()));
    assert_eq!(at("-o"), "HDMI-A-1");
    assert_eq!(at("--audio-device"), "spk.monitor");
    assert!(!recorder_command_with(Vec::new(), "/tmp/x.mp4", Some("1,1 9x9"), None, None, "screenrec").contains(&"--no-hw".into()));
    // a stored wf-recorder encoder (older settings, or after one fallback)
    // reorders wf-recorder's encoders only: wl-screenrec stays first
    for pref in ["vaapi", "x264", "nvenc"] {
        let order = encoders_with(Some(pref), &all, &all);
        assert_eq!(order[..3], ["screenrec", "screenrec-sw", pref], "{pref}");
    }
    assert_eq!(encoders_with(Some("screenrec-sw"), &all, &all)[..2], ["screenrec-sw", "screenrec"]);
}

#[test]
fn test_encoder_order_and_fallback() {
    let no_screenrec = |n: &str| n != "wl-screenrec";
    let all = |_: &str| true;
    let order = encoders_with(None, &no_screenrec, &all);
    assert_eq!(order.last().unwrap(), "x264"); // the CPU one always works, last
    assert_eq!(encoders_with(Some("x264"), &no_screenrec, &all)[0], "x264"); // the one that worked first
    assert!(recorder_command_with(Vec::new(), "/tmp/x.mp4", None, Some("eDP-1"), None, "nvenc").contains(&"h264_nvenc".into()));
    // neither recorder found: wf-recorder's list still (the missing tool is reported first)
    assert_eq!(encoders_with(None, &|_| false, &|_| false), ["x264"]);
}

#[test]
fn test_two_tracks_commands() {
    let cmd = tracks_command("/m/.a.mka", "sys.monitor", "mic");
    assert_eq!(cmd.iter().filter(|a| *a == "-f").count(), 2);
    assert!(cmd.contains(&"sys.monitor".into()));
    let i = cmd.iter().position(|a| a == "-map").unwrap();
    assert_eq!(cmd[i..i + 4], ["-map", "0:a", "-map", "1:a"]);
    let mux = mux_command("/m/v.mp4", "/m/a.mka", "/m/out.mp4");
    assert!(mux.join(" ").contains("amix=inputs=2"));
    assert_eq!(mux.iter().filter(|a| *a == "-map").count(), 4); // picture, mix, system, microphone
    assert_eq!(mux.last().unwrap(), "/m/out.mp4");
    // the picture silent beside its tracks, hidden until they're one movie
    assert_eq!(hidden_beside(Path::new("/m/Screen Recording 1.mp4"), ".video.mp4"), PathBuf::from("/m/.Screen Recording 1.video.mp4"));
}

#[test]
fn test_folders_per_kind() {
    let d = defaults();
    assert_eq!((d["shots_to"].as_str(), d["movies_to"].as_str()), (Some("pictures"), Some("videos")));
    assert_eq!(d["shots_copy"], json!(false));
    let other = tempfile::tempdir().unwrap();
    let o = other.path().display().to_string();
    assert_eq!(shots_dir(&cfg(json!({"shots_to": "other", "shots_dir": o}))), other.path());
    let new = other.path().join("new");
    assert_eq!(movies_dir(&cfg(json!({"movies_to": "other", "movies_dir": new.display().to_string()}))), new);
    assert!(new.is_dir()); // made when missing
}

#[test]
fn test_names() {
    let t = glib::DateTime::from_local(2026, 9, 29, 1, 52, 10.0).unwrap();
    assert_eq!(file_name("Screenshot", "png", &t), "Screenshot 2026-09-29 at 01.52.10.png");
    assert_eq!(file_name("Screen Recording", "mp4", &t), "Screen Recording 2026-09-29 at 01.52.10.mp4");
    assert_eq!(elapsed_text(7), "0:07");
    assert_eq!(elapsed_text(62), "1:02");
    assert_eq!(elapsed_text(3723), "1:02:03");
}

/// Vini: the capture toolbar should open on the last capture mode; one it
/// doesn't know opens on Capture a Display.
#[test]
fn test_toolbar_opens_on_the_last_mode() {
    assert_eq!(restored_mode(Some(&json!("rec-area"))), "rec-area");
    assert_eq!(restored_mode(Some(&json!("nonsense"))), "display");
    assert_eq!(restored_mode(None), "display");
}

/// Screenshot in Launchpad / the Apps Menu (Vini: it had no entry there).
#[test]
fn test_opens_the_capture_toolbar() {
    let (name, text) = capture_desktop_entry("sonata2");
    assert_eq!(name, "sonata2-screenshot.desktop");
    assert!(text.contains("Exec=sonata2 screenshot toolbar\n")); // the same as Super+Shift+5
    assert!(text.contains("Name=Screenshot\n") && text.contains("Categories=Utility;\n"));
    assert!(!text.contains("NoDisplay"));
}

/// The recording / sharing pills are bare, the red stop button their only colour.
#[test]
fn test_stop_is_the_only_colour() {
    let css: Value = serde_json::from_str(include_str!("../../../crates/sonata-ui/css.json")).unwrap();
    let all = css["shell/capture.py"].to_string();
    let block = |sel: &str| {
        let i = all.find(&format!("{sel} {{")).unwrap_or_else(|| panic!("{sel}"));
        all[i..i + all[i..].find('}').unwrap()].to_string()
    };
    assert!(block(".rec-pill").contains("background: none"));
    assert!(block(".rec-pill button").contains("sys_red"));
    assert!(!block(".rec-dot").contains("sys_red"));
    assert!(!all.contains("sys_purple"));
}

#[test]
fn test_python_truthiness() {
    assert!(!truthy(None) && !truthy(Some(&json!(0))) && !truthy(Some(&json!(""))) && !truthy(Some(&json!(null))));
    assert!(truthy(Some(&json!(1))) && truthy(Some(&json!("x"))) && truthy(Some(&json!(true))));
    assert_eq!(text(&cfg(json!({"audio": 3})), "audio", "system"), "");
    assert_eq!(text(&Cfg::new(), "audio", "system"), "system");
}
