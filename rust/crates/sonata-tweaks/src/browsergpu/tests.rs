//! tests/test_browsergpu.py (Vini: a toggle for each app instead of one
//! switch for every browser; before: "Unable to find a GPU" in a WebGPU
//! game, Chrome's Vulkan left a transparent window on Wayland, WhatsApp
//! showed "unsupported command-line flag").

use super::*;
use std::fs;

fn v(x: &[&str]) -> Vec<String> {
    x.iter().map(|s| s.to_string()).collect()
}

fn all() -> Vec<String> {
    v(&FLAGS)
}

#[test]
fn on_off_roundtrip() {
    let mine = v(&["--gtk-version=3"]);
    let (on, app) = edit(&mine, &all(), false);
    assert_eq!(on, v(&["--gtk-version=3", MARK, "--enable-features=Vulkan", "--ozone-platform=x11", "--enable-unsafe-webgpu"]));
    assert!(!app);
    assert_eq!(edit(&on, &all(), false), (on.clone(), false)); // twice: once
    assert_eq!(edit(&on, &[], false), (mine, false));
}

/// Chrome keeps only the last --enable-features: Vulkan joins theirs.
#[test]
fn users_own_features_line() {
    let mine = v(&["--enable-features=TouchpadOverscrollHistoryNavigation"]);
    let (on, app) = edit(&mine, &all(), false);
    assert!(app);
    assert_eq!(on[0], "--enable-features=TouchpadOverscrollHistoryNavigation,Vulkan");
    assert_eq!(on.iter().filter(|l| l.starts_with("--enable-features")).count(), 1);
    assert_eq!(edit(&on, &[], app), (mine, false));
    // the user's own Vulkan is kept
    let mine = v(&["--enable-features=Vulkan"]);
    let (on, app) = edit(&mine, &all(), false);
    assert!(!app);
    assert_eq!(edit(&on, &[], app).0, mine);
    // the launchers pass every word of a line to the browser: no comment on a flag line
    let (on, _) = edit(&v(&["--enable-features=A"]), &all(), false);
    assert!(!on.iter().any(|l| l.starts_with("--") && l.contains('#')));
}

/// Files written by the old global switch keep working.
#[test]
fn old_global_block_read_and_replaced() {
    let old = v(&["--gtk-version=3", MARKS[1], OWN, X11, WEBGPU]);
    assert_eq!(block(&old), v(&[X11, WEBGPU]));
    assert_eq!(edit(&old, &[], false).0, v(&["--gtk-version=3"]));
}

/// Vini: the switch for Firefox too (dom.webgpu.enabled).
#[test]
fn firefox_user_js() {
    let d = tempfile::tempdir().unwrap();
    let prof = d.path().to_path_buf();
    fs::write(prof.join("prefs.js"), "user_pref(\"a\", 1);\n").unwrap();
    fs::write(prof.join("user.js"), "user_pref(\"mine\", 2);\n").unwrap();
    let ps = vec![prof.clone()];
    apply_mozilla(true, &ps);
    assert_eq!(fs::read_to_string(prof.join("user.js")).unwrap(), format!("user_pref(\"mine\", 2);\n{MOZ_LINE}\n"));
    apply_mozilla(true, &ps); // once
    assert_eq!(fs::read_to_string(prof.join("user.js")).unwrap().matches("Sonata WebGPU").count(), 1);
    let mut p = fs::read_to_string(prof.join("prefs.js")).unwrap();
    p += "user_pref(\"dom.webgpu.enabled\", true);\n"; // Firefox copied it
    fs::write(prof.join("prefs.js"), p).unwrap();
    apply_mozilla(false, &ps);
    assert_eq!(fs::read_to_string(prof.join("user.js")).unwrap(), "user_pref(\"mine\", 2);\n");
    assert_eq!(fs::read_to_string(prof.join("prefs.js")).unwrap(), "user_pref(\"a\", 1);\n");
}

#[test]
fn profiles_found_by_prefs_js() {
    let d = tempfile::tempdir().unwrap();
    fs::create_dir_all(d.path().join("abc.default")).unwrap();
    fs::write(d.path().join("abc.default/prefs.js"), "").unwrap();
    fs::create_dir_all(d.path().join("Crash Reports")).unwrap();
    let base = d.path().display().to_string();
    assert_eq!(mozilla_profiles(&[&base]), [d.path().join("abc.default")]);
}

fn fake_electron(n: &str, _f: bool) -> bool {
    n == "discord" || n == "com.discordapp.Discord"
}

