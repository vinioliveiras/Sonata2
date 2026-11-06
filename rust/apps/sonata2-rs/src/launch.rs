//! Sonata's launch path (apps.py's _gpu_aware wrapper), handed to
//! sonata-ui once at start (`sonata_ui::apps::set_launcher`): every app
//! Sonata opens -- Dock, Launchpad, Spotlight, Files, notifications, login
//! items -- goes through it. sonata-ui can't do it itself: the app lock,
//! the permissions review, the GPU and the scopes live in crates that
//! depend on it.

use gtk::gio;
use gtk::glib;
use gtk::prelude::*;
use sonata_desktop::{applock, appscope, sandbox};
use sonata_tweaks::{appperms, gpu};

/// Sonata's own copy of an app (Steam + its arguments) is marked: already
/// set up, it must not be judged again -- it has no desktop id, looked like
/// an everyday app and Steam (and its games) went to the integrated GPU.
const ENV_SET: &str = "sonata-env-set";

fn env_set(info: &gio::AppInfo) -> bool {
    // SAFETY: the mark is only ever set and read as a bool, here
    unsafe { info.data::<bool>(ENV_SET) }.is_some()
}

/// The command line Sonata runs instead of the entry's own, when the app
/// needs arguments of its own here (Steam on NVIDIA, Electron under X11,
/// flatpak run options); None: the entry as it is.
pub fn command_line(line: &str, args: &[String], run_args: &[String]) -> Option<String> {
    if args.is_empty() && run_args.is_empty() {
        return None;
    }
    Some(gpu::with_launcher_args(&gpu::with_args(line, args), run_args))
}

/// Open `info` as Sonata does: the password of a locked app, a new
/// Flatpak's review, the limits set in Settings > Apps, then the GPU's
/// environment, its own scope, and never the shell's GTK_THEME.
pub fn launch(info: &gio::AppInfo, files: &[gio::File], ctx: Option<&gio::AppLaunchContext>) -> Result<(), glib::Error> {
    if env_set(info) {
        return info.launch(files, ctx);
    }
    let again = {
        let (i, f, c) = (info.clone(), files.to_vec(), ctx.cloned());
        move || {
            if let Err(e) = launch(&i, &f, c.as_ref()) {
                eprintln!("sonata2: {}: {e}", i.id().unwrap_or_default());
            }
        }
    };
    // a locked app: the password first (applock.py)
    if applock::gate(info, again.clone()) {
        return Ok(());
    }
    // a new Flatpak app: what it may use, at its first open
    let permissions = |fid: &str| {
        let owner = sonata_system::uninstall::Owner::new("flatpak", fid, "");
        sonata_system::appmanage::permissions(Some(&owner), None).into_iter().map(|(k, t, on)| (k.to_string(), t, on)).collect()
    };
    let refuse = |fid: &str, key: &str| {
        let owner = sonata_system::uninstall::Owner::new("flatpak", fid, "");
        sonata_system::appmanage::set_permission(Some(&owner), key, false, None);
    };
    // (asked only where GTK runs: the login items' launcher has no display
    // to ask on -- autostart.py's had none either, and opened the app)
    if gtk::is_initialized_main_thread() && appperms::review_gate(info, permissions, refuse, again) {
        return Ok(());
    }
    // (a login item's own file name when it has no desktop id)
    let id = sonata_ui::apps::desktop_id(info);
    // a packaged app kept from the camera, network... (Settings > Apps)
    let refused = appperms::limits(&id);
    if !refused.is_empty() && appperms::flatpak_id(info).is_empty() {
        let uris: Vec<String> = files.iter().map(|f| f.uri().to_string()).collect();
        let refused: Vec<&str> = refused.iter().map(String::as_str).collect();
        if sandbox::run_restricted(info, &refused, &uris, &[]) {
            return Ok(());
        }
        eprintln!("sonata2: {id}: limits not applied (bubblewrap missing?)");
    }
    // a quit pending for it is called off
    sonata_sessionui::quitonclose::mark_launch(&id);
    let ctx = ctx.cloned().unwrap_or_else(sonata_ui::apps::launch_context); // it may come to the front
    for (k, v) in gpu::launch_env(info).into_iter().chain(gpu::extra_env(info)) {
        ctx.setenv(k, v);
    }
    for n in sonata_ui::apps::NOT_HANDED_ON {
        if std::env::var_os(n).is_some() {
            ctx.unsetenv(n);
        }
    }
    // its own scope: systemd-oomd closes it alone
    appscope::watch(&ctx, &id);
    let line = info.commandline().map(|c| c.to_string_lossy().into_owned()).unwrap_or_default();
    if let Some(cmd) = command_line(&line, &gpu::extra_args(info), &gpu::launcher_args(info, &line)) {
        let flags = if cmd.contains("%U") || cmd.contains("%u") { gio::AppInfoCreateFlags::SUPPORTS_URIS } else { gio::AppInfoCreateFlags::NONE };
        match gio::AppInfo::create_from_commandline(&cmd, Some(&info.name()), flags) {
            Ok(other) => {
                unsafe { other.set_data(ENV_SET, true) };
                return other.launch(files, Some(&ctx));
            }
            Err(e) => eprintln!("sonata2: discrete GPU: {e}"), // never keeps an app from opening
        }
    }
    info.launch(files, Some(&ctx))
}

