//! Launchpad's arithmetic and choices without GTK (unit-tested): the grid
//! for a display, the cell under the pointer, the Apps Menu's panel and
//! tabs, the arrow keys, the Escape steps, the command line.

use serde_json::{json, Value};
use sonata_desktop::launchpad_model::{Folder, Item};
use sonata_i18n::n_;

pub const OPEN_MS: u32 = 230;
pub const CLOSE_MS: u32 = 170;
pub const FOLDER_HOLD_MS: u64 = 400;
pub const FLIP_HOLD_MS: u64 = 650;
/// icons make way only after a short pause (so you can reach an icon to make a folder)
pub const REORDER_HOLD_MS: u64 = 220;
/// icons zoom in from 112 % while fading in (Big Sur)
pub const ZOOM_FROM: f64 = 1.12;
/// ui.edit.HOLD_MS: press and hold before jiggle mode starts
pub const HOLD_MS: u32 = 800;
/// the folders' zoom in / out (the CSS's fold_in / fold_out)
pub const FOLD_IN_MS: f64 = 240.0;
pub const FOLD_OUT_MS: f64 = 160.0;

// -- the Apps Menu (launchpad_window.py) --------------------------------------------
/// ~/.config/sonata2/launcher.json
pub const LAUNCHER: &str = "launcher";
pub const MENU_ICON: i32 = 64;
pub const MENU_OPEN_MS: f64 = 220.0;
pub const MENU_CLOSE_MS: f64 = 140.0;
pub const MENU_FADE_MS: f64 = 150.0;
/// where the style is chosen (Vini)
pub const SETTINGS_PAGE: &str = "appearance";

/// (value, title): Settings > Appearance > Apps style.
pub const STYLES: [(&str, &str); 2] = [("fullscreen", n_!("Full Screen")), ("window", sonata_desktop::names::APPS_MENU)];

/// tab id, title, freedesktop categories (first match wins, in this order)
pub const CATEGORIES: [(&str, &str, &[&str]); 5] = [
    ("social", n_!("Social"), &["Chat", "InstantMessaging", "Email", "IRCClient", "Telephony", "VideoConference", "News", "Feed", "ContactManagement"]),
    ("creativity", n_!("Creativity"), &["Graphics", "2DGraphics", "3DGraphics", "RasterGraphics", "VectorGraphics", "Photography", "AudioVideoEditing", "Midi", "Mixer", "Sequencer", "Publishing"]),
    ("entertainment", n_!("Entertainment"), &["Game", "AudioVideo", "Audio", "Video", "Player", "TV", "Music"]),
    ("productivity", n_!("Productivity & Finance"), &["Office", "Finance", "Development", "Education", "Science", "Calendar", "ProjectManagement", "WordProcessor", "Spreadsheet", "Presentation", "IDE", "TextEditor"]),
    ("utilities", n_!("Utilities"), &["Utility", "System", "Settings", "FileManager", "Accessibility", "Monitor", "TerminalEmulator", "Network", "WebBrowser", "Security", "Archiving", "Core"]),
];
pub const OTHER: (&str, &str) = ("other", n_!("Other"));

/// The layout chosen: "fullscreen" (the default, and for anything unknown) or "window".
pub fn style_of(cfg: &serde_json::Map<String, Value>) -> &'static str {
    match cfg.get("style").and_then(Value::as_str) {
        Some("window") => "window",
        _ => "fullscreen",
    }
}

pub fn style() -> &'static str {
    style_of(&sonata_config::load(LAUNCHER, &json!({"style": "fullscreen"})))
}

/// The tab an app goes under (its desktop entry's Categories, "A;B;").
pub fn category_of(categories: &str) -> &'static str {
    let cats: Vec<&str> = categories.split(';').filter(|c| !c.is_empty()).collect();
    CATEGORIES.iter().find(|(_, _, names)| cats.iter().any(|c| names.contains(c))).map(|c| c.0).unwrap_or(OTHER.0)
}

