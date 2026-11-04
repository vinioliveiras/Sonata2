//! tests/test_mixer.py, test_mixer_learn.py, test_mixer_owner.py and the
//! mixer half of test_webapp_tabs_audio.py.

use super::*;
use std::sync::atomic::{AtomicUsize, Ordering};

fn item(index: i64, name: &str, binary: &str, pct: &str, mute: bool, role: Option<&str>, app_id: Option<&str>, icon: Option<&str>, sink: i64) -> Value {
    let mut props = json!({"application.name": name, "application.process.binary": binary});
    if let Some(r) = role {
        props["media.role"] = json!(r);
    }
    if let Some(a) = app_id {
        props["application.id"] = json!(a);
    }
    if let Some(i) = icon {
        props["application.icon_name"] = json!(i);
    }
    json!({"index": index, "mute": mute, "corked": false, "properties": props, "sink": sink,
           "volume": {"front-left": {"value_percent": pct}, "front-right": {"value_percent": pct}}})
}

fn pactl_json() -> String {
    json!([
        item(10, "Firefox", "firefox", "40%", false, None, None, Some("firefox"), 1),
        item(11, "Firefox", "firefox", "40%", false, None, None, None, 1), // a second tab playing
        item(12, "Spotify", "spotify", "80%", true, None, Some("com.spotify.Client"), None, 2),
        item(13, "pw-play", "pw-play", "100%", false, None, None, None, 1), // Sonata's own sound
        item(14, "Chat", "chat", "100%", false, Some("event"), None, None, 1), // a ding
    ])
    .to_string()
}

fn parsed() -> Vec<Stream> {
    parse_with(&pactl_json(), None, &|_| None)
}

fn recorder() -> (Arc<Mutex<Vec<Vec<String>>>>, impl Fn(&[&str]) -> (i32, String)) {
    let calls: Arc<Mutex<Vec<Vec<String>>>> = Arc::default();
    let c = calls.clone();
    (calls, move |a: &[&str]| {
        c.lock().unwrap().push(a.iter().map(|s| s.to_string()).collect());
        (0, String::new())
    })
}

fn v(a: &[&str]) -> Vec<String> {
    a.iter().map(|s| s.to_string()).collect()
}

#[test]
fn parse_leaves_out_own_players_and_events() {
    let ss = parsed();
    assert_eq!(ss.iter().map(|s| s.index).collect::<Vec<_>>(), [10, 11, 12]);
    assert_eq!(ss[0].key, "firefox");
    assert_eq!(ss[0].volume, 40);
    assert_eq!(ss[2].key, "com.spotify.client"); // the app id first
    assert!(ss[2].muted);
    assert_eq!(parse("not json"), vec![]);
    assert_eq!(parse("{}"), vec![]);
}

#[test]
fn group_one_row_per_app() {
    assert_eq!(group(&parsed()).iter().map(|s| s.key.as_str()).collect::<Vec<_>>(), ["firefox", "com.spotify.client"]);
}

#[test]
fn new_stream_line() {
    assert_eq!(new_stream_index("Event 'new' on sink-input #42"), Some(42));
    assert_eq!(new_stream_index("Event 'change' on sink-input #42"), None);
    assert_eq!(new_stream_index("Event 'new' on sink #1"), None);
}

#[test]
fn set_level_saves_and_sets_every_stream() {
    let _l = crate::test_lock();
    let _ = sonata_config::save(NAME, &Map::new());
    let (calls, run) = recorder();
    assert!(set_level_with(&run, "firefox", Some(25), None, Some(&parsed())));
    assert_eq!(*calls.lock().unwrap(), [v(&["pactl", "set-sink-input-volume", "10", "25%"]), v(&["pactl", "set-sink-input-volume", "11", "25%"])]);
    assert_eq!(saved().volumes, *json!({"firefox": 25}).as_object().unwrap()); // kept for next time
    set_level_with(&run, "firefox", Some(500), None, Some(&[]));
    assert_eq!(saved().volume("firefox"), Some(MAX_PERCENT));
}

#[test]
fn restore_on_a_new_stream() {
    let _l = crate::test_lock();
    let _ = sonata_config::save(NAME, &Map::new());
    remember("firefox", Some(25), None);
    remember("com.spotify.client", None, Some(false));
    let ss = parsed();
    let (calls, run) = recorder();
    assert!(restore_with(&run, &ss[0], None));
    assert!(restore_with(&run, &ss[2], None)); // unmuted, as saved
    remember("firefox", Some(40), None);
    assert!(!restore_with(&run, &ss[0], None)); // already right: nothing to do
    assert_eq!(*calls.lock().unwrap(), [v(&["pactl", "set-sink-input-volume", "10", "25%"]), v(&["pactl", "set-sink-input-mute", "12", "0"])]);
}