/// A login item (autostart): opened as the Dock opens its app. Its entry
/// is read from ~/.config/autostart and has no desktop id, so its file name
/// stands in (Steam got no FPS limit and no GPU as a login item); and you
/// just logged in, so a locked app doesn't ask again (autostart.py).
pub fn login_item(name: &str, info: &gio::DesktopAppInfo) -> Result<(), glib::Error> {
    mark_login_item(name, info);
    applock::trusted(|| launch(info.upcast_ref(), &[], None))
}

/// A login item's entry file name ("steam.desktop") as its desktop id,
/// when GIO gave it none.
pub fn mark_login_item(name: &str, info: &gio::DesktopAppInfo) {
    if info.id().is_none_or(|i| i.is_empty()) {
        sonata_ui::apps::set_desktop_id(info, name);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Steam on an NVIDIA card opens with -cef-disable-gpu (gpu.extra_args),
    /// before its %U; an entry needing nothing runs as it is.
    #[test]
    fn test_steam_opened_without_its_arguments() {
        let args = vec!["-cef-disable-gpu".to_string()];
        assert_eq!(command_line("/usr/bin/steam %U", &args, &[]).as_deref(), Some("/usr/bin/steam -cef-disable-gpu %U"));
        assert_eq!(command_line("/usr/bin/steam %U", &[], &[]), None);
    }

    /// The copy Sonata makes (Steam + its arguments) is launched as it is,
    /// never judged again.
    #[test]
    fn copies_are_not_judged_again() {
        let other = gio::AppInfo::create_from_commandline("true", Some("x"), gio::AppInfoCreateFlags::NONE).unwrap();
        assert!(!env_set(&other));
        unsafe { other.set_data(ENV_SET, true) };
        assert!(env_set(&other));
    }

    /// Vini: Steam opened as a login item got no FPS limit (and no GPU
    /// choice): read from ~/.config/autostart its entry has no desktop id,
    /// so gpu::app_key was "" and fpslimit never knew it was Steam. Its
    /// file name stands in (autostart.py's sonata_desktop_id).
    #[test]
    fn test_steam_at_login_got_no_fps_limit() {
        let kf = glib::KeyFile::new();
        kf.load_from_data("[Desktop Entry]\nType=Application\nName=Steam\nExec=steam -silent %U\n", glib::KeyFileFlags::NONE).unwrap();
        let info = gio::DesktopAppInfo::from_keyfile(&kf).unwrap();
        assert_eq!(gpu::app_key(info.upcast_ref()), "", "GIO gives it no id");
        mark_login_item("steam.desktop", &info);
        assert_eq!(gpu::app_key(info.upcast_ref()), "steam");
        assert_eq!(sonata_ui::apps::desktop_id(&info), "steam.desktop");
        let env = sonata_tweaks::fpslimit::env_for_key(&gpu::app_key(info.upcast_ref()), true);
        assert!(env.iter().any(|(k, v)| k == "ENABLE_FRAME_PACER" && v == "1"), "{env:?}");
        // an entry GIO knows keeps its own id
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("x.desktop"), "[Desktop Entry]\nType=Application\nName=X\nExec=true\n").unwrap();
        let from_file = gio::DesktopAppInfo::from_filename(dir.path().join("x.desktop")).unwrap();
        mark_login_item("other.desktop", &from_file);
        let want = from_file.id().map(|s| s.to_string()).unwrap_or_else(|| "other.desktop".into());
        assert_eq!(sonata_ui::apps::desktop_id(&from_file), want);
    }

    /// Vini, in the Rust session: apps opened from Search, the desktop,
    /// Recent Items, a notification, a tray icon, Show in Files, a Dock
    /// stack, Feedbacker, Camera, Videos, Task Manager and the screenshot
    /// thumbnail stayed behind the window in front, and skipped the GPU,
    /// their scope and the quit-on-close mark: those called GIO directly
    /// (apps.py patched GIO for the whole process; Rust can't). Every one
    /// goes through sonata_ui::apps now -- a new direct call fails here.
    #[test]
    fn test_launches_skipped_sonatas_launch_path_and_front() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let dirs = ["apps/spotlight", "apps/desktop", "apps/topbar", "crates/sonata-tray", "crates/sonata-notifications", "apps/dock", "apps/feedback", "apps/camera", "apps/videos", "apps/activity", "apps/capture", "crates/sonata-desktop", "apps/sonata2-rs"];
        let direct = |code: &str| {
            let gio_default = code.replace("sonata_ui::apps::launch_default_for_uri(", "");
            [".launch(&[", ".launch(files", ".launch(&files", ".launch_uris(", ".should_show()"].iter().any(|p| code.contains(p)) || gio_default.contains("launch_default_for_uri(")
        };
        fn walk(d: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
            for e in std::fs::read_dir(d).into_iter().flatten().flatten() {
                let p = e.path();
                if p.is_dir() {
                    walk(&p, out);
                } else if p.extension().is_some_and(|x| x == "rs") {
                    out.push(p);
                }
            }
        }
        let mut found = Vec::new();
        for d in dirs {
            let mut files = Vec::new();
            walk(&root.join(d).join("src"), &mut files);
            assert!(!files.is_empty(), "{d}");
            for f in files {
                if f.ends_with("sonata2-rs/src/launch.rs") {
                    continue; // the launch path itself (and this test)
                }
                for (n, line) in std::fs::read_to_string(&f).unwrap().lines().enumerate() {
                    let code = line.trim_start();
                    if !code.starts_with("//") && direct(code) {
                        found.push(format!("{}:{}: {code}", f.display(), n + 1));
                    }
                }
            }
        }
        assert!(found.is_empty(), "direct GIO launches:\n{}", found.join("\n"));
    }
}
