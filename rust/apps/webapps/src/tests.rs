//! tests/test_webapps.py, test_webapp_engine.py, test_webapp_video_images.py
//! (the store, the engines, the memory cap) and test_webapp_menubar_name.py.

use super::*;
use std::cell::RefCell;

thread_local! {
    /// The browsers "installed" for this test thread (None: the real search).
    pub(crate) static FOUND: RefCell<Option<Vec<Browser>>> = const { RefCell::new(None) };
}

/// The tests share one temporary HOME and XDG folders: the ones writing
/// settings take turns, each from an empty webapps.json.
fn lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    for dir in sonata_config::read_dirs() {
        let _ = std::fs::remove_file(dir.join("webapps.json"));
        let _ = std::fs::remove_file(dir.join("icons.json"));
    }
    g
}

fn installed(found: &[Browser]) {
    FOUND.with(|f| *f.borrow_mut() = Some(found.to_vec()));
}

fn b(path: &str, name: &'static str, key: &'static str) -> Browser {
    Browser { path: path.into(), name, key }
}

fn chrome() -> Browser {
    b("/usr/bin/google-chrome-stable", "Google Chrome", "chrome")
}

fn brave() -> Browser {
    b("/usr/bin/brave", "Brave", "brave")
}

fn entry(v: Value) -> Map<String, Value> {
    v.as_object().cloned().unwrap()
}

fn png(side: i32) -> Vec<u8> {
    let pix = gtk::gdk_pixbuf::Pixbuf::new(gtk::gdk_pixbuf::Colorspace::Rgb, true, 8, side, side).unwrap();
    pix.fill(0x25d366ff);
    pix.save_to_bufferv("png", &[]).unwrap()
}

#[test]
fn test_addresses() {
    assert_eq!(normalize_url("web.whatsapp.com").as_deref(), Some("https://web.whatsapp.com/"));
    assert_eq!(normalize_url(" http://example.org/a?b=1 ").as_deref(), Some("http://example.org/a?b=1"));
    for bad in ["", "hello", "two words.com", "ftp://x.org", "javascript:alert(1)"] {
        assert_eq!(normalize_url(bad), None, "{bad}");
    }
    assert_eq!(normalize_url("localhost:3000").as_deref(), Some("https://localhost:3000/"));
    assert_eq!(default_name("https://web.whatsapp.com/"), "Whatsapp");
    assert_eq!(default_name("https://www.notion.so/"), "Notion");
}

#[test]
fn test_urlsplit() {
    let u = urlsplit("HTTPS://user@WWW.Example.com:8080/a/b?q=1#top");
    assert_eq!((u.scheme.as_str(), u.netloc.as_str(), u.path.as_str(), u.query.as_str(), u.fragment.as_str()), ("https", "user@WWW.Example.com:8080", "/a/b", "q=1", "top"));
    assert_eq!(u.hostname().as_deref(), Some("www.example.com"));
    assert_eq!(urlsplit("http://[::1]:80/").hostname().as_deref(), Some("::1"));
    assert_eq!(urlsplit("mailto:a@b.c").scheme, "mailto");
    assert_eq!(urlsplit("").hostname(), None);
}

#[test]
fn test_create_writes_its_own_app() {
    let _l = lock();
    installed(&[]);
    let wid = create("WhatsApp", "web.whatsapp.com", Some("sonata2"), false, false, None, None).unwrap();
    let path = applications_dir().join(format!("{}.desktop", desktop_id(&wid)));
    let t = std::fs::read_to_string(&path).unwrap_or_default();
    let _ = std::fs::remove_file(&path);
    assert!(wid.len() == 11 && is_own_id(&wid), "{wid}");
    assert!(t.contains(&format!("Exec=sonata2 webapp {wid}\n")));
    assert!(t.contains(&format!("StartupWMClass={APP_ID_PREFIX}{wid}\n"))); // the Dock tells them apart
    assert!(t.contains("Name=WhatsApp\n"));
    assert!(t.contains(&format!("Icon={FALLBACK_ICON}\n")));
    assert!(is_webapp(&format!("{}.desktop", desktop_id(&wid))));
    assert_eq!(id_of(&desktop_id(&wid)), wid);
    assert!(desktop_id(&wid).starts_with("sonata2-")); // Sonata's own: no "Move to Trash"
}