#[test]
fn saved_is_a_known_config() {
    let _l = crate::test_lock();
    let _ = sonata_config::save(NAME, &Map::new());
    remember("x", Some(10), Some(true));
    assert_eq!(Value::Object(sonata_config::load(NAME, &defaults())), json!({"volumes": {"x": 10}, "muted": {"x": true}}));
}

#[test]
fn stream_knows_its_output() {
    assert_eq!(parsed().iter().map(|s| s.sink).collect::<Vec<_>>(), [1, 1, 2]);
    let mut one = item(1, "A", "a", "50%", false, None, None, None, 0);
    one["sink"] = Value::Null;
    assert_eq!(parse_with(&json!([one]).to_string(), None, &|_| None)[0].sink, -1);
}

fn sinks() -> String {
    json!([
        {"index": 1, "name": "alsa_output.pci", "description": "Built-in Audio Analog Stereo"},
        {"index": 2, "name": "bluez_output.AA", "description": "AirPods Pro"},
        {"index": 3, "name": "alsa_output.hdmi", "description": "HDMI"},
        {"index": 4, "name": "sonata-eq.alsa_output.pci", "description": "Equalizer"},
        {"index": 5, "name": "alsa_output.usb", "description": "USB Audio"},
    ])
    .to_string()
}

fn devices() -> Vec<AudioDevice> {
    let d = |k: &str, n: &str, def: bool| AudioDevice { key: k.into(), name: n.into(), default: def };
    vec![d("alsa_output.pci|analog-output-speaker", "Speakers", true), d("bluez_output.AA|", "AirPods Pro", false), d("alsa_output.usb|line", "Line Out", false), d("alsa_output.usb|headphones", "Headphones", false)]
}

/// Vini: each app's slider gets a button that sends that app alone to
/// another output.
#[test]
fn outputs_as_in_the_output_list() {
    let outs = outputs_from(&sinks(), &devices());
    let got: Vec<(i64, &str, &str)> = outs.iter().map(|o| (o.index, o.name.as_str(), o.label.as_str())).collect();
    assert_eq!(got, [(1, "alsa_output.pci", "Speakers"), (2, "bluez_output.AA", "AirPods Pro"), (5, "alsa_output.usb", "USB Audio")]);
    assert_eq!(outputs_from("not json", &devices()), vec![]);
}

#[test]
fn current_output_through_the_equalizer_too() {
    let outs = outputs_from(&sinks(), &devices());
    let mut ss = parsed();
    assert_eq!(current_output(&ss[2], &outs, &sinks()).unwrap().label, "AirPods Pro");
    ss[0].sink = 4; // through the equalizer: its device's
    assert_eq!(current_output(&ss[0], &outs, &sinks()).unwrap().name, "alsa_output.pci");
    ss[0].sink = 99;
    assert_eq!(current_output(&ss[0], &outs, &sinks()), None);
}

#[test]
fn move_sends_every_stream_of_the_app() {
    let (calls, run) = recorder();
    assert!(move_with(&run, "firefox", "bluez_output.AA", Some(&parsed())));
    assert_eq!(*calls.lock().unwrap(), [v(&["pactl", "move-sink-input", "10", "bluez_output.AA"]), v(&["pactl", "move-sink-input", "11", "bluez_output.AA"])]);
}

#[test]
fn choices_read_once() {
    let text = sinks();
    let (outs, cur) = choices_with(&|_| (0, text.clone()), &parsed()[2], &devices);
    assert_eq!(outs.len(), 3);
    assert_eq!(cur.unwrap().name, "bluez_output.AA");
    assert_eq!(choices_with(&|_| (1, String::new()), &parsed()[2], &devices), (vec![], None));
}

fn st(index: i64, key: &str, vol: i32, muted: bool) -> Stream {
    Stream { index, key: key.into(), name: key.into(), icon: String::new(), volume: vol, muted, corked: false, pid: 0, sink: -1 }
}

fn data(v: Value) -> Saved {
    Saved { volumes: v.as_object().unwrap().clone(), muted: Map::new() }
}

