//! `sonata2-rs <component> [options]`: Sonata's components ported to Rust,
//! the counterpart of `python3 -m sonata2 <component>` (docs/RUST_PORT.md).
//!
//!   sonata2-rs calculator [--dark|--light]    the Calculator
//!   sonata2-rs clock [--dark|--light]         Clock
//!   sonata2-rs textedit [--new-window] [FILE...]   TextEdit
//!   sonata2-rs preview [--markup] [FILE...]        Preview
//!   sonata2-rs notes [--dark|--light]              Notes (with Reminders)
//!   sonata2-rs welcome [--preview]                 the welcome screen after login
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

const USAGE: &str = "usage: sonata2-rs <calculator|clock|textedit|preview|notes|welcome|key|screenshot|desktop-files|keep|keep-all> [--dark|--light]";

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