#[test]
fn kinds() {
    for (did, line, want) in [
        ("google-chrome", "/usr/bin/google-chrome-stable %U", "chromium"),
        ("com.google.Chrome", "/usr/bin/flatpak run --branch=stable com.google.Chrome @@u %U @@", "chromium"),
        ("sonata2-webapp-wa", "sonata2 webapp wa", "webapp"),
        ("firefox", "/usr/lib/firefox/firefox %u", "mozilla"),
        ("steam", "/usr/bin/steam %U", "wine"),
        ("discord", "env FOO=1 discord", "electron"),
        ("com.discordapp.Discord", "flatpak run com.discordapp.Discord", "electron"),
        ("org.gnome.Nautilus", "nautilus --new-window", "other"),
    ] {
        assert_eq!(kind_with(&App::new(did, line), &fake_electron), want, "{did}");
    }
    let k = |a: &App| kind_with(a, &fake_electron);
    let ctx = Ctx { kind: &k, ..Ctx::default() };
    assert_eq!(ctx.supports(&App::new("firefox", "firefox")), (false, true));
    assert_eq!(ctx.supports(&App::new("gedit", "gedit")), (true, false));
    assert_eq!(ctx.supports(&App::new("google-chrome", "google-chrome-stable")), (true, true));
}

#[test]
fn electron_by_asar_or_script() {
    let d = tempfile::tempdir().unwrap();
    fs::create_dir_all(d.path().join("app/resources")).unwrap();
    fs::write(d.path().join("app/resources/app.asar"), "").unwrap();
    let exe = d.path().join("app/code");
    fs::write(&exe, "").unwrap();
    assert!(electron(&exe.display().to_string(), false));
    let script = d.path().join("obsidian");
    fs::write(&script, "#!/bin/sh\nexec electron37 /usr/lib/obsidian/app.asar \"$@\"\n").unwrap();
    assert!(electron(&script.display().to_string(), false));
    let plain = d.path().join("plain");
    fs::write(&plain, "#!/bin/sh\nexec true\n").unwrap();
    assert!(!electron(&plain.display().to_string(), false));
}

/// A Chromium browser's choices live in its flag file (any way it opens).
#[test]
fn toggles_write_the_file() {
    let _l = crate::util::test_lock();
    let d = tempfile::tempdir().unwrap();
    let ctx = Ctx { cfg_dir: Some(d.path()), ..Ctx::default() };
    let file = d.path().join("chrome-flags.conf");
    let chrome = App::new("google-chrome", "/usr/bin/google-chrome-stable %U");
    // off: no file made
    ctx.set_vulkan(&chrome, false);
    assert!(!file.exists());
    fs::write(&file, "--enable-features=X\n").unwrap();
    assert_eq!((ctx.vulkan(&chrome), ctx.webgpu(&chrome)), (false, false));
    ctx.set_vulkan(&chrome, true);
    assert_eq!(crate::util::read_lines(&file), v(&["--enable-features=X,Vulkan", MARK, X11]));
    assert!(ctx.vulkan(&chrome));
    ctx.set_webgpu(&chrome, true);
    assert_eq!(crate::util::read_lines(&file)[1..], v(&[MARK, X11, WEBGPU]));
    assert!(ctx.vulkan_forced(&chrome));
    ctx.set_webgpu(&chrome, false);
    ctx.set_vulkan(&chrome, false);
    assert_eq!(crate::util::read_lines(&file), v(&["--enable-features=X"]));
}

#[test]
fn firefox_webgpu_its_own_profiles() {
    let base = tempfile::tempdir().unwrap();
    fs::create_dir_all(base.path().join("p")).unwrap();
    fs::write(base.path().join("p/prefs.js"), "").unwrap();
    let b = base.path().display().to_string();
    let moz = move |_: &str| vec![b.clone()];
    let ctx = Ctx { mozilla: &moz, ..Ctx::default() };
    let ff = App::new("firefox", "firefox %u");
    assert!(!ctx.webgpu(&ff));
    ctx.set_webgpu(&ff, true);
    assert!(ctx.webgpu(&ff));
    assert!(fs::read_to_string(base.path().join("p/user.js")).unwrap().contains(MOZ_LINE));
    ctx.set_webgpu(&ff, false);
    assert!(!ctx.webgpu(&ff));
}