/// Vini: Spotify's level went back every time -- he set it in Spotify, and
/// Sonata kept putting back the old saved one.
#[test]
fn outside_changes_learned() {
    let d = data(json!({"spotify": 100, "chrome": 51}));
    let ss = [st(1, "spotify", 35, false), st(2, "chrome", 51, false), st(3, "chrome", 51, false), st(4, "discord", 80, true)];
    assert_eq!(learn(&ss, &d, &HashSet::new()), [("spotify".to_string(), (35, false)), ("discord".to_string(), (80, true))]);
}

#[test]
fn fresh_streams_and_disagreeing_apps_left() {
    let d = data(json!({"spotify": 100}));
    assert_eq!(learn(&[st(1, "spotify", 35, false)], &d, &HashSet::from([1])), vec![]); // its start: ours wins
    assert_eq!(learn(&[st(2, "chrome", 20, false), st(3, "chrome", 70, false)], &d, &HashSet::new()), vec![]); // two tabs, two levels
}

/// A playing app's 'change' events with no level change learn nothing
/// (and read no config).
#[test]
fn learn_only_when_a_level_moved() {
    let levels = Mutex::default();
    let reads = AtomicUsize::new(0);
    let d = || {
        reads.fetch_add(1, Ordering::SeqCst);
        data(json!({"spotify": 100}))
    };
    let ss = [st(42, "spotify", 30, false)];
    assert_eq!(learn_step(&ss, &levels, &HashSet::new(), &d), [("spotify".to_string(), (30, false))]);
    assert_eq!(learn_step(&ss, &levels, &HashSet::new(), &d), vec![]);
    assert_eq!(reads.load(Ordering::SeqCst), 1);
}

fn service(read_count: Arc<AtomicUsize>, ss: Vec<Stream>) -> (MixerService, Arc<Mutex<Vec<Vec<String>>>>) {
    let calls: Arc<Mutex<Vec<Vec<String>>>> = Arc::default();
    let c = calls.clone();
    let svc = MixerService::with(
        Arc::new(move || {
            read_count.fetch_add(1, Ordering::SeqCst);
            ss.clone()
        }),
        Arc::new(move |a: &[&str]| {
            c.lock().unwrap().push(a.iter().map(|s| s.to_string()).collect());
            (0, String::new())
        }),
    );
    (svc, calls)
}

/// Performance: stream events with no new stream and no open menu run nothing.
#[test]
fn idle_events_spawn_nothing() {
    crate::on_main(move || {
        let reads = Arc::new(AtomicUsize::new(0));
        let (svc, _calls) = service(reads.clone(), parsed());
        svc.line("Event 'change' on sink-input #10");
        crate::spin(300);
        assert_eq!(reads.load(Ordering::SeqCst), 0);
    });
}

#[test]
fn new_stream_restored_and_menu_told() {
    crate::on_main(move || {
        let _ = sonata_config::save(NAME, &Map::new());
        remember("firefox", Some(25), None);
        let reads = Arc::new(AtomicUsize::new(0));
        let (svc, calls) = service(reads.clone(), parsed());
        let got: Rc<RefCell<Vec<usize>>> = Rc::default();
        let g = got.clone();
        svc.add_listener(move |ss| g.borrow_mut().push(ss.len()));
        svc.line("Event 'new' on sink-input #10");
        svc.line("Event 'new' on sink-input #12");
        crate::spin(100);
        assert_eq!(*got.borrow(), [3]);
        assert_eq!(reads.load(Ordering::SeqCst), 2); // read again after restoring
        assert_eq!(calls.lock().unwrap()[0], v(&["pactl", "set-sink-input-volume", "10", "25%"]));
        drop(svc);
    });
}

fn fake_proc(tree: &[(i64, i64, &str, &[(&str, &str)])]) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    for (pid, ppid, cmd, env) in tree {
        let d = root.path().join(pid.to_string());
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("stat"), format!("{pid} (some name) S {ppid} 0 0\n")).unwrap();
        std::fs::write(d.join("cmdline"), cmd.replace(' ', "\0")).unwrap();
        let e: Vec<String> = env.iter().map(|(k, v)| format!("{k}={v}")).collect();
        std::fs::write(d.join("environ"), e.join("\0")).unwrap();
    }
    root
}