#[test]
fn test_remove_takes_its_data() {
    let _l = lock();
    installed(&[]);
    let wid = create("Site", "example.org", Some("sonata2"), false, false, None, None).unwrap();
    std::fs::write(data_dir(&wid).join("cookies.sqlite"), b"").unwrap();
    remove(&wid);
    assert!(get(&wid).is_none());
    assert!(!data_dir(&wid).exists());
}

#[test]
fn test_remove_never_touches_other_paths() {
    let _l = lock();
    remove("../../etc"); // no crash, nothing removed
    assert!(Path::new("/etc").exists());
}

#[test]
fn test_site_icon_biggest_first_then_favicon() {
    let html = r#"<link rel="icon" href="/f16.png" sizes="16x16"><link rel="apple-touch-icon" href="/touch.png"><link rel="icon" href="https://cdn.x.org/i192.png" sizes="192x192">"#;
    assert_eq!(icon_candidates("https://x.org/app", html), ["https://cdn.x.org/i192.png", "https://x.org/touch.png", "https://x.org/f16.png", "https://x.org/favicon.ico"]);
    let html = r#"<LINK REL='Shortcut Icon' HREF='/a.svg?v=1&amp;x=2'><link rel=stylesheet href=/s.css><link rel=icon href=i.png>"#;
    assert_eq!(icon_candidates("https://x.org/app/", html), ["https://x.org/a.svg?v=1&x=2", "https://x.org/app/i.png", "https://x.org/favicon.ico"]);
}

