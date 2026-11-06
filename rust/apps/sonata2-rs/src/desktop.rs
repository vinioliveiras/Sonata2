//! The ported apps' desktop entries (apps.py's write_desktop_file and
//! localize_desktop): written to `$XDG_DATA_HOME/applications`, with Name
//! and Comment in Sonata's languages from the same catalogs, so the Dock,
//! Launchpad and the menu bar show them translated.

use sonata_i18n::{Catalog, Entry, SUPPORTED};
use std::io;
use std::path::{Path, PathBuf};

const LOCALIZED_KEYS: [&str; 3] = ["Name", "GenericName", "Comment"];

/// `text` with `Name[pt_BR]=...` lines after each Name / Comment that a catalog translates.
/// A web app's name is its own (X-Sonata-WebApp): left as it is.
pub fn localize(text: &str, catalogs: &[Catalog]) -> String {
    if text.contains("X-Sonata-WebApp=") {
        return text.to_string();
    }
    let mut out = Vec::new();
    for line in text.split('\n') {
        out.push(line.to_string());
        let Some((key, value)) = line.split_once('=') else { continue };
        if !LOCALIZED_KEYS.contains(&key) || value.is_empty() {
            continue;
        }
        for cat in catalogs {
            let got = match cat.get(value) {
                Some(Entry::One(s)) => Some(s.as_str()),
                Some(Entry::Plural(f)) => f.first().map(String::as_str),
                None => None,
            };
            if let Some(t) = got.filter(|t| !t.is_empty()) {
                out.push(format!("{key}[{}]={t}", cat.lang));
            }
        }
    }
    out.join("\n")
}

fn data_home() -> PathBuf {
    match std::env::var_os("XDG_DATA_HOME") {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/share"),
    }
}

/// How the desktop entry starts this binary: `$SONATA2_RS_LAUNCHER` or this executable.
fn launcher() -> String {
    std::env::var("SONATA2_RS_LAUNCHER")
        .ok()
        .filter(|l| Path::new(l).exists())
        .or_else(|| std::env::current_exe().ok().map(|p| p.display().to_string()))
        .unwrap_or_else(|| "sonata2-rs".into())
}

/// Desktop Entry quoting for one Exec argument (as __main__.self_command).
fn quote(arg: &str) -> String {
    let a = arg.replace('%', "%%");
    if !a.chars().any(|c| " \t\n\"'\\><~|&;$*?#()`".contains(c)) {
        return a;
    }
    let mut q = String::from("\"");
    for c in a.chars() {
        if "\"`$\\".contains(c) {
            q.push('\\');
        }
        q.push(c);
    }
    q.push('"');
    q.replace('\\', "\\\\") // the file's own string escape comes on top
}

