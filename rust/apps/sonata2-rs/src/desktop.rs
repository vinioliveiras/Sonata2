//! The ported apps' desktop entries (apps.py's write_desktop_file and
//! localize_desktop): written to `$XDG_DATA_HOME/applications`, with Name
//! and Comment in Sonata's languages from the same catalogs, so the Dock,
//! Launchpad and the menu bar show them translated.

use sonata_i18n::{Catalog, Entry, SUPPORTED};
use std::io;
use std::path::{Path, PathBuf};

const LOCALIZED_KEYS: [&str; 2] = ["Name", "Comment"];

/// `text` with `Name[pt_BR]=...` lines after each Name / Comment that a catalog translates.
pub fn localize(text: &str, catalogs: &[Catalog]) -> String {
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

/// Write every ported app's entry (only when it changed); the paths written.
pub fn write_all() -> io::Result<Vec<PathBuf>> {
    let dir = sonata_i18n::locale_dir();
    let catalogs: Vec<Catalog> = SUPPORTED.iter().map(|l| Catalog::load(&dir, l)).collect();
    let folder = data_home().join("applications");
    let mut written = Vec::new();
    let exec = launcher();
    for (file, text) in [calculator_entry(&exec), clock_entry(&exec), textedit_entry(&exec), preview_entry(&exec), notes_entry(&exec), terminal_entry(&exec), feedback_entry(&exec), diskutil_entry(&exec), camera_entry(&exec), videos_entry(&exec)] {
        let text = localize(&text, &catalogs);
        let path = folder.join(file);
        if std::fs::read_to_string(&path).ok().as_deref() != Some(text.as_str()) {
            sonata_config::atomic_write(&path, text.as_bytes(), false)?;
        }
        written.push(path);
    }
    Ok(written)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn real_catalogs() -> Vec<Catalog> {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../sonata2/locale");
        SUPPORTED.iter().map(|l| Catalog::load(&dir, l)).collect()
    }

    #[test]
    fn trial_calculator_entry_is_its_own_and_translated() {
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
}
