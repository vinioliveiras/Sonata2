//! `sonata2-rs <component> [options]`: every Sonata component, one binary
//! (docs/RUST_PORT.md).
//!
//!   sonata2-rs calculator [--dark|--light]    the Calculator
//!   sonata2-rs clock [--dark|--light]         Clock
//!   sonata2-rs textedit [--new-window] [FILE...]   TextEdit
//!   sonata2-rs preview [--markup] [FILE...]        Preview
//!   sonata2-rs notes [--dark|--light]              Notes (with Reminders)
//!   sonata2-rs welcome [--preview]                 the welcome screen after login
//!   sonata2-rs lock                                lock the session
//!   sonata2-rs spotlight [--background] [--search TEXT]   Spotlight (again: toggles it)
//!   sonata2-rs polkit                              the password prompt for system actions
//!   sonata2-rs diskutil                            Disk Manager
//!   sonata2-rs feedback                            Feedbacker: report a problem
//!   sonata2-rs terminal [--new-window] [--exec CMD] [FOLDER...]   Terminal
//!                                                  (sonata2-rs-terminal: only it needs libvte)
//!   sonata2-rs camera                              Camera (sonata2-rs-camera: only it needs GStreamer)
//!   sonata2-rs videos [FILE...]                    Videos (sonata2-rs-videos, the same)
//!   sonata2-rs calendar [FILE.ics...]             Calendar
//!   sonata2-rs files [FOLDER...] [--background]   Files
//!   sonata2-rs restart [COMPONENT...]            restart shell components
//!   sonata2-rs doctor [windows]                   is this computer ready for Sonata?
//!   sonata2-rs autostart                          login items, at login
//!   sonata2-rs clipboard [--background]           clipboard history picker
//!   sonata2-rs emoji [--background]               Character Viewer
//!   sonata2-rs gamebar [--preview]                the game bar (on trial: its own overlay)
//!   sonata2-rs wallpaper [--preview]              the desktop: wallpaper (video too) and icons
//!   sonata2-rs switcher [next|prev] [--preview]   the app switcher
//!   sonata2-rs mission [--preview]                Mission Control (on trial: --preview only)
//!   sonata2-rs webapp new | edit ID | ID          web apps (WebKit opened at run time)
//!   sonata2-rs capture [toolbar|screen|area|...]  screenshots, recording, live drawing
//!   sonata2-rs share-picker                       the screen-sharing picker (portal)
//!   sonata2-rs lock-wait                          swayidle's lock command
//!   sonata2-rs setup [--preview]                  first-login setup
//!   sonata2-rs session-try corners|blackout|alarm|timer|idle|nightshift|displays  try a session helper (writes nothing)
//!   sonata2-rs dock [--preview] [--set K=V] ...   the Dock (on trial: try it with --preview)
//!   sonata2-rs topbar [--preview] [--menu N]      the menu bar and Control Center
//!   sonata2-rs keyring ...                        the login keyring (keyring.py)
//!   sonata2-rs prefs session-env                  defaults filled in, the session's environment
//!   sonata2-rs launchpad [--preview] [--search T] Launchpad (again: toggles it)
//!   sonata2-rs settings [--page ID]               Settings
//!   sonata2-rs show-desktop                       Super+D: every window away, and back
//!   sonata2-rs notifications [--preview] [--center]  notifications (on trial: off the real bus name)
//!   sonata2-rs greeter                            the login screen (SONATA_GREETER_FAKE=1: preview)
//!   sonata2-rs portal                             the portal backend (not on trial)
//!   sonata2-rs chooser [open|save|folder]         an Open / Save panel, to try
//!   sonata2-rs activity                            Task Manager
//!   sonata2-rs assistant                           Assistant (sonata2-rs-assistant: libsoup, libsecret)
//!   sonata2-rs key <volume-up|volume-down|volume-mute|brightness-up|
//!                  brightness-down|play-pause|next|previous>   a media key
//!   sonata2-rs screenshot [screen|area|edit]    a screenshot (the menu bar's, else grim)
//!   sonata2-rs desktop-files                  write the ported apps' desktop entries
//!   sonata2-rs keep <component> [args]        run it, start it again if it crashes
//!   sonata2-rs keep-all <component>[,args] ...  the same, several in one process
//!   sonata2-rs migrate user | install --prefix P  carry settings and data over from the
//!                                             Python Sonata and the Rust trial (install.sh)
//!
//! Installed, `sonata2` is this binary (install.sh links it): the session,
//! Wayfire's keys, D-Bus and desktop entries start `sonata2 <component>`.