#[test]
fn test_fetch_icon_and_the_entry_follows() {
    let _l = lock();
    installed(&[]);
    let wid = create("Site", "example.org", Some("sonata2"), false, false, None, None).unwrap();
    let big = png(300);
    let files = move |u: &str| match u {
        "https://example.org/" => Some(br#"<link rel="apple-touch-icon" href="/t.png">"#.to_vec()),
        "https://example.org/t.png" => Some(big.clone()),
        _ => None,
    };
    assert!(fetch_icon(&wid, &files));
    let (_, w, h) = gtk::gdk_pixbuf::Pixbuf::file_info(icon_path(&wid)).unwrap();
    assert_eq!((w, h), (256, 256)); // kept small
    assert!(!save_icon(&wid, &png(64))); // never a smaller one
    assert!(desktop_text(&wid, &get(&wid).unwrap(), Some("sonata2")).contains(&format!("Icon={}\n", icon_path(&wid).display())));
    remove(&wid);
}

#[test]
fn test_custom_icon_copied_and_used() {
    let _l = lock();
    installed(&[]);
    let wid = create("Site", "example.org", Some("sonata2"), false, false, None, None).unwrap();
    let d = tempfile::tempdir().unwrap();
    let pic = d.path().join("mine.png");
    std::fs::write(&pic, png(64)).unwrap();
    let dest = set_custom_icon(&wid, &pic).unwrap();
    assert!(dest.starts_with(data_dir(&wid))); // a copy: the original may move
    std::fs::remove_file(&pic).unwrap();
    let icons = sonata_config::load("icons", &json!({"apps": {}}));
    assert_eq!(icons["apps"][desktop_id(&wid)], json!({"source": "file", "path": dest.to_string_lossy()}));
    remove(&wid);
    let icons = sonata_config::load("icons", &json!({"apps": {}}));
    assert!(icons["apps"].get(desktop_id(&wid)).is_none()); // forgotten with it
}

/// Vini: the icon pack's icon first, when it has one with the web app's name.
#[test]
fn test_names_tried() {
    assert_eq!(icon_names("WhatsApp", "https://web.whatsapp.com/")[..1], ["whatsapp"]);
    let n = icon_names("Google Calendar", "https://calendar.google.com/");
    assert!(n.contains(&"google-calendar".to_string()));
    assert!(n.contains(&"googlecalendar".to_string()));
    assert!(n.contains(&"google_calendar".to_string()));
}

#[test]
fn test_pack_icon_wins_over_the_sites() {
    let _l = lock();
    installed(&[]);
    let mut e = entry(json!({"name": "WhatsApp", "url": "https://web.whatsapp.com/", "theme_icon": "whatsapp"}));
    let _ = std::fs::create_dir_all(data_dir("w00000000ee"));
    std::fs::write(icon_path("w00000000ee"), png(64)).unwrap(); // the site's icon too
    assert!(desktop_text("w00000000ee", &e, Some("sonata2")).contains("Icon=whatsapp\n"));
    e.remove("theme_icon");
    assert!(desktop_text("w00000000ee", &e, Some("sonata2")).contains(&format!("Icon={}\n", icon_path("w00000000ee").display())));
    let _ = std::fs::remove_dir_all(data_dir("w00000000ee"));
    assert!(desktop_text("w00000000ee", &e, Some("sonata2")).contains(&format!("Icon={FALLBACK_ICON}\n")));
}

#[test]
fn desktop_values_cant_add_keys() {
    let e = entry(json!({"name": "A\nExec=evil\\s", "url": "https://x.org/"}));
    let t = desktop_text("wabc", &e, Some("sonata2"));
    assert!(t.contains("Name=A Exec=evil\\\\s\n"));
    assert!(!t.contains("\nExec=evil"));
}

/// Vini's dev install links ~/.local/share/sonata2 to the git clone: a web
/// app's login landed in the repository.
#[test]
fn test_never_in_sonatas_own_folder() {
    assert!(data_dir("w00000000aa").starts_with(glib::user_data_dir().join("sonata2-data")));
}

#[test]
fn test_old_place_moved_and_the_icon_follows() {
    let _l = lock();
    let new = glib::user_data_dir().join("sonata2-data").join("webapps");
    let _ = std::fs::remove_dir_all(&new);
    let old = glib::user_data_dir().join("sonata2").join("webapps").join("w00000000bb");
    std::fs::create_dir_all(&old).unwrap();
    std::fs::write(old.join("custom-icon.png"), png(64)).unwrap();
    set_app_pref(&desktop_id("w00000000bb"), &[("source", Some(json!("file"))), ("path", Some(json!(old.join("custom-icon.png").to_string_lossy())))]);
    save(&entry(json!({"apps": {"w00000000bb": {"name": "Old", "url": "https://old.example/"}}})));
    write_all(Some("sonata2"));
    let moved = new.join("w00000000bb").join("custom-icon.png");
    assert!(moved.exists());
    let icons = sonata_config::load("icons", &json!({"apps": {}}));
    assert_eq!(icons["apps"][desktop_id("w00000000bb")]["path"], json!(moved.to_string_lossy()));
}

/// Vini: a web app made can be edited (Launchpad's and the Dock's menus).
#[test]
fn test_update_keeps_its_login() {
    let _l = lock();
    installed(&[]);
    let wid = create("Zap", "web.whatsapp.com", Some("sonata2"), false, false, None, None).unwrap();
    std::fs::write(data_dir(&wid).join("cookies.sqlite"), b"").unwrap();
    let c = Changes { name: Some("WhatsApp".into()), url: Some("https://web.whatsapp.com/?x=1".into()), background: Some(true), ..Default::default() };
    assert!(update(&wid, &c, Some("sonata2")));
    let e = get(&wid).unwrap();
    assert_eq!((e["name"].clone(), e["url"].clone(), e["background"].clone()), (json!("WhatsApp"), json!("https://web.whatsapp.com/?x=1"), json!(true)));
    assert!(data_dir(&wid).join("cookies.sqlite").exists()); // same data
    update(&wid, &Changes { background: Some(false), ..Default::default() }, None);
    assert!(get(&wid).unwrap().get("background").is_none());
    assert!(!update("w0000000000", &Changes { name: Some("x".into()), ..Default::default() }, None));
    remove(&wid);
}

// -- engines (test_webapp_engine.py) -----------------------------------------------------------

#[test]
fn test_browser_found_in_order() {
    let which = |c: &str| (c == "chromium").then(|| "/usr/bin/chromium".to_string());
    let found = browsers_with(&which, &|_| false);
    assert_eq!(found, [b("/usr/bin/chromium", "Chromium", "chromium")]);
    installed(&found);
    assert_eq!(default_engine(), "chromium");
    installed(&[]);
    assert_eq!(chromium_browser(None), None);
    assert_eq!(default_engine(), "webkit");
}

#[test]
fn test_engine_of_an_app() {
    installed(&[b("/b", "Chromium", "chromium")]);
    assert_eq!(engine(Some(&entry(json!({"engine": "chromium"})))), "chromium");
    assert_eq!(engine(Some(&entry(json!({})))), "webkit"); // made before the choice: its login
    installed(&[]);
    assert_eq!(engine(Some(&entry(json!({"engine": "chromium"})))), "webkit"); // browser gone
}

#[test]
fn test_command() {
    installed(&[chrome()]);
    let cmd = chromium_command("wabc", &entry(json!({"url": "https://web.whatsapp.com/"})), "/usr/bin/google-chrome-stable");
    assert_eq!(cmd[0], "/usr/bin/google-chrome-stable");
    assert!(cmd.contains(&"--app=https://web.whatsapp.com/".to_string()));
    assert!(cmd.iter().any(|a| a.starts_with("--user-data-dir=") && a.ends_with("wabc/chromium")));
    assert!(cmd.contains(&"--test-type".to_string()));
}

/// Chrome names an --app window itself (seen: chrome-example.com__-Default; --class ignored).
#[test]
fn test_window_class() {
    assert_eq!(chromium_app_id("https://example.com", "chrome"), "chrome-example.com__-Default");
    assert_eq!(chromium_app_id("https://web.whatsapp.com/", "chrome"), "chrome-web.whatsapp.com__-Default");
    assert_eq!(chromium_app_id("https://x.com/a/b?q=1", "chrome"), "chrome-x.com__a_b-Default");
    installed(&[b("/b", "B", "chrome")]);
    let mut e = entry(json!({"name": "Zap", "url": "https://web.whatsapp.com/", "engine": "chromium"}));
    assert!(desktop_text("wabc", &e, Some("s")).contains("StartupWMClass=chrome-web.whatsapp.com__-Default\n"));
    e.insert("engine".into(), json!("webkit"));
    assert!(desktop_text("wabc", &e, Some("s")).contains(&format!("StartupWMClass={}\n", app_id("wabc"))));
}

/// Vini: web apps couldn't be made with Brave when Chrome was installed
/// too -- only the first Chromium-family browser found was offered.
#[test]
fn test_only_chrome_offered_when_brave_installed_too() {
    let which = |c: &str| match c {
        "google-chrome-stable" => Some("/usr/bin/google-chrome-stable".to_string()),
        "brave" => Some("/usr/bin/brave".to_string()),
        _ => None,
    };
    let found = browsers_with(&which, &|_| false);
    assert_eq!(found.iter().map(|b| (b.name, b.key)).collect::<Vec<_>>(), [("Google Chrome", "chrome"), ("Brave", "brave")]);
    installed(&found);
    assert_eq!(chromium_browser(None), Some(chrome()));
    assert_eq!(chromium_browser(Some(&entry(json!({"browser": "brave"})))), Some(brave()));
}

#[test]
fn test_brave_app_runs_in_brave_with_its_own_profile_and_app_id() {
    installed(&[chrome(), brave()]);
    let mut e = entry(json!({"url": "https://web.whatsapp.com/", "engine": "chromium", "browser": "brave"}));
    assert_eq!(window_class("wabc", &e), "brave-web.whatsapp.com__-Default");
    assert!(profile_dir("wabc", &e).ends_with("wabc/brave"));
    e.insert("browser".into(), json!("chrome"));
    assert!(profile_dir("wabc", &e).ends_with("wabc/chromium"));
}

/// Vini's Brave is a Flatpak (com.brave.Browser): it wasn't offered at all
/// -- only browsers on PATH were. A Flatpak one is, run with `flatpak run`,
/// its profile in its own folder (it can't reach home).
#[test]
fn test_brave_flatpak_not_found() {
    let which = |c: &str| (c == "flatpak").then(|| "/usr/bin/flatpak".to_string());
    let found = browsers_with(&which, &|p: &Path| p.ends_with("com.brave.Browser"));
    assert_eq!(found, [b("flatpak:com.brave.Browser", "Brave", "brave")]);
    installed(&found);
    let e = entry(json!({"url": "https://web.whatsapp.com/", "engine": "chromium", "browser": "brave"}));
    let cmd = chromium_command("wabc", &e, "flatpak:com.brave.Browser");
    assert_eq!(cmd[..3], ["flatpak", "run", "com.brave.Browser"]);
    assert!(cmd.contains(&"--app=https://web.whatsapp.com/".to_string()));
    let prof = cmd.iter().find(|a| a.starts_with("--user-data-dir=")).unwrap();
    assert!(prof.ends_with("/.var/app/com.brave.Browser/data/sonata2-webapps/wabc"), "{prof}");
    assert_eq!(window_class("wabc", &e), "brave-web.whatsapp.com__-Default");
}

#[test]
fn test_brave_gone_falls_back() {
    installed(&[b("/usr/bin/chromium", "Chromium", "chromium")]);
    assert_eq!(chromium_browser(Some(&entry(json!({"browser": "brave"})))), Some(b("/usr/bin/chromium", "Chromium", "chromium")));
}

#[test]
fn test_saved() {
    let _l = lock();
    installed(&[chrome(), brave()]);
    let app = create("Zap", "web.whatsapp.com", None, false, false, Some("webkit"), None).unwrap();
    assert_eq!(get(&app).unwrap()["engine"], "webkit");
    update(&app, &Changes { engine: Some("chromium".into()), ..Default::default() }, None);
    assert_eq!(get(&app).unwrap()["engine"], "chromium");
    update(&app, &Changes { engine: Some("nonsense".into()), browser: Some("brave".into()), ..Default::default() }, None);
    assert_eq!(get(&app).unwrap()["engine"], "chromium");
    assert_eq!(get(&app).unwrap()["browser"], "brave");
    update(&app, &Changes { browser: Some("nonsense".into()), ..Default::default() }, None);
    assert_eq!(get(&app).unwrap()["browser"], "brave");
}

/// Vini: the WhatsApp web app was there twice -- both get one app id in the
/// browser, so the Dock mixed up their windows and opening one opened
/// Chrome. The same site again is the web app already made.
#[test]
fn test_whatsapp_made_twice() {
    let _l = lock();
    installed(&[chrome()]);
    let first = create("WhatsApp", "https://web.whatsapp.com/", None, false, false, None, None).unwrap();
    let again = create("WhatsApp", "web.whatsapp.com", None, false, false, None, None).unwrap();
    let other = create("Instagram", "https://www.instagram.com/", None, false, false, None, None).unwrap();
    assert_eq!(again, first);
    assert_ne!(other, first);
    let mut ids: Vec<String> = apps().keys().cloned().collect();
    ids.sort();
    let mut want = vec![first, other];
    want.sort();
    assert_eq!(ids, want);
}

/// Only Chromium web apps of one browser share an app id per site: a WebKit
/// one (its own app id) for a site already made -- another account, say --
/// is made, with the name and engine asked for.
#[test]
fn test_second_webkit_app_for_a_site_was_refused() {
    let _l = lock();
    installed(&[chrome()]);
    let first = create("WhatsApp", "https://web.whatsapp.com/", None, false, false, Some("chromium"), None).unwrap();
    let work = create("WhatsApp Work", "https://web.whatsapp.com/", None, false, false, Some("webkit"), None).unwrap();
    assert_ne!(work, first);
    assert_eq!(get(&work).unwrap()["name"], "WhatsApp Work");
    assert_eq!(create("Zap", "web.whatsapp.com", None, false, false, Some("chromium"), None).unwrap(), first);
}

/// Vini: the menu bar named the WhatsApp web app (on Chrome) "Sonata2
/// Webapp W6bf1bb01cf": a Chromium web app's window is found by Sonata's
/// own list of web apps.
#[test]
fn test_chromium_web_app_found_by_its_window_class() {
    let _l = lock();
    installed(&[chrome()]);
    save(&entry(json!({"apps": {"w6bf1bb01cf": {"name": "WhatsApp", "url": "https://web.whatsapp.com/", "engine": "chromium"}}})));
    assert_eq!(desktop_for_window_class("chrome-web.whatsapp.com__-Default").as_deref(), Some("sonata2-webapp-w6bf1bb01cf"));
    assert_eq!(desktop_for_window_class(&app_id("wabc")).as_deref(), Some("sonata2-webapp-wabc"));
    assert_eq!(desktop_for_window_class("org.gnome.Nautilus"), None);
}

// -- size (Vini: the web app in Chrome didn't keep the size he gave its window) --------------

#[test]
fn test_size_of_an_app() {
    let _l = lock();
    installed(&[]);
    assert_eq!(window_size("a"), DEFAULT_SIZE);
    sonata_ui::window::save_size(&size_key("a"), 900, 700, false);
    assert_eq!(window_size("a"), (900, 700));
    assert!(chromium_command("a", &entry(json!({"url": "https://x/"})), "/b").contains(&"--window-size=900,700".to_string()));
}

#[test]
fn test_view_size() {
    let mut views = json!([{"app-id": "other", "geometry": {"width": 5, "height": 5}}, {"app-id": "chrome-x__-Default", "geometry": {"width": 1000, "height": 640}}]);
    assert_eq!(view_size(&views, "chrome-x__-Default"), Some((1000, 640)));
    views[1]["tiled-edges"] = json!(15); // maximized: not its size
    assert_eq!(view_size(&views, "chrome-x__-Default"), None);
    assert_eq!(view_size(&json!([]), "chrome-x__-Default"), None);
}

// -- video and memory (test_webapp_video_images.py) --------------------------------------------

/// Vini: WhatsApp said the mp4 was not supported -- WebKit's video through
/// the GPU decoders failed ("Media failed to decode"); software works.
#[test]
fn test_ranks() {
    let r = gst_ranks("");
    for d in ["nvh264dec:0", "vah264dec:0", "vulkanh264dec:0"] {
        assert!(r.split(',').any(|p| p == d));
    }
    let kept = gst_ranks("nvh264dec:300,foo:1");
    assert!(kept.starts_with("nvh264dec:300,foo:1,")); // what was set stays
    assert_eq!(kept.split(',').filter(|p| *p == "nvh264dec:0").count(), 0);
}

#[test]
fn test_cap_follows_free_memory() {
    assert_eq!(memory_cap(2000, 20000), 2000 + 20000 - RESERVE_MB); // room: big cap
    assert_eq!(memory_cap(6000, 500), FLOOR_MB.max(6500 - RESERVE_MB));
    assert_eq!(memory_cap(100, 100), FLOOR_MB);
}

#[test]
fn test_guard_writes_the_cap() {
    let d = tempfile::Builder::new().suffix(".scope").tempdir().unwrap();
    std::fs::write(d.path().join("memory.current"), (3000i64 * 1024 * 1024).to_string()).unwrap();
    std::fs::write(d.path().join("memory.max"), "max").unwrap();
    let mut g = MemoryGuard::new(Some(d.path().to_path_buf()));
    assert!(g.ok());
    assert!(g.tick_with(Some(10000)));
    let max: i64 = std::fs::read_to_string(d.path().join("memory.max")).unwrap().parse().unwrap();
    assert_eq!(max, memory_cap(3000, 10000) * 1024 * 1024);
    let first = g.last;
    g.tick_with(Some(10050));
    assert_eq!(g.last, first); // small move: not written
    g.tick_with(Some(2000));
    assert_eq!(g.last, Some(memory_cap(3000, 2000))); // less free: lower cap
    assert!(!MemoryGuard::new(Some(PathBuf::from("/sys/fs/cgroup/user.slice"))).ok()); // not a scope
}

#[test]
fn test_scoped_command() {
    let cmd = scoped_command(&["waee32c088c".to_string()], "/usr/bin/sonata2-rs");
    assert_eq!(cmd[..3], ["systemd-run", "--user", "--scope"]);
    assert!(cmd.contains(&"MemorySwapMax=0".to_string()));
    assert_eq!(cmd[cmd.len() - 3..], ["/usr/bin/sonata2-rs", "webapp", "waee32c088c"]);
}

#[test]
fn test_main_reexecs_once() {
    let a = |s: &str| vec![s.to_string()];
    assert!(needs_scope(&a("abc"), false, true));
    assert!(!needs_scope(&a("abc"), true, true)); // already in it
    assert!(!needs_scope(&a("abc"), false, false)); // no user systemd: run as is
    assert!(!needs_scope(&a("new"), false, true)); // the forms aren't scoped
    assert!(!needs_scope(&a("edit"), false, true));
    assert!(!needs_scope(&[], false, true));
}

#[test]
fn missing_webkit_names_the_package() {
    let (heading, body) = missing_webkit();
    assert!(heading.contains("WebKitGTK 6"));
    assert!(body.contains("webkitgtk-6.0"));
}
