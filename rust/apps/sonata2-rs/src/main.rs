//! `sonata2-rs <component> [options]`: Sonata's components ported to Rust,
//! the counterpart of `python3 -m sonata2 <component>` (docs/RUST_PORT.md).
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
//!                                             (Rust components here, the rest through `sonata2`)
//!
//! On trial (the default) everything runs next to the installed Python
//! Sonata: its own app ids, its own settings folder for writing.

mod desktop;
mod gamebar_host;
mod host;

use std::process::ExitCode;

const USAGE: &str = "usage: sonata2-rs <calculator|clock|textedit|preview|notes|welcome|lock|spotlight|polkit|terminal|camera|videos|assistant|calendar|activity|files|portal|chooser|greeter|notifications|show-desktop|settings|launchpad|keyring|prefs|topbar|dock|lock-wait|setup|session-try|capture|share-picker|webapp|switcher|mission|wallpaper|gamebar|clipboard|emoji|restart|doctor|autostart|feedback|diskutil|key|screenshot|desktop-files|keep|keep-all|xsettings> [--dark|--light]";

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
const NO_WINDOWS: &[&str] = &["key", "screenshot", "keep", "keep-all", "autostart", "xsettings", "prefs", "desktop-files", "restart", "doctor", "lock-wait", "keyring", "show-desktop"];

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
        "doctor" => ExitCode::from(sonata_session::doctor(&args[1..]) as u8),
        // in the Rust session the setup steps are the Rust ones; on trial the
        // Python session's own autostart runs and this one only tries it
        "autostart" if !sonata_config::side_by_side() => ExitCode::from(sonata_session::autostart::autostart_with(&login_steps) as u8),
        "autostart" => ExitCode::from(sonata_session::autostart(&args[1..]) as u8),
        // XWayland's startup script (tools/wayfire-config.sh): X11 apps in Sonata's look
        "xsettings" => ExitCode::from(sonata_system::xsettings::run() as u8),
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
        // `sonata2 <component>` in the "Sonata (Rust preview)" session
        // (install.sh's launcher): 0 when the Rust build runs it, so the
        // apps and helpers Launchpad, the Dock, D-Bus and the session start
        // through `sonata2` are the Rust ones there, and the Python ones
        // where nothing is ported (self-update...)
        "--handles" => ExitCode::from(if args.get(1).is_some_and(|c| handles(c)) { 0 } else { 1 }),
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
    let me = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "sonata2-rs".into());
    let spawn = move |args: Vec<String>| {
        let me = me.clone();
        Rc::new(move || {
            let _ = std::process::Command::new(&me).args(&args).spawn();
        }) as Rc<dyn Fn()>
    };
    let mut s = sonata_topbar::seams::Services { mixer: Some(sonata_topbar::seams::system_mixer()), ..Default::default() };
    s.night_shift = Some((Rc::new(sonata_sessionui::nightshift::is_on), Rc::new(sonata_sessionui::nightshift::set_manual)));
    s.end_session = Some(Rc::new(|kind: &str, reopen: bool| {
        sonata_sessionui::quitapps::end_session(kind, Some(host::quit_windows()), Some(reopen));
    }));
    // the capture lives in the menu bar's process (host.rs); on trial the
    // Python menu bar keeps it, and `sonata2-rs capture` is the Rust one
    s.capture_toolbar = Some(if sonata_config::side_by_side() { spawn(vec!["capture".into(), "toolbar".into()]) } else { Rc::new(host::show_capture_toolbar) });
    host::fill_services(&mut s);
    s
}

#[cfg(test)]
mod handles_tests {
    /// Vini, in the Rust session: Task Manager, Terminal and the portal
    /// opened as the Python ones -- Launchpad, the Dock and D-Bus start
    /// them through `sonata2`, which only knew Python. The launcher asks
    /// `sonata2-rs --handles` first there.
    #[test]
    fn test_rust_session_opened_python_apps() {
        for c in ["activity", "terminal", "files", "portal", "settings", "topbar", "screenshot"] {
            assert!(super::handles(c), "{c}");
        }
        for c in ["self-update", "gallery", "", "--handles"] {
            assert!(!super::handles(c), "{c}");
        }
    }
}