/// The Calculator's entry, named "(Rust)" on trial so it is told apart
/// from the Python one in Launchpad.
pub fn calculator_entry(exec: &str) -> (String, String) {
    let id = sonata_calculator::app_id();
    let name = if sonata_config::side_by_side() { sonata_i18n::n_!("Calculator (Rust)") } else { "Calculator" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Add, subtract, multiply and divide\n\
         Icon=sonata-calculator\nCategories=Utility;Calculator;\nStartupNotify=true\n\
         Exec={} calculator\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Clock's entry ("Clock (Rust)" on trial).
pub fn clock_entry(exec: &str) -> (String, String) {
    let id = sonata_clock::app_id();
    let name = if sonata_config::side_by_side() { sonata_i18n::n_!("Clock (Rust)") } else { "Clock" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Alarms, stopwatch and timer\nIcon=sonata-clock\n\
         Categories=Utility;Clock;\nKeywords=alarm;clock;wake;timer;stopwatch;\nStartupNotify=true\n\
         Exec={} clock\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// TextEdit's entry ("TextEdit (Rust)" on trial). On trial it claims no
/// file types: the Python TextEdit stays what opens text files; try the
/// Rust one with `sonata2-rs textedit FILE`.
pub fn textedit_entry(exec: &str) -> (String, String) {
    let id = sonata_textedit::app_id();
    let trial = sonata_config::side_by_side();
    let name = if trial { sonata_i18n::n_!("TextEdit (Rust)") } else { "TextEdit" };
    let mime = if trial {
        String::new()
    } else {
        "MimeType=text/plain;text/markdown;text/x-log;application/x-shellscript;text/x-python;application/json;text/csv;\
         text/x-csrc;text/x-chdr;text/x-c++src;text/javascript;text/css;text/html;application/xml;application/x-yaml;\
         application/toml;text/rtf;application/vnd.openxmlformats-officedocument.wordprocessingml.document;\
         application/vnd.oasis.opendocument.text;\n"
            .to_string()
    };
    let exec = quote(exec);
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Write and edit plain text documents\n\
         Icon=accessories-text-editor\nCategories=Utility;TextEditor;\n{mime}StartupNotify=true\n\
         Exec={exec} textedit %F\nActions=new-window;\n\n[Desktop Action new-window]\nName=New Window\n\
         Exec={exec} textedit --new-window\n"
    );
    (format!("{id}.desktop"), text)
}

/// Disk Manager's entry (diskutil_desktop_file).
pub fn diskutil_entry(exec: &str) -> (String, String) {
    let id = sonata_diskutil::app_id();
    let name = if sonata_config::side_by_side() { sonata_i18n::n_!("Disk Manager (Rust)") } else { "Disk Manager" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Manage disks and volumes\nIcon=sonata-diskmanager\n\
         Categories=System;Utility;\nKeywords=disk;drive;partition;format;erase;mount;usb;\nStartupNotify=true\n\
         Exec={} diskutil\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Feedbacker's entry (feedback_desktop_file).
pub fn feedback_entry(exec: &str) -> (String, String) {
    let id = sonata_feedback::app_id();
    let name = if sonata_config::side_by_side() { sonata_i18n::n_!("Feedbacker (Rust)") } else { "Feedbacker" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Report a problem with Sonata\nIcon=sonata-feedback\n\
         Categories=System;Utility;\nKeywords=bug;report;problem;feedback;logs;crash;\nStartupNotify=true\n\
         Exec={} feedback\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Videos' entry (videos_desktop_file). On trial it takes no file
/// types: the Python Videos stays what opens movies.
pub fn videos_entry(exec: &str) -> (String, String) {
    let trial = sonata_config::side_by_side();
    let id = if trial { "io.github.vinioliveiras.sonata2rs.videos" } else { "io.github.vinioliveiras.sonata2.videos" };
    let name = if trial { sonata_i18n::n_!("Videos (Rust)") } else { "Videos" };
    let mime = if trial {
        String::new()
    } else {
        let types = ["video/mp4", "video/x-matroska", "video/webm", "video/x-msvideo", "video/avi", "video/quicktime", "video/mpeg", "video/ogg", "video/x-ogm+ogg", "video/x-flv", "video/3gpp", "video/3gpp2", "video/x-m4v", "video/mp2t", "video/x-ms-wmv", "audio/mpeg", "audio/mp3", "audio/flac", "audio/x-flac", "audio/ogg", "audio/x-vorbis+ogg", "audio/vorbis", "audio/opus", "audio/x-opus+ogg", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/x-aac", "audio/wav", "audio/x-wav", "audio/vnd.wave"];
        format!("MimeType={}\n", types.iter().map(|t| format!("{t};")).collect::<String>())
    };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Watch movies\nIcon=sonata-videos\n\
         Categories=AudioVideo;Video;Audio;Player;\n{mime}StartupNotify=true\nExec={} videos %F\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Task Manager's entry (activity_desktop_file).
pub fn activity_entry(exec: &str) -> (String, String) {
    let id = sonata_activity::app_id();
    let name = if sonata_config::side_by_side() { sonata_i18n::n_!("Task Manager (Rust)") } else { "Task Manager" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=See and manage running apps and processes\n\
         Icon=utilities-system-monitor\nCategories=System;Monitor;\n\
         Keywords=process;task;cpu;memory;kill;startup;services;activity;monitor;\nStartupNotify=true\nExec={} activity\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Files' entry (files_desktop_file). On trial it takes no file types:
/// the Python Files stays what opens folders and the Trash.
pub fn files_entry(exec: &str) -> (String, String) {
    let trial = sonata_config::side_by_side();
    let id = sonata_files::app_id();
    let name = if trial { sonata_i18n::n_!("Files (Rust)") } else { "Files" };
    let mime = if trial { "" } else { "MimeType=inode/directory;x-scheme-handler/trash;\n" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Browse your files\nIcon=system-file-manager\n\
         Categories=System;FileManager;Utility;\n{mime}StartupNotify=true\nExec={} files %U\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Calendar's entry (calendar_desktop_file). On trial it takes no file
/// types: the Python Calendar stays what opens .ics files.
pub fn calendar_entry(exec: &str) -> (String, String) {
    let trial = sonata_config::side_by_side();
    let id = sonata_calendar::app_id();
    let name = if trial { sonata_i18n::n_!("Calendar (Rust)") } else { "Calendar" };
    let mime = if trial { "" } else { "MimeType=text/calendar;\n" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Keep track of your events\nIcon=x-office-calendar\n\
         Categories=Office;Calendar;\n{mime}StartupNotify=true\nExec={} calendar %F\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// The Assistant's entry (assistant_desktop_file).
pub fn assistant_entry(exec: &str) -> (String, String) {
    let trial = sonata_config::side_by_side();
    let id = if trial { "io.github.vinioliveiras.sonata2rs.assistant" } else { "io.github.vinioliveiras.sonata2.assistant" };
    let name = if trial { sonata_i18n::n_!("Assistant (Rust)") } else { "Assistant" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Chat with Claude\nIcon=sonata-assistant\n\
         Categories=Utility;\nKeywords=ai;chat;claude;assistant;\nStartupNotify=true\nExec={} assistant\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Camera's entry (camera_desktop_file).
pub fn camera_entry(exec: &str) -> (String, String) {
    let trial = sonata_config::side_by_side();
    let id = if trial { "io.github.vinioliveiras.sonata2rs.camera" } else { "io.github.vinioliveiras.sonata2.camera" };
    let name = if trial { sonata_i18n::n_!("Camera (Rust)") } else { "Camera" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Take photos and videos with your camera\nIcon=camera\n\
         Categories=AudioVideo;Video;Recorder;\nKeywords=photo;video;webcam;selfie;picture;record;\nStartupNotify=true\n\
         Exec={} camera\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Terminal's entry (terminal_desktop_file). On trial it isn't a
/// TerminalEmulator: the Python Terminal stays the one the system picks.
pub fn terminal_entry(exec: &str) -> (String, String) {
    let trial = sonata_config::side_by_side();
    let id = if trial { "io.github.vinioliveiras.sonata2rs.terminal" } else { "io.github.vinioliveiras.sonata2.terminal" };
    let name = if trial { sonata_i18n::n_!("Terminal (Rust)") } else { "Terminal" };
    let categories = if trial { "System;" } else { "System;TerminalEmulator;" };
    let exec = quote(exec);
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Use the command line\nIcon=utilities-terminal\n\
         Categories={categories}\nStartupNotify=true\nExec={exec} terminal %F\nActions=new-window;\n\n\
         [Desktop Action new-window]\nName=New Window\nExec={exec} terminal --new-window\n"
    );
    (format!("{id}.desktop"), text)
}

/// Preview's entry ("Preview (Rust)" on trial, claiming no file types:
/// the Python Preview stays what opens pictures).
pub fn preview_entry(exec: &str) -> (String, String) {
    let id = sonata_preview::app_id();
    let trial = sonata_config::side_by_side();
    let name = if trial { sonata_i18n::n_!("Preview (Rust)") } else { "Preview" };
    let mime = if trial { String::new() } else { format!("MimeType={}\n", sonata_preview::window::mime_types()) };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=View pictures\nIcon=image-viewer\n\
         Categories=Graphics;Viewer;\n{mime}StartupNotify=true\nExec={} preview %F\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Notes' entry ("Notes (Rust)" on trial).
pub fn notes_entry(exec: &str) -> (String, String) {
    let id = sonata_notes::app_id();
    let name = if sonata_config::side_by_side() { sonata_i18n::n_!("Notes (Rust)") } else { "Notes" };
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={name}\nComment=Write notes, make checklists and keep reminders\n\
         Icon=sonata-notes\nCategories=Office;Utility;\nKeywords=note;reminder;todo;checklist;\nStartupNotify=true\n\
         Exec={} notes\n",
        quote(exec)
    );
    (format!("{id}.desktop"), text)
}

/// Launchpad's entry (launchpad_desktop_file): hidden from app lists, so
/// the Dock can show Launchpad as an app tile (its permanent pin needs it).
pub fn launchpad_entry(exec: &str) -> (String, String) {
    let text = format!(
        "[Desktop Entry]\nType=Application\nName={}\nComment=Find and open your apps\n\
         Icon=sonata-launchpad\nNoDisplay=true\nCategories=System;\nExec={} launchpad\n",
        sonata_desktop::names::APPS,
        quote(exec)
    );
    ("sonata2-launchpad.desktop".into(), text)
}

/// Every entry the Dock writes as it starts (__main__.run_dock), in its
/// order: Launchpad, Settings, Files, Sonata's apps, Screenshot.
pub fn dock_start_entries(exec: &str) -> Vec<(String, String)> {
    let q = quote(exec);
    let (shot, shot_text) = sonata_capture::capture::capture_desktop_entry(&q);
    vec![
        launchpad_entry(exec),
        ("sonata2-settings.desktop".into(), sonata_settings::settings_desktop_entry(&q)),
        files_entry(exec),
        calculator_entry(exec),
        textedit_entry(exec),
        preview_entry(exec),
        terminal_entry(exec),
        notes_entry(exec),
        activity_entry(exec),
        videos_entry(exec),
        diskutil_entry(exec),
        calendar_entry(exec),
        camera_entry(exec),
        feedback_entry(exec),
        clock_entry(exec),
        assistant_entry(exec),
        (shot.into(), shot_text),
    ]
}

/// Sonata apps that were removed: their launchers go from Launchpad and
/// Open With (apps.RETIRED -- Music: songs play in Videos).
pub const RETIRED: [&str; 1] = ["io.github.vinioliveiras.sonata2.music.desktop"];

/// Delete the launchers RETIRED lists from `folder`; the paths removed.
pub fn remove_retired_in(folder: &Path) -> Vec<PathBuf> {
    RETIRED.iter().map(|n| folder.join(n)).filter(|p| std::fs::remove_file(p).is_ok()).collect()
}

/// Write each entry into `folder` (only when it changed: a rewrite moves
/// the file in, so Apps and GIO read it again), translated; one failing
/// never keeps the rest. The paths written, the errors apart.
pub fn write_entries(folder: &Path, entries: Vec<(String, String)>, catalogs: &[Catalog]) -> (Vec<PathBuf>, Vec<(PathBuf, io::Error)>) {
    let (mut written, mut failed) = (Vec::new(), Vec::new());
    for (file, text) in entries {
        let text = localize(&text, catalogs);
        let path = folder.join(file);
        if std::fs::read_to_string(&path).ok().as_deref() != Some(text.as_str()) {
            if let Err(e) = sonata_config::atomic_write(&path, text.as_bytes(), false) {
                failed.push((path, e));
                continue;
            }
        }
        written.push(path);
    }
    (written, failed)
}

fn catalogs() -> Vec<Catalog> {
    let dir = sonata_i18n::locale_dir();
    SUPPORTED.iter().map(|l| Catalog::load(&dir, l)).collect()
}

/// How Sonata's entries start it: the session's `sonata2` launcher (it
/// hands what's ported to this binary in the Rust session, and is the
/// Python Sonata in the other one -- both share these entries), else this
/// binary.
fn session_command() -> String {
    std::env::var("SONATA2_LAUNCHER").ok().filter(|l| Path::new(l).exists()).unwrap_or_else(launcher)
}

/// What the Dock does as it starts (__main__.run_dock): every Sonata entry
/// written -- Screenshot was missing from Apps, and so were Settings, the
/// web apps and Launchpad's own tile, with only the Rust Dock running --
/// the retired ones taken away. On trial nothing: the Python Dock writes
/// the real entries, and the "(Rust)" names must never replace them.
pub fn write_at_dock_start() {
    if sonata_config::side_by_side() {
        return;
    }
    let folder = data_home().join("applications");
    let exec = session_command();
    let (_, failed) = write_entries(&folder, dock_start_entries(&exec), &catalogs());
    for (p, e) in failed {
        eprintln!("sonata2-dock: {}: {e}", p.display()); // never keeps the Dock from starting
    }
    remove_retired_in(&folder);
    sonata_webapps::write_all(Some(&quote(&exec))); // the web apps made with "New Web App…"
}

/// Write every ported app's entry (only when it changed); the paths written.
pub fn write_all() -> io::Result<Vec<PathBuf>> {
    let folder = data_home().join("applications");
    let exec = launcher();
    let entries = vec![calculator_entry(&exec), clock_entry(&exec), textedit_entry(&exec), preview_entry(&exec), notes_entry(&exec), terminal_entry(&exec), feedback_entry(&exec), diskutil_entry(&exec), camera_entry(&exec), videos_entry(&exec), assistant_entry(&exec), calendar_entry(&exec), activity_entry(&exec), files_entry(&exec)];
    let (written, mut failed) = write_entries(&folder, entries, &catalogs());
    match failed.pop() {
        Some((_, e)) => Err(e),
        None => Ok(written),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn real_catalogs() -> Vec<Catalog> {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../locale");
        SUPPORTED.iter().map(|l| Catalog::load(&dir, l)).collect()
    }

    #[test]
    fn trial_calculator_entry_is_its_own_and_translated() {
        let _env = ENV.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var("SONATA2_RS_SIDE_BY_SIDE");
        let (file, text) = calculator_entry("/home/me/.local/bin/sonata2-rs");
        assert_eq!(file, "io.github.vinioliveiras.sonata2rs.calculator.desktop");
        let text = localize(&text, &real_catalogs());
        assert!(text.contains("\nName=Calculator (Rust)\nName[pt_BR]=Calculadora (Rust)\nName[pt_PT]=Calculadora (Rust)\n"));
        assert!(text.contains("Comment[pt_BR]=Some, subtraia, multiplique e divida"));
        assert!(text.contains("Comment[pt_PT]=Somar, subtrair, multiplicar e dividir"));
        assert!(text.contains("Exec=/home/me/.local/bin/sonata2-rs calculator\n"));
    }

    #[test]
    fn exec_paths_with_spaces_stay_one_argument() {
        assert_eq!(quote("/a b/sonata2-rs"), "\"/a b/sonata2-rs\"".replace('\\', "\\\\"));
        assert_eq!(quote("/plain/path"), "/plain/path");
        assert_eq!(quote("100%"), "100%%");
    }

    /// Vini: Screenshot never showed in Apps with the Rust Dock -- nor did
    /// Settings, Launchpad's own tile or the web apps: only `sonata2-rs
    /// desktop-files` wrote 14 entries, and nothing ran it. The Dock's start
    /// writes every entry the Python Dock writes, and the retired Music
    /// launcher goes.
    #[test]
    fn test_screenshot_missing_from_apps() {
        let _env = ENV.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("SONATA2_RS_SIDE_BY_SIDE", "0");
        let entries = dock_start_entries("/usr/bin/sonata2");
        std::env::remove_var("SONATA2_RS_SIDE_BY_SIDE");
        let names: Vec<&str> = entries.iter().map(|(f, _)| f.as_str()).collect();
        for want in ["sonata2-launchpad.desktop", "sonata2-settings.desktop", "sonata2-screenshot.desktop", "io.github.vinioliveiras.sonata2.files.desktop", "io.github.vinioliveiras.sonata2.calculator.desktop", "io.github.vinioliveiras.sonata2.clock.desktop", "io.github.vinioliveiras.sonata2.assistant.desktop"] {
            assert!(names.contains(&want), "{want} in {names:?}");
        }
        assert_eq!(names.len(), 17, "{names:?}");
        assert!(entries.iter().all(|(_, t)| !t.contains("(Rust)")), "real names in the Rust session");
        let shot = &entries.iter().find(|(f, _)| f == "sonata2-screenshot.desktop").unwrap().1;
        assert!(shot.contains("Exec=/usr/bin/sonata2 screenshot toolbar\n"));
        let pad = &entries.iter().find(|(f, _)| f == "sonata2-launchpad.desktop").unwrap().1;
        assert!(pad.contains("\nName=Apps\n") && pad.contains("NoDisplay=true") && pad.contains("Exec=/usr/bin/sonata2 launchpad\n"));
        let set = &entries.iter().find(|(f, _)| f == "sonata2-settings.desktop").unwrap().1;
        assert!(set.contains("StartupWMClass=io.github.vinioliveiras.sonata2.settings\n"));

        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join(RETIRED[0]), "[Desktop Entry]\nName=Music\n").unwrap();
        let (written, failed) = write_entries(dir.path(), entries, &real_catalogs());
        assert!(failed.is_empty());
        assert_eq!(written.len(), 17);
        let text = std::fs::read_to_string(dir.path().join("sonata2-screenshot.desktop")).unwrap();
        assert!(text.contains("Name[pt_BR]=") && text.contains("Name[pt_PT]="), "{text}");
        assert_eq!(remove_retired_in(dir.path()), vec![dir.path().join(RETIRED[0])]);
        assert!(!dir.path().join(RETIRED[0]).exists());
    }

    /// On trial the Rust Dock runs next to the Python one: it must never
    /// write the real entries (nor "(Rust)" ones in their place).
    #[test]
    fn test_trial_dock_wrote_the_real_entries() {
        let _env = ENV.lock().unwrap_or_else(|e| e.into_inner());
        let data = tempfile::tempdir().unwrap();
        let old = std::env::var_os("XDG_DATA_HOME");
        std::env::set_var("XDG_DATA_HOME", data.path());
        std::env::remove_var("SONATA2_RS_SIDE_BY_SIDE");
        write_at_dock_start();
        match old {
            Some(v) => std::env::set_var("XDG_DATA_HOME", v),
            None => std::env::remove_var("XDG_DATA_HOME"),
        }
        assert!(!data.path().join("applications").exists());
    }

    /// Tests that set the process' environment take turns.
    pub static ENV: std::sync::Mutex<()> = std::sync::Mutex::new(());
}