/// The panel for a w x h screen (Vini: smaller than at first, which took
/// 60 % of the width): about 46 % x 58 %, within 560-940 x 440-680.
pub fn panel_size(w: i32, h: i32) -> (i32, i32) {
    (((w as f64 * 0.46) as i32).clamp(560, 940), ((h as f64 * 0.58) as i32).clamp(440, 680))
}

/// Rows of the Apps Menu's page `i` of `n`: the last one only as tall as
/// its apps (no empty rows at the end).
pub fn menu_page_rows(i: usize, n: usize, len: usize, cols: usize, rows: usize) -> usize {
    if i + 1 < n {
        rows
    } else {
        len.div_ceil(cols.max(1)).max(1)
    }
}

// -- the full screen's grid ------------------------------------------------------------

#[derive(Debug, PartialEq)]
pub struct Grid {
    /// the side margins (~12 %)
    pub side: i32,
    pub cols: i64,
    /// as many as fit (set_grid clamps and truncates)
    pub rows: f64,
    pub icon: i32,
}

/// Big Sur proportions for a w x h display with the Dock reserving `dock`
/// px at `edge`: ~12 % side margins, icon ~56 % of a cell, as many rows as
/// the display has room for (search, page dots and the Dock take their
/// share): 5 on 16:9 screens, 6 on taller ones like 16:10. The icon is
/// worked out for the grid set_grid ends up with: (cols, rows) given.
pub fn grid_for(w: i32, h: i32, dock: i64, edge: &str) -> Grid {
    let side = (w as f64 * 0.12) as i32;
    let room = (h as i64 - 160 - if edge == "bottom" { dock } else { 0 }).max(1) as f64;
    let width = (w as i64 - 2 * side as i64 - if edge == "left" || edge == "right" { dock } else { 0 }).max(1) as f64;
    let cols = if width >= 900.0 { 7 } else { (width / 150.0) as i64 }; // macOS: 7; fewer on narrow screens
    let rows = room / (width / cols.max(1) as f64 * 0.72).max(1.0); // row pitch ~72 % of a column (macOS)
    Grid { side, cols, rows, icon: 0 }
}

/// The icon size once the page is (cols, rows).
pub fn icon_for(w: i32, h: i32, dock: i64, edge: &str, cols: usize, rows: usize) -> i32 {
    let side = (w as f64 * 0.12) as i32;
    let room = (h as i64 - 160 - if edge == "bottom" { dock } else { 0 }).max(1) as f64;
    let width = (w as i64 - 2 * side as i64 - if edge == "left" || edge == "right" { dock } else { 0 }).max(1) as f64;
    let (cell_w, cell_h) = (width / cols as f64, room / rows as f64);
    (cell_w * 0.56).min(cell_h * 0.62).clamp(48.0, 128.0) as i32
}

/// The cell under (x, y) of a w x h grid, and whether the pointer is "over
/// the icon" (the inner part of the cell: folder making).
pub fn cell_at(w: f64, h: f64, cols: usize, rows: usize, x: f64, y: f64) -> (usize, bool) {
    let (w, h) = (w.max(1.0), h.max(1.0)); // (not laid out yet)
    let (cw, ch) = (w / cols as f64, h / rows as f64);
    let c = ((x / cw).max(0.0) as usize).min(cols - 1);
    let r = ((y / ch).max(0.0) as usize).min(rows - 1);
    let (cx, cy) = ((c as f64 + 0.5) * cw, (r as f64 + 0.5) * ch);
    (r * cols + c, (x - cx).abs() < cw * 0.22 && (y - cy).abs() < ch * 0.22)
}

/// The pointer is on the right of its cell: "after this one".
pub fn right_half(w: f64, cols: usize, x: f64) -> bool {
    let cw = w.max(1.0) / cols as f64;
    x.rem_euclid(cw) >= cw / 2.0
}

/// An open folder's grid: (cols, rows) for n apps.
pub fn folder_dims(n: usize, cols: usize) -> (usize, usize) {
    let c = n.max(3).min(cols);
    (c, n.div_ceil(c).clamp(1, 3))
}

// -- items -----------------------------------------------------------------------------

