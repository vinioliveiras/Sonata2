//! Sonata's own wallpapers (sonata2/wallpapers.py: data/wallpapers, photos
//! from Unsplash under the Unsplash License), shown in Settings > Wallpaper.
//! No GTK here.
//!
//! A wallpaper is a picture for Light and one for Dark (the same file for
//! most); "Mountains", the default, changes with the appearance: snowy
//! peaks by day, the same kind of peaks under the stars at night.

use sonata_i18n::n_;
use std::path::PathBuf;

#[derive(Clone, Debug, PartialEq)]
pub struct Wallpaper {
    pub id: &'static str,
    /// shown translated: gettext(w.name)
    pub name: &'static str,
    pub light: PathBuf,
    pub dark: PathBuf,
}

pub fn folder() -> PathBuf {
    sonata_config::data_path("wallpapers")
}

fn w(id: &'static str, name: &'static str, light: &str, dark: Option<&str>) -> Wallpaper {
    let f = folder();
    Wallpaper { id, name, light: f.join(format!("{light}.jpg")), dark: f.join(format!("{}.jpg", dark.unwrap_or(light))) }
}

pub fn catalog() -> Vec<Wallpaper> {
    vec![
        w("mountains", n_!("Mountains"), "snow-peaks", Some("starry-night")), // the default (Light / Dark)
        w("above-the-clouds", n_!("Above the Clouds"), "above-the-clouds", None),
        w("snow-peaks", n_!("Snow Peaks"), "snow-peaks", None),
        w("starry-night", n_!("Starry Night"), "starry-night", None),
        w("lake-cabin", n_!("Lake Cabin"), "lake-cabin", None),
        w("valley", n_!("Valley"), "valley", None),
        w("golden-meadow", n_!("Golden Meadow"), "golden-meadow", None),
        w("rolling-hills", n_!("Rolling Hills"), "rolling-hills", None),
        w("highlands", n_!("Highlands"), "highlands", None),
        w("pier", n_!("Pier"), "pier", None),
    ]
}

pub fn default() -> Wallpaper {
    catalog().remove(0)
}

pub fn uri(path: &std::path::Path) -> String {
    format!("file://{}", path.display())
}

pub fn default_uris() -> (String, String) {
    let d = default();
    (uri(&d.light), uri(&d.dark))
}

/// The catalog id the desktop shows now ("" for the user's own picture).
pub fn current(light_uri: &str, dark_uri: &str) -> String {
    let dark_uri = if dark_uri.is_empty() { light_uri } else { dark_uri };
    catalog().into_iter().find(|w| uri(&w.light) == light_uri && uri(&w.dark) == dark_uri).map(|w| w.id.to_string()).unwrap_or_default()
}

/// Small copy for the gallery (the full 4K picture would cost memory).
pub fn thumb(w: &Wallpaper) -> PathBuf {
    let p = folder().join("thumbs").join(format!("{}.jpg", w.id)); // Mountains: half day, half night
    if p.exists() { p } else { w.light.clone() }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_ids_and_current() {
        let (l, d) = default_uris();
        assert!(l.ends_with("snow-peaks.jpg") && d.ends_with("starry-night.jpg"));
        assert_eq!(current(&l, &d), "mountains");
        let peaks = uri(&folder().join("snow-peaks.jpg"));
        assert_eq!(current(&peaks, ""), "snow-peaks");
        assert_eq!(current("file:///home/x/mine.jpg", ""), "");
        let ids: Vec<_> = catalog().iter().map(|w| w.id).collect();
        assert_eq!(ids.len(), 10);
        assert_eq!(l, sonata_config::prefs::get(sonata_config::prefs::BG, "picture-uri", "", &Default::default()));
    }

    #[test]
    fn every_wallpaper_is_shipped() {
        for w in catalog() {
            assert!(w.light.is_file() && w.dark.is_file(), "{}", w.id);
            assert!(thumb(&w).is_file());
        }
    }
}
