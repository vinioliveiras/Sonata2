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

use std::process::ExitCode;

const USAGE: &str = "usage: sonata2-rs <calculator|clock|textedit|preview|notes|welcome|lock|spotlight|polkit|terminal|camera|videos|assistant|calendar|activity|files|portal|chooser|greeter|notifications|wallpaper|gamebar|clipboard|emoji|restart|doctor|autostart|feedback|diskutil|key|screenshot|desktop-files|keep|keep-all> [--dark|--light]";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(component) = args.first() else {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    };
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
        "autostart" => ExitCode::from(sonata_session::autostart(&args[1..]) as u8),
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