mod desktop;
mod gamebar_host;
mod host;
mod launch;
mod procenv;

use std::process::ExitCode;

const USAGE: &str = "usage: sonata2-rs <calculator|clock|textedit|preview|notes|welcome|lock|spotlight|polkit|terminal|camera|videos|assistant|calendar|activity|files|portal|chooser|greeter|notifications|show-desktop|settings|launchpad|keyring|prefs|topbar|dock|lock-wait|setup|session-try|capture|share-picker|webapp|switcher|mission|wallpaper|gamebar|clipboard|emoji|restart|doctor|autostart|feedback|diskutil|key|screenshot|desktop-files|keep|keep-all|xsettings|wayfire-config|self-update|migrate|gallery> [--dark|--light]";

/// autostart.py's setup steps before the login items, each on its own
/// (one failing never keeps the rest, or the apps, from starting).
fn login_steps() {
    // Sonata title bars for Chrome, VS Code... (before they start)
    sonata_tweaks::titlebars::apply(None);
    // ç on U.S. International, before the apps start (Vini)
    sonata_system::compose::ensure(None);
    // forced GTK_THEME gone from D-Bus activated apps; Flatpak apps in Sonata's look
    sonata_system::gtkstyle::reset_env();
    sonata_tweaks::flatpak_theme::apply();
    // KeePassXC: saved passwords for every app
    sonata_tweaks::keyring::start();
}

/// The commands that run and return, or run without GTK: no start-up trace.
const NO_WINDOWS: &[&str] = &["key", "screenshot", "keep", "keep-all", "autostart", "xsettings", "wayfire-config", "prefs", "desktop-files", "restart", "doctor", "lock-wait", "keyring", "show-desktop", "self-update", "migrate"];

