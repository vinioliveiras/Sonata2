//! Default apps (sonata2/defaultapps.py; Settings > Default Apps): which
//! app opens web links, mail, calendar invites, music, videos, pictures,
//! PDFs, text files and folders.
//!
//! Each kind is a set of MIME types; choosing an app makes it the default
//! for the ones it can open (mimeapps.list, through Gio), the web browser
//! through xdg-settings (it also sets http/https/text/html the way every
//! desktop reads).

use gio::prelude::*;
use sonata_i18n::n_;

pub struct Kind {
    pub id: &'static str,
    /// shown translated
    pub title: &'static str,
    pub icon: &'static str,
    pub types: &'static [&'static str],
}

pub const KINDS: [Kind; 9] = [
    Kind { id: "web", title: n_!("Web Browser"), icon: "web-browser-symbolic", types: &["x-scheme-handler/https", "x-scheme-handler/http", "text/html"] },
    Kind { id: "mail", title: n_!("Mail"), icon: "mail-unread-symbolic", types: &["x-scheme-handler/mailto"] },
    Kind { id: "calendar", title: n_!("Calendar"), icon: "x-office-calendar-symbolic", types: &["text/calendar"] },
    Kind {
        id: "music",
        title: n_!("Music"),
        icon: "audio-x-generic-symbolic",
        types: &["audio/mpeg", "audio/flac", "audio/x-flac", "audio/ogg", "audio/x-vorbis+ogg", "audio/opus", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/wav", "audio/x-wav"],
    },
    Kind { id: "video", title: n_!("Videos"), icon: "video-x-generic-symbolic", types: &["video/mp4", "video/x-matroska", "video/webm", "video/quicktime", "video/x-msvideo", "video/mpeg", "video/ogg"] },
    Kind { id: "photos", title: n_!("Pictures"), icon: "image-x-generic-symbolic", types: &["image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff", "image/heic", "image/avif"] },
    Kind { id: "pdf", title: n_!("PDF Documents"), icon: "x-office-document-symbolic", types: &["application/pdf"] },
    Kind { id: "text", title: n_!("Text Files"), icon: "text-x-generic-symbolic", types: &["text/plain", "text/markdown"] },
    Kind { id: "files", title: n_!("Folders"), icon: "folder-symbolic", types: &["inode/directory"] },
];

pub fn by_id(id: &str) -> Option<&'static Kind> {
    KINDS.iter().find(|k| k.id == id)
}

/// [(desktop id, name)] of the apps that open this kind, each once.
pub fn candidates(kind_id: &str) -> Vec<(String, String)> {
    let Some(k) = by_id(kind_id) else { return Vec::new() };
    let mut out: Vec<(String, String)> = Vec::new();
    for a in gio::AppInfo::all_for_type(k.types[0]) {
        let Some(did) = a.id().map(|s| s.to_string()) else { continue };
        if !out.iter().any(|(d, _)| *d == did) && a.should_show() {
            out.push((did, a.display_name().to_string()));
        }
    }
    out
}

/// The default app's desktop id ("" when none).
pub fn current(kind_id: &str) -> String {
    let Some(k) = by_id(kind_id) else { return String::new() };
    gio::AppInfo::default_for_type(k.types[0], false).and_then(|a| a.id()).map(|s| s.to_string()).unwrap_or_default()
}

/// The kind's types an app opens (always the first one).
pub fn types_for(supported: &[String], kind_id: &str) -> Vec<&'static str> {
    let Some(k) = by_id(kind_id) else { return Vec::new() };
    k.types.iter().enumerate().filter(|(i, t)| *i == 0 || supported.iter().any(|s| s == *t)).map(|(_, t)| *t).collect()
}

pub fn set_default(kind_id: &str, desktop_id: &str) -> bool {
    if kind_id == "web" {
        return sonata_backend::system::set_default_browser(desktop_id);
    }
    let Some(app) = gio::DesktopAppInfo::new(desktop_id) else { return false };
    let supported: Vec<String> = app.supported_types().iter().map(|s| s.to_string()).collect();
    let mut ok = true;
    for t in types_for(&supported, kind_id) {
        if app.set_as_default_for_type(t).is_err() {
            ok = false;
        }
    }
    ok
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn types_an_app_opens() {
        let s: Vec<String> = vec!["audio/flac".into(), "video/mp4".into()];
        assert_eq!(types_for(&s, "music"), ["audio/mpeg", "audio/flac"]);
        assert_eq!(types_for(&[], "pdf"), ["application/pdf"]);
        assert!(types_for(&s, "nope").is_empty());
        assert_eq!(KINDS.len(), 9);
        assert!(!set_default("nope", "no-such-app.desktop"));
    }
}