/// What tiles are kept under: an app's id; a folder's apps (Python keeps
/// them by identity; a folder here is a value, and its apps are what make
/// it that folder -- renaming keeps the key, so the kept tile follows).
pub fn key_of(item: &Item) -> String {
    match item {
        Item::App(a) => a.clone(),
        Item::Folder(f) => folder_key(f),
    }
}

pub fn folder_key(f: &Folder) -> String {
    format!("\u{1}folder:{}", f.apps.join("\u{1}"))
}

/// Apps the jiggle-mode x uninstalls (asked first): all but Sonata's own.
pub fn removable(desktop_id: &str) -> bool {
    !(desktop_id.starts_with("io.github.vinioliveiras.sonata2.") || desktop_id.starts_with("sonata2-"))
}

/// A folder locked with Lock Folder (or the Hidden folder).
pub fn is_secure(f: &Folder) -> bool {
    // (only truthy kept keys are read: launchpad_model.parse_item)
    f.kept.get("secure").is_some_and(|v| v != &Value::Bool(false) && !v.is_null())
}

/// The model's pages plus `hidden_folder` after the last item (not stored:
/// it holds the hidden apps, shown only while there are some).
pub fn pages_with_hidden(pages: &[Vec<Item>], hidden_folder: Option<Folder>, per_page: usize) -> Vec<Vec<Item>> {
    let mut out = pages.to_vec();
    let Some(h) = hidden_folder.filter(|h| !h.apps.is_empty()) else { return out };
    match out.last_mut() {
        Some(last) if last.len() < per_page => last.push(Item::Folder(h)),
        _ => out.push(vec![Item::Folder(h)]),
    }
    out
}

/// Where an app held out of its folder goes: beside the app under it (its
/// left half: before it, its right half: after it), past the end: last.
pub fn out_place(page_i: usize, index: usize, page_len: usize, right: bool) -> (usize, usize) {
    (page_i, (index + usize::from(right)).min(page_len))
}

// -- keys ------------------------------------------------------------------------------

#[derive(Debug, PartialEq)]
pub enum Escape {
    ClearSearch,
    CloseFolder,
    StopJiggle,
    AllTabs,
    Close,
}

/// Escape steps back one thing at a time.
pub fn escape(searching: bool, folder_open: bool, jiggling: bool, tab: bool) -> Escape {
    if searching {
        Escape::ClearSearch
    } else if folder_open {
        Escape::CloseFolder
    } else if jiggling {
        Escape::StopJiggle
    } else if tab {
        Escape::AllTabs
    } else {
        Escape::Close
    }
}

#[derive(Debug, PartialEq)]
pub enum Arrow {
    Select(usize),
    /// past the page's edge: the page next to it (+1 / -1), its first or last tile
    Page(i32),
    Nothing,
}

/// An arrow key (delta -1 / +1 / -cols / +cols) with `selected` of `n`
/// tiles; `pages`: left and right turn pages past the edges.
pub fn arrow(selected: Option<usize>, n: usize, delta: i64, horizontal: bool, pages: bool) -> Arrow {
    if n == 0 {
        return Arrow::Nothing;
    }
    let i = match selected {
        Some(s) => s as i64,
        None if delta > 0 => -delta,
        None => n as i64,
    };
    let j = i + delta;
    if pages && horizontal && (j < 0 || j >= n as i64) {
        return Arrow::Page(if j >= n as i64 { 1 } else { -1 });
    }
    Arrow::Select(j.clamp(0, n as i64 - 1) as usize)
}

/// The Dock's dot under Launchpad's icon: told only on a change.
#[derive(Default)]
pub struct DockDot(Option<bool>);

impl DockDot {
    /// Some(on) when the Dock is to be told.
    pub fn set(&mut self, on: bool) -> Option<bool> {
        if self.0.unwrap_or(false) == on {
            return None;
        }
        self.0 = Some(on);
        Some(on)
    }
}

// -- the command line ------------------------------------------------------------------

#[derive(Debug, Default, PartialEq)]
pub struct Args {
    pub preview: bool,
    pub background: bool,
    pub folder: bool,
    pub jiggle: bool,
    pub search: Option<String>,
    pub appearance: Option<&'static str>,
}