/// Vini: the sound mixer showed no icon for a Steam game.
#[test]
fn whose_steam_game_window_or_unknown() {
    let _l = crate::test_lock();
    forget_owners();
    let proc = fake_proc(&[(500, 400, "x", &[("HOME", "/h")]), (400, 300, "x", &[("SteamAppId", "3219630")]), (300, 1, "x", &[])]);
    let p = proc.path().to_str().unwrap();
    assert_eq!(whose(500, Some(&[]), p), Some(("steam", "3219630".into())));
    let proc = fake_proc(&[(900, 800, "x", &[]), (800, 1, "x", &[])]); // WebKitWebProcess -> the web app
    let views = [json!({"pid": 800, "app-id": "io.github.vinioliveiras.sonata2.webapp.w0123456789"})];
    assert_eq!(whose(900, Some(&views), proc.path().to_str().unwrap()), Some(("app", "io.github.vinioliveiras.sonata2.webapp.w0123456789".into())));
    let proc = fake_proc(&[(70, 1, "x", &[])]);
    assert_eq!(whose(70, Some(&[]), proc.path().to_str().unwrap()), None);
    let s = parse_with(r#"[{"index": 3, "volume": {}, "mute": false, "properties": {"application.name": "WINE", "application.process.id": "4242"}}]"#, None, &|_| None);
    assert_eq!(s[0].pid, 4242);
}

/// Vini: web apps' sound showed as Chrome's.
#[test]
fn webapp_of_its_browser_or_window() {
    let proc = fake_proc(&[
        (10, 1, "/opt/google/chrome/chrome --user-data-dir=/home/v/.local/share/sonata2-data/webapps/wa1/chromium --app=https://web.whatsapp.com/", &[]),
        (11, 10, "/opt/google/chrome/chrome --type=utility --utility-sub-type=audio.mojom.AudioService", &[]),
        (20, 1, "/usr/bin/python3 -m sonata2 webapp yt2", &[]),
        (21, 20, "/usr/lib/webkitgtk-6.0/WebKitWebProcess 7 12", &[]),
        (30, 1, "/opt/google/chrome/chrome", &[]),
        (31, 30, "/opt/google/chrome/chrome --type=utility", &[]),
    ]);
    let p = proc.path().to_str().unwrap();
    assert_eq!(webapp_of(11, p).as_deref(), Some("wa1"));
    assert_eq!(webapp_of(21, p).as_deref(), Some("yt2"));
    assert_eq!(webapp_of(31, p), None); // the real Chrome
}

#[test]
fn stream_is_the_webapps_own() {
    let text = json!([{"index": 5, "volume": {}, "mute": false, "properties": {"application.name": "Google Chrome", "application.process.binary": "chrome", "application.process.id": "11"}}]).to_string();
    let entry = |wid: &str| (wid == "wa1").then(|| json!({"name": "WhatsApp"}).as_object().unwrap().clone());
    let s = &parse_with(&text, Some(&|pid| (pid == 11).then(|| "wa1".to_string())), &entry)[0];
    assert_eq!((s.key.as_str(), s.name.as_str()), ("sonata2-webapp-wa1", "WhatsApp"));
    assert_eq!(parse_with(&text, Some(&|_| None), &entry)[0].key, "chrome");
}

/// Vini: changing the volume flashed a row in the mixer for the feedback
/// sound -- system sounds never show there.
#[test]
fn system_sounds_left_out() {
    let streams = |props: Vec<Value>| -> Vec<String> {
        let items: Vec<Value> = props.into_iter().enumerate().map(|(i, p)| json!({"index": i, "volume": {}, "mute": false, "properties": p})).collect();
        parse_with(&json!(items).to_string(), None, &|_| None).into_iter().map(|s| s.name).collect()
    };
    let got = streams(vec![
        json!({"application.name": "pw-play", "media.name": "audio-volume-change.oga"}),
        json!({"application.name": "Sonata", "media.role": "Event", "application.process.binary": "pw-cat"}),
        json!({"application.name": "Firefox", "media.role": "event"}),
        json!({"application.name": "GNOME Settings", "event.id": "bell-window-system"}),
        json!({"application.name": "Some App", "media.category": "Event"}),
        json!({"application.name": "Spotify", "application.process.binary": "spotify"}),
    ]);
    assert_eq!(got, ["Spotify"]);
    // Review: any name starting with "sonata" was hidden -- a web app the
    // user named "Sonata Radio", or Sonatype
    let mut got = streams(vec![
        json!({"application.name": "Sonata Radio", "application.process.binary": "WebKitWebProcess"}),
        json!({"application.name": "Sonatype", "application.process.binary": "java"}),
        json!({"application.name": "Sonata", "application.process.binary": "paplay"}),
    ]);
    got.sort();
    assert_eq!(got, ["Sonata Radio", "Sonatype"]);
}