/// The components USAGE lists (the ones this binary dispatches).
fn handles(component: &str) -> bool {
    let list = USAGE.split_once('<').and_then(|(_, r)| r.split_once('>')).map_or("", |(l, _)| l);
    list.split('|').any(|c| c == component)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(component) = args.first() else {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    };
    // `sonata2-rs launchpad` (Super, F4, the pinch): the resident Launchpad
    // told straight away -- starting GTK only to pass the word on took
    // ~0.5 s before it began to open (__main__.py's tell_launchpad)
    if args.len() == 1 && component == "launchpad" && tell_launchpad("toggle", 800) {
        return ExitCode::SUCCESS;
    }
    // a Chromium web app's windows ("chrome-<site>-Default") are its own
    // app, not Chrome's (apps.py looks through webapps.json the same way)
    sonata_desktop::appmatch::set_webapp_resolver(sonata_webapps::desktop_for_window_class);
    // every app Sonata opens goes through Sonata's launch path (apps.py
    // patched GIO for that; here sonata_ui::apps::launch is the one door)
    sonata_ui::apps::set_launcher(launch::launch);
    if handles(component) && procenv::setup(component) {
        // GTK reads GDK_DEBUG once, at init (done by the first idle): the
        // apps opened from the shell get the session's again
        gtk::glib::idle_add_local_once(procenv::restore_gdk_debug);
    }
    if handles(component) && !NO_WINDOWS.contains(&component.as_str()) && !args.iter().any(|a| a == "--preview") {
        // __main__.py: the start-up watchdog (detailed logs) until the first
        // idle, and the main-loop stall log (ui.theme.setup) in every
        // process that shows something
        sonata_system::trace::start(component);
        let c = component.clone();
        gtk::glib::idle_add_local_once(move || {
            sonata_system::trace::ready("first idle");
            sonata_session::stallwatch::start(&c);
        });
    }
    match component.as_str() {
        "calculator" => {
            let code = sonata_calculator::window::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "clock" => {
            let code = sonata_clock::window::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "textedit" => {
            let code = sonata_textedit::window::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "preview" => {
            let code = sonata_preview::window::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "notes" => {
            let code = sonata_notes::window::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "key" if args.len() > 1 => ExitCode::from(sonata_session::keys::key(&args[1]) as u8),
        // Wayfire runs the screenshot keys (always_binding_) even over the
        // lock screen: no capture of it, no editor behind it (__main__.py screenshot)
        "screenshot" if sonata_sessionui::idlelock::is_locked() => ExitCode::SUCCESS,
        "screenshot" => ExitCode::from(sonata_session::keys::screenshot(args.get(1).map_or("screen", String::as_str)) as u8),
        "welcome" => {
            let code = sonata_welcome::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "lock" => {
            let code = sonata_lock::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "spotlight" => {
            let code = sonata_spotlight::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "terminal" => ExitCode::from(terminal(&args[1..]) as u8),
        "camera" => ExitCode::from(camera(&args[1..]) as u8),
        "videos" => ExitCode::from(own_binary("videos", &args[1..], "GStreamer") as u8),
        "assistant" => ExitCode::from(own_binary("assistant", &args[1..], "libsoup and libsecret") as u8),
        "activity" => {
            let code = sonata_activity::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "restart" => ExitCode::from(sonata_session::restart(&args[1..]) as u8),
        // Sonata's own update (Software Update's Update runs it in a
        // terminal through the `sonata2` launcher, which hands it here in
        // the Rust session): the release installed, the Rust build rebuilt
        "self-update" => ExitCode::from(sonata_system::selfupdate::main(&args[1..]).clamp(0, 255) as u8),
        "doctor" => ExitCode::from(sonata_session::doctor(&args[1..]) as u8),
        // the login items through Sonata's launch path (their GPU, Steam's
        // FPS limit, their own scopes -- sent before this short process ends)
        "autostart" => {
            let code = sonata_session::autostart::autostart_with(&launch::login_item, &login_steps);
            sonata_desktop::appscope::flush();
            ExitCode::from(code as u8)
        }
        // the design system's page (ui/gallery.py)
        "gallery" => {
            let code = sonata_ui::gallery::run_gallery(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        // XWayland's startup script (tools/wayfire-config.sh): X11 apps in Sonata's look
        "xsettings" => ExitCode::from(sonata_system::xsettings::run() as u8),
        // tools/wayfire-config.sh at every login of the Rust session: its
        // Python steps (the window frame, the migrations, the overrides), so
        // that session's login needs no python3
        "wayfire-config" => ExitCode::from(sonata_backend::wfconfig::login_main(&args[1..]) as u8),
        "clipboard" => {
            let code = sonata_clipboard::run_clipboard(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "emoji" => {
            let code = sonata_clipboard::run_emoji(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "gamebar" => {
            let code = sonata_gamebar::run_gamebar(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "wallpaper" => {
            let code = sonata_desktop_icons::run_wallpaper(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "switcher" => {
            let code = sonata_mission::run_switcher(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "mission" => {
            let code = sonata_mission::run_mission(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "webapp" => {
            let code = sonata_webapps::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "capture" => {
            let code = sonata_capture::run_capture(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "share-picker" => {
            let code = sonata_capture::run_share_picker(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "lock-wait" => ExitCode::from(sonata_sessionui::idlelock::run_lock_wait(&args[1..]) as u8),
        "setup" => {
            let code = sonata_sessionui::setup::run_setup(&args[1..], Default::default());
            ExitCode::from(code.value() as u8)
        }
        "session-try" => {
            let code = sonata_sessionui::run_try(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "dock" => {
            // __main__.run_dock: Sonata's desktop entries as the Dock starts
            // (here: the Dock can't depend on Settings, Capture and the web
            // apps that depend on it); nothing on trial
            desktop::write_at_dock_start();
            let code = sonata_dock::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "topbar" => {
            let code = sonata_topbar::run_with(&args[1..], topbar_services(), host::topbar_started);
            ExitCode::from(code.value() as u8)
        }
        "keyring" => ExitCode::from(sonata_tweaks::keyring::main(&args[1..]) as u8),
        // tools/session-env.sh, every login: the environment the session needs
        "prefs" if args.get(1).map(String::as_str) == Some("session-env") => {
            print!("{}", sonata_tweaks::prefs_write::session_env());
            ExitCode::SUCCESS
        }
        "launchpad" => {
            let code = sonata_launchpad::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "settings" => {
            let code = sonata_settings::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        // install.sh: USBGuard set up as Settings' "Set Up…" does (the script, printed)
        "settings-usb-setup" => {
            println!("{}", sonata_settings::pages::system::usb_setup_script());
            ExitCode::SUCCESS
        }
        // Super+D: the Dock does it
        "show-desktop" => ExitCode::from(if sonata_session::keys::app_action(sonata_dock::app_id(), "show-desktop", None) { 0 } else { 1 }),
        "notifications" => {
            let code = sonata_notifications::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "greeter" => {
            let code = sonata_greeter::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "portal" => {
            let code = sonata_portal::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "chooser" => {
            let code = sonata_portal::run_chooser(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "files" => {
            let code = sonata_files::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "calendar" => {
            let code = sonata_calendar::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "diskutil" => {
            let code = sonata_diskutil::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "feedback" => {
            let code = sonata_feedback::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "polkit" => {
            let code = sonata_polkit::run(&args[1..]);
            ExitCode::from(code.value() as u8)
        }
        "keep" if args.len() > 1 => ExitCode::from(sonata_session::keep::keep(&args[1..]) as u8),
        "keep-all" => ExitCode::from(sonata_session::keep::keep_all(&args[1..]) as u8),
        "desktop-files" => match desktop::write_all() {
            Ok(paths) => {
                for p in paths {
                    println!("{}", p.display());
                }
                ExitCode::SUCCESS
            }
            Err(e) => {
                eprintln!("sonata2-rs: desktop files: {e}");
                ExitCode::FAILURE
            }
        },
        // the upgrade: what the Python Sonata and the Rust trial kept, carried
        // over (install.sh runs it once the new build is in place)
        "migrate" => ExitCode::from(sonata_config::migrate::main(&args[1..]) as u8),
        "-h" | "--help" | "help" => {
            println!("{USAGE}");
            ExitCode::SUCCESS
        }
        other => {
            eprintln!("sonata2-rs: no component {other:?} (yet)\n{USAGE}");
            ExitCode::from(2)
        }
    }
}

/// The running Launchpad's `action` ("toggle", "close") over D-Bus, waited
/// for: GIO only, no GTK. False when it isn't running (never started by
/// D-Bus for this: then this process starts it the long way).
fn tell_launchpad(action: &str, timeout_ms: i32) -> bool {
    use gtk::{gio, glib};
    use gtk::prelude::*;
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return false };
    let id = sonata_launchpad::app_id();
    let args = (action, Vec::<glib::Variant>::new(), std::collections::HashMap::<String, glib::Variant>::new()).to_variant();
    bus.call_sync(Some(id), &format!("/{}", id.replace('.', "/")), "org.freedesktop.Application", "ActivateAction", Some(&args), None, gio::DBusCallFlags::NO_AUTO_START, timeout_ms, gio::Cancellable::NONE)
        .is_ok()
}

/// Apps with a library the rest must not need (Terminal: libvte; Camera:
/// GStreamer) are binaries of their own: the one next to this one, else
/// in $PATH. Replaces this process; `missing` says what to install.
fn own_binary(name: &str, args: &[String], missing: &str) -> i32 {
    use std::os::unix::process::CommandExt;
    let file = format!("sonata2-rs-{name}");
    let beside = std::env::current_exe().ok().and_then(|e| e.parent().map(|d| d.join(&file))).filter(|p| p.exists());
    let exe = beside.map(|p| p.into_os_string()).unwrap_or_else(|| file.into());
    let err = std::process::Command::new(&exe).args(args).exec();
    eprintln!("sonata2-rs: {name}: {err} ({missing})");
    1
}

fn terminal(args: &[String]) -> i32 {
    own_binary("terminal", args, &sonata_i18n::tr!("Terminal needs VTE for GTK 4 (package vte4 / gir1.2-vte-3.91)."))
}

fn camera(args: &[String]) -> i32 {
    own_binary("camera", args, "GStreamer")
}

/// The menu bar's seams, filled from the crates ported since (the menu bar
/// can't depend on them all itself: some depend on it).
fn topbar_services() -> sonata_topbar::seams::Services {
    use std::rc::Rc;
    let mut s = sonata_topbar::seams::Services { mixer: Some(sonata_topbar::seams::system_mixer()), ..Default::default() };
    s.night_shift = Some((Rc::new(sonata_sessionui::nightshift::is_on), Rc::new(sonata_sessionui::nightshift::set_manual)));
    s.end_session = Some(Rc::new(|kind: &str, reopen: bool| {
        sonata_sessionui::quitapps::end_session(kind, Some(host::quit_windows()), Some(reopen));
    }));
    // the capture lives in the menu bar's process (host.rs)
    s.capture_toolbar = Some(Rc::new(host::show_capture_toolbar));
    host::fill_services(&mut s);
    s
}

#[cfg(test)]
mod launchpad_tests {
    /// Vini: Launchpad took ~0.5 s to begin opening in the Rust session --
    /// `sonata2-rs launchpad` started GTK and the whole Launchpad only to
    /// tell the running one to toggle. It asks the running one first, before
    /// anything else, and goes on the long way only when none answers.
    #[test]
    fn test_launchpad_started_gtk_to_toggle() {
        let src = include_str!("main.rs");
        let main = &src[src.find("fn main() -> ExitCode").unwrap()..];
        let fast = main.find("tell_launchpad(\"toggle\", 800)").expect("the fast path");
        assert!(fast < main.find("set_launcher").unwrap() && fast < main.find("procenv::setup").unwrap());
        // nothing running (no session bus here): false at once, never started by D-Bus
        let t = std::time::Instant::now();
        assert!(!super::tell_launchpad("toggle", 800));
        assert!(t.elapsed() < std::time::Duration::from_secs(2));
    }
}

#[cfg(test)]
mod screenshot_tests {
    /// The screenshot keys are Wayfire always_binding_ keys (they work in
    /// Mission Control), so they run over the lock screen too: `screenshot`
    /// does nothing there (no capture of it, no editor behind it), checked
    /// before the menu bar or grim are asked.
    #[test]
    fn test_screenshot_over_the_lock_screen() {
        let src = include_str!("main.rs");
        let main = &src[src.find("fn main() -> ExitCode").unwrap()..];
        let guard = main.find("\"screenshot\" if sonata_sessionui::idlelock::is_locked() => ExitCode::SUCCESS").expect("the lock guard");
        assert!(guard < main.find("\"screenshot\" => ExitCode::from(sonata_session::keys::screenshot").unwrap());
    }
}

#[cfg(test)]
mod handles_tests {
    /// The Python launcher's `--handles` question is gone with it: the
    /// installed `sonata2` is this binary, and every command runs here.
    #[test]
    fn test_portal_stayed_python_in_the_rust_session() {
        let install = include_str!("../../../../install.sh");
        assert!(install.contains("ln -sf sonata2-rs \"$BIN/sonata2\""), "sonata2 is the Rust binary");
        assert!(!install.contains("python3 -m sonata2"));
        assert!(super::handles("migrate") && super::NO_WINDOWS.contains(&"migrate"));
    }

    /// Vini, in the Rust session: Task Manager, Terminal and the portal
    /// opened as the Python ones -- Launchpad, the Dock and D-Bus start
    /// them through `sonata2`, which only knew Python. The launcher asks
    /// `sonata2-rs --handles` first there.
    #[test]
    fn test_rust_session_opened_python_apps() {
        for c in ["activity", "terminal", "files", "portal", "settings", "topbar", "screenshot"] {
            assert!(super::handles(c), "{c}");
        }
        for c in ["", "--handles", "nothing"] {
            assert!(!super::handles(c), "{c}");
        }
    }

    /// In the Rust session, Software Update's Update ran the Python
    /// `sonata2 self-update`: the Rust build had no such command, and the
    /// session went on running the old Rust Sonata after an update. The
    /// launcher hands it to sonata2-rs now (no window, no start-up trace).
    #[test]
    fn test_self_update_ran_the_python_one() {
        assert!(super::handles("self-update"));
        assert!(super::NO_WINDOWS.contains(&"self-update"));
        let src = include_str!("main.rs");
        assert!(src.contains("\"self-update\" => ExitCode::from(sonata_system::selfupdate::main("));
    }
}