pub fn parse(args: &[String]) -> Args {
    let mut a = Args::default();
    let mut it = args.iter();
    while let Some(s) = it.next() {
        match s.as_str() {
            "--preview" => a.preview = true,
            "--background" => a.background = true,
            "--folder" => a.folder = true,
            "--jiggle" => a.jiggle = true,
            "--dark" => a.appearance = Some("dark"),
            "--light" => a.appearance = Some("light"),
            "--search" => a.search = it.next().cloned(),
            _ => {}
        }
    }
    a
}

/// "Cheap: changes when any applications folder (or subfolder) changes"
/// (apps.signature, not in sonata-ui).
pub fn apps_signature() -> Vec<(std::path::PathBuf, i128)> {
    fn walk(d: &std::path::Path, out: &mut Vec<(std::path::PathBuf, i128)>) {
        let Ok(meta) = std::fs::metadata(d) else { return };
        let t = meta.modified().ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map(|t| t.as_nanos() as i128).unwrap_or(0);
        out.push((d.to_path_buf(), t));
        if let Ok(rd) = std::fs::read_dir(d) {
            for e in rd.flatten() {
                if e.file_type().is_ok_and(|t| t.is_dir()) {
                    walk(&e.path(), out);
                }
            }
        }
    }
    let mut out = Vec::new();
    for d in sonata_ui::apps::app_dirs() {
        walk(&d, &mut out);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_categories() {
        let got: Vec<&str> = ["Network;InstantMessaging;", "Graphics;2DGraphics;", "AudioVideo;Audio;", "Utility;Calculator;Office;", "System;FileManager;", ""].iter().map(|c| category_of(c)).collect();
        // (first match in CATEGORIES' order: the calculator's Office before Utility)
        assert_eq!(got, ["social", "creativity", "entertainment", "productivity", "utilities", "other"]);
    }

    #[test]
    fn test_style_default_and_bad_value() {
        let cfg = |v: Value| v.as_object().unwrap().clone();
        assert_eq!(style_of(&cfg(json!({}))), "fullscreen"); // the current Launchpad stays the default
        assert_eq!(style_of(&cfg(json!({"style": "window"}))), "window");
        assert_eq!(style_of(&cfg(json!({"style": "???"}))), "fullscreen");
    }

    /// Vini: it was too big on his 1920 x 1080 screen.
    #[test]
    fn test_smaller_panel() {
        let (w, h) = panel_size(1920, 1080);
        assert!(w as f64 <= 1920.0 * 0.5 && h as f64 <= 1080.0 * 0.6);
        assert_eq!(panel_size(4000, 3000), (940, 680)); // never huge
        assert_eq!(panel_size(800, 600), (560, 440)); // never cramped
    }

    #[test]
    fn rows_fit_the_display() {
        // 16:9 with a 70 px Dock: 5 rows; 16:10: 6 (as launchpad.py's comment says)
        let g = grid_for(1920, 1080, 70, "bottom");
        assert_eq!((g.side, g.cols, g.rows as i64), (230, 7, 5));
        assert_eq!(grid_for(1920, 1200, 70, "bottom").rows as i64, 6);
        // narrow: fewer columns
        assert_eq!(grid_for(800, 1200, 0, "bottom").cols, 4);
        let icon = icon_for(1920, 1080, 70, "bottom", 7, 5);
        assert!((48..=128).contains(&icon), "{icon}");
    }

    #[test]
    fn cells_and_their_centres() {
        assert_eq!(cell_at(700.0, 500.0, 7, 5, 50.0, 50.0), (0, true));
        assert_eq!(cell_at(700.0, 500.0, 7, 5, 99.0, 50.0), (0, false));
        assert_eq!(cell_at(700.0, 500.0, 7, 5, 650.0, 450.0), (34, true));
        assert_eq!(cell_at(700.0, 500.0, 7, 5, 9999.0, -5.0), (6, false)); // clamped
        assert_eq!(cell_at(0.0, 0.0, 7, 5, 0.0, 0.0).0, 0); // not laid out yet
        assert!(!right_half(700.0, 7, 120.0) && right_half(700.0, 7, 160.0));
    }

    /// The out-of-folder mark: beside the app under the pointer.
    #[test]
    fn test_out_of_a_folder_beside_the_app() {
        assert_eq!(out_place(0, 2, 5, false), (0, 2)); // left half: before it
        assert_eq!(out_place(0, 2, 5, true), (0, 3)); // right half: after it
        assert_eq!(out_place(1, 9, 5, false), (1, 5)); // past the end: last
    }

    #[test]
    fn folder_grids() {
        assert_eq!(folder_dims(2, 7), (3, 1));
        assert_eq!(folder_dims(8, 7), (7, 2));
        assert_eq!(folder_dims(40, 7), (7, 3)); // three rows at most
        assert_eq!(menu_page_rows(0, 2, 35, 7, 5), 5);
        assert_eq!(menu_page_rows(1, 2, 8, 7, 5), 2);
        assert_eq!(menu_page_rows(0, 1, 0, 7, 5), 1);
    }

    #[test]
    fn hidden_folder_after_the_last_item() {
        let h = Folder::new("Hidden", vec!["x".into()]);
        let pages = vec![vec![Item::from("a")]];
        let got = pages_with_hidden(&pages, Some(h.clone()), 2);
        assert_eq!(got[0].len(), 2);
        let full = vec![vec![Item::from("a"), Item::from("b")]];
        assert_eq!(pages_with_hidden(&full, Some(h.clone()), 2).len(), 2); // a page of its own
        assert_eq!(pages_with_hidden(&full, Some(Folder::new("Hidden", vec![])), 2), full); // none hidden
    }

    #[test]
    fn test_escape_steps() {
        assert_eq!(escape(true, true, true, true), Escape::ClearSearch);
        assert_eq!(escape(false, true, true, true), Escape::CloseFolder);
        assert_eq!(escape(false, false, true, true), Escape::StopJiggle);
        assert_eq!(escape(false, false, false, true), Escape::AllTabs); // the Apps Menu: its tab first
        assert_eq!(escape(false, false, false, false), Escape::Close);
    }

    #[test]
    fn test_arrow_keys_move_the_selection() {
        assert_eq!(arrow(None, 10, 1, true, true), Arrow::Select(0));
        assert_eq!(arrow(None, 10, -1, true, true), Arrow::Select(9));
        assert_eq!(arrow(Some(2), 10, 7, false, true), Arrow::Select(9));
        assert_eq!(arrow(Some(9), 10, 7, false, true), Arrow::Select(9));
        assert_eq!(arrow(Some(9), 10, 1, true, true), Arrow::Page(1));
        assert_eq!(arrow(Some(0), 10, -1, true, true), Arrow::Page(-1));
        assert_eq!(arrow(Some(9), 10, 1, true, false), Arrow::Select(9)); // the Apps Menu has no pages
        assert_eq!(arrow(None, 0, 1, true, true), Arrow::Nothing);
    }

    #[test]
    fn test_tells_the_dock_once_per_change() {
        let mut dot = DockDot::default();
        let told: Vec<bool> = [true, true, false, false, true].into_iter().filter_map(|on| dot.set(on)).collect();
        assert_eq!(told, [true, false, true]);
        assert_eq!(DockDot::default().set(false), None);
    }

    #[test]
    fn keys_and_protection() {
        let f = Folder::new("Games", vec!["a".into(), "b".into()]);
        let mut renamed = f.clone();
        renamed.name = "Fun".into();
        assert_eq!(folder_key(&f), folder_key(&renamed)); // the kept tile follows a rename
        assert_ne!(key_of(&Item::Folder(f)), key_of(&Item::from("a")));
        assert!(!removable("io.github.vinioliveiras.sonata2.files.desktop") && !removable("sonata2-launchpad.desktop"));
        assert!(removable("firefox.desktop"));
    }

    #[test]
    fn command_line() {
        let a = parse(&["--preview".into(), "--search".into(), "fi".into(), "--dark".into(), "--jiggle".into()]);
        assert_eq!(a, Args { preview: true, search: Some("fi".into()), appearance: Some("dark"), jiggle: true, ..Args::default() });
    }
}