#[test]
fn launch_flags_and_env() {
    let _l = crate::util::test_lock();
    let k = |a: &App| kind_with(a, &fake_electron);
    let ctx = Ctx { kind: &k, ..Ctx::default() };
    // other apps: Vulkan's environment
    let app = App::new("org.gnome.Nautilus-rs-test", "nautilus");
    assert!(ctx.launch_env(&app).is_empty());
    ctx.set_vulkan(&app, true);
    assert_eq!(ctx.launch_env(&app), pairs(&VULKAN_ENV));
    assert!(ctx.launch_args(&app).is_empty());
    // Wine: Vulkan by default, WineD3D when off
    let steam = App::new("steam", "/usr/bin/steam %U");
    assert!(ctx.vulkan(&steam));
    assert!(ctx.launch_env(&steam).is_empty());
    ctx.set_vulkan(&steam, false);
    assert!(ctx.launch_env(&steam).contains(&("PROTON_USE_WINED3D".into(), "1".into())));
    ctx.set_vulkan(&steam, true);
    // Electron: the flags at launch
    let app = App::new("discord", "discord");
    assert!(ctx.launch_args(&app).is_empty());
    ctx.set_webgpu(&app, true);
    assert_eq!(ctx.launch_args(&app), v(&[X11, WEBGPU, OWN]));
    assert!(ctx.vulkan_forced(&app));
    ctx.set_webgpu(&app, false);
}

fn launcher(d: &Path) -> (String, String) {
    use std::os::unix::fs::PermissionsExt;
    let real = d.join("opt-chrome");
    fs::write(&real, "#!/bin/sh\n").unwrap();
    fs::set_permissions(&real, fs::Permissions::from_mode(0o755)).unwrap();
    let l = d.join("google-chrome-stable");
    fs::write(&l, format!("#!/bin/bash\nXDG_CONFIG_HOME=${{XDG_CONFIG_HOME:-~/.config}}\nif [[ -f $XDG_CONFIG_HOME/chrome-flags.conf ]]; then\n  CHROME_USER_FLAGS=\"$(grep -v '^#' $XDG_CONFIG_HOME/chrome-flags.conf)\"\nfi\nexec {} $CHROME_USER_FLAGS \"$@\"\n", real.display())).unwrap();
    (real.display().to_string(), l.display().to_string())
}

/// Vini: WhatsApp showed "unsupported command-line flag".
#[test]
fn past_the_launcher_when_chrome_has_our_block() {
    let _l = crate::util::test_lock();
    let (d, cfg) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
    let ctx = Ctx { cfg_dir: Some(cfg.path()), ..Ctx::default() };
    let (real, l) = launcher(d.path());
    assert_eq!(ctx.web_app_command("wa", &l), [l.clone()]); // nothing to skip
    let chrome = App::new("google-chrome", &l);
    fs::write(cfg.path().join("chrome-flags.conf"), "--gtk-version=3\n--enable-features=Foo\n").unwrap();
    ctx.set_webgpu(&chrome, true);
    assert_eq!(ctx.web_app_command("wa", &l), [real, "--gtk-version=3".into(), "--enable-features=Foo".into()]);
    // a web app's own toggle
    let game = App::new("sonata2-webapp-game", "");
    ctx.set_webgpu(&game, true);
    let cfg2 = tempfile::tempdir().unwrap();
    let ctx2 = Ctx { cfg_dir: Some(cfg2.path()), ..Ctx::default() };
    assert_eq!(ctx2.web_app_command("game", &l), [l.clone(), X11.into(), WEBGPU.into(), "--enable-features=Vulkan".into()]);
    ctx.set_webgpu(&game, false);
}

#[test]
fn unknown_launcher_with_block_stays_on_wayland() {
    let _l = crate::util::test_lock();
    let cfg = tempfile::tempdir().unwrap();
    write_flag_file("chrome-flags.conf", &v(&[X11]), Some(cfg.path()));
    let ctx = Ctx { cfg_dir: Some(cfg.path()), ..Ctx::default() };
    assert_eq!(ctx.web_app_command("wa", "/x/google-chrome-stable"), v(&["/x/google-chrome-stable", "--ozone-platform=wayland", "--disable-features=Vulkan"]));
}

#[test]
fn programs_of_exec_lines() {
    assert_eq!(program("env FOO=1 discord --x"), ("discord".into(), false));
    assert_eq!(program("/usr/bin/flatpak run --branch=stable com.google.Chrome @@u %U @@"), ("com.google.Chrome".into(), true));
    assert_eq!(program("'unbalanced"), (String::new(), false));
    assert_eq!(features(&v(&["a", "--enable-features=X"]), "Vulkan"), v(&["a", "--enable-features=X,Vulkan"]));
}
