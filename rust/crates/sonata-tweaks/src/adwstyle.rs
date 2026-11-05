//! Other GTK 4 / libadwaita apps (Bazaar, GNOME apps, Flatpak ones too) in
//! Sonata's window look -- only inside the Sonata session (adwstyle.py).
//!
//! libadwaita takes no theme, but every GTK 4 app reads the user's
//! ~/.config/gtk-4.0/gtk.css. Sonata keeps one line there (between
//! markers): an @import of $XDG_RUNTIME_DIR/sonata2/adw/libadwaita.css.
//! That file is written at each Sonata login and removed when the session
//! ends; the runtime folder is emptied at logout/reboot anyway. In a GNOME
//! or KDE session it doesn't exist, the import finds nothing and their
//! apps look as usual. GTK 3 apps (and Chrome's GTK mode) get the same
//! through ~/.config/gtk-3.0/gtk.css and gtk3.css.
//!
//! What it changes, in windows that aren't Sonata's own (.sonata-window):
//! - the title bar buttons: Sonata's traffic lights (the same pictures
//!   pixdecor draws on other apps' title bars), already on the left
//!   (prefs: button-layout);
//! - the header bar: Sonata's title bar colour (lighter when the window is
//!   inactive), bold title.
//!
//! Flatpak apps see both paths through Flatpak overrides (flatpak_theme.rs).
//! Off with Settings > Appearance > "Sonata title bars for all apps".

use crate::frame::{self, Frame};
use std::io;
use std::path::{Path, PathBuf};

/// gtk.css' markers: the same as gtkstyle's (sonata_system::gtkstyle), so
/// Python and Rust keep one block.
pub const BEGIN: &str = "/* >>> Sonata (written at login by sonata2/gtkstyle.py; edit outside these markers) */";
pub const END: &str = "/* <<< Sonata */";

/// libadwaita centres its header bar's controls this far down
pub const TL_CENTRE_Y_ADW: f64 = 23.0;
/// GTK 3's title buttons keep their 16 px icon's size
pub const GTK3_ICON: f64 = 16.0;
/// ... and are centred this far down a GTK 3 header bar
pub const GTK3_BAR_CENTRE: f64 = 22.0;
/// whose button box starts 1 px out, over its border
pub const GTK3_BOX_INSET: f64 = -1.0;
/// and puts 6 px between them (a property, not CSS)
pub const GTK3_SPACING: f64 = 6.0;
/// the header bar's start box sits this far in (with no padding)
pub const ADW_START_INSET: f64 = 6.0;
/// libadwaita's header bar height: the see-through band of a glass window
pub const ADW_HEADER_H: i64 = 47;
pub const LIGHTS: [&str; 6] = ["close", "close-hover", "minimize", "minimize-hover", "maximize", "maximize-hover"];

pub fn runtime_dir() -> PathBuf {
    crate::util::runtime_dir().join("sonata2").join("adw")
}

pub fn css_path() -> PathBuf {
    runtime_dir().join("libadwaita.css")
}

pub fn css_path3() -> PathBuf {
    runtime_dir().join("gtk3.css")
}

pub fn user_css() -> PathBuf {
    sonata_config::config_home().join("gtk-4.0").join("gtk.css")
}

pub fn user_css3() -> PathBuf {
    sonata_config::config_home().join("gtk-3.0").join("gtk.css")
}

/// GTK 4.20+ (libadwaita 1.8+) reads @media (prefers-color-scheme: dark);
/// older ones skip the block, and a dark app would get the light title
/// bar: then only the buttons and the bold title change.
pub fn media_queries() -> bool {
    (gtk::major_version(), gtk::minor_version()) >= (4, 20)
}

/// A number as Python's `f"{v:g}"` writes it (18.0 -> "18", 3.5 -> "3.5").
fn g(v: f64) -> String {
    if v == v.trunc() { format!("{}", v as i64) } else { format!("{v}") }
}

fn url(folder: &Path, name: &str) -> String {
    format!("url(\"file://{}\")", folder.join(format!("sonata-tl-{name}.svg")).display())
}

/// Black & White buttons are white on dark apps (their own picture set).
pub fn dark_dots(w: &str, folder: &Path, look: &str) -> String {
    if look != "mono" {
        return String::new();
    }
    let rules: String = ["close", "minimize", "maximize"]
        .iter()
        .map(|n| {
            format!(
                "{w}.{n} {{ background-image: {}; }}\n{w}.{n}:hover {{ background-image: {}; }}\n",
                url(folder, &format!("{n}-dark")),
                url(folder, &format!("{n}-hover-dark"))
            )
        })
        .collect();
    format!("@media (prefers-color-scheme: dark) {{\n{rules}}}\n")
}

fn bar(t: &sonata_ui::tokens::Values, glass: bool) -> String {
    let get = |k: &str| t.get(k).cloned().unwrap_or_default();
    let (bg, bg_off) = if glass { (get("titlebar_glass"), get("titlebar_glass_inactive")) } else { (get("titlebar_bg"), get("titlebar_bg_inactive")) };
    let mut out = format!(
        "window:not(.sonata-window) headerbar {{ background-color: {bg}; box-shadow: inset 0 -1px {}; }}\n\
         window:not(.sonata-window):backdrop headerbar {{ background-color: {bg_off}; }}\n",
        get("separator")
    );
    if glass {
        // the app paints its window opaque: only the header's band is let through
        out += &format!(
            "window:not(.sonata-window).csd.background {{ background-color: transparent; \
             background-image: linear-gradient(to bottom, transparent {ADW_HEADER_H}px, {} {ADW_HEADER_H}px); }}\n",
            get("window_bg")
        );
    }
    out
}

/// The stylesheet for frame `f` (pictures from `folder`; bars: the title
/// bar colour; glass: Settings > Appearance > "Glass title bars",
/// experimental here -- the window's background is see-through only
/// behind its header bar, where Wayfire blurs; the rest stays the app's
/// own colour). `look`: the buttons' colours (trafficlights::style).
pub fn css_for(f: &Frame, folder: &Path, bars: bool, glass: bool, look: &str) -> String {
    let (d, gap) = (f.dot as f64, f.dot_gap as f64);
    let w = "window:not(.sonata-window) windowcontrols > button";
    // Sonata's place for the dots (window.rs): 12 px, centres 20 px apart,
    // the first one centred 13 px from the side edge and 14 px from the top.
    // libadwaita's header bar: no padding at its ends, its controls centred
    // in ~46 px. Both sides are written, whichever Window buttons says: an
    // app reads this once, when it starts, and one that stays running
    // (Bazaar keeps a background service) kept the left side's rules after
    // the buttons moved right -- 25 px apart, low, further in.
    let (tl_left, tl_top) = (f.dot_left, f.dot_top);
    let mut sides = String::new();
    for (bx, edge, side) in [("start", "first-child", "left"), ("end", "last-child", "right")] {
        let wc = format!("window:not(.sonata-window) headerbar windowcontrols.{bx}");
        sides += &format!(
            "{wc} {{ border-spacing: 0; padding: 0; margin: 0 0 {}px 0; }}\n{wc} > button:{edge} {{ margin-{side}: {}px; }}\n",
            g((2.0 * (TL_CENTRE_Y_ADW - tl_top)).max(0.0)),
            g(tl_left - d / 2.0 - ADW_START_INSET)
        );
    }
    let di = f.dot;
    let mut out = format!(
        "/* Sonata's window look for other GTK 4 apps -- written by sonata2/adwstyle.py at login */\n\
         window:not(.sonata-window) headerbar {{ padding-left: 0; padding-right: 0; }}\n\
         window:not(.sonata-window).csd {{ border-radius: {}px; }}\n\
         window:not(.sonata-window).csd.maximized, window:not(.sonata-window).csd.fullscreen,\n\
         window:not(.sonata-window).csd.tiled, window:not(.sonata-window).csd.tiled-top,\n\
         window:not(.sonata-window).csd.tiled-left, window:not(.sonata-window).csd.tiled-right,\n\
         window:not(.sonata-window).csd.tiled-bottom {{ border-radius: 0; }}\n",
        f.radius // the corners of Sonata's windows and of the ones Wayfire draws (sonata-corners radius)
    );
    out += &sides;
    out += &format!(
        "{w}, {w}:hover, {w}:active, {w}:backdrop {{\n  min-width: {di}px; min-height: {di}px; padding: 0; margin: 0 {}px; border: none;\n  border-radius: 999px;\n  box-shadow: none; outline: none; background-color: transparent; background-repeat: no-repeat;\n  background-position: center; background-size: {di}px {di}px; }}\n\
         {w} > image {{ opacity: 0; background: none; box-shadow: none; padding: 0; margin: 0;\n  min-width: {di}px; min-height: {di}px; -gtk-icon-size: {di}px; }}\n",
        g(gap / 2.0)
    );
    for n in ["close", "minimize", "maximize"] {
        out += &format!("{w}.{n} {{ background-image: {}; }}\n{w}.{n}:hover {{ background-image: {}; }}\n", url(folder, n), url(folder, &format!("{n}-hover")));
    }
    out += &format!("{w}:active {{ filter: brightness(0.85); }}\nwindow:not(.sonata-window) headerbar .title {{ font-weight: 700; }}\n");
    if bars {
        out += &dark_dots(w, folder, look);
        out += &bar(&sonata_ui::tokens::palette(false), glass);
        out += "@media (prefers-color-scheme: dark) {\n";
        out += &bar(&sonata_ui::tokens::palette(true), glass);
        out += "}\n";
    }
    out
}

/// [`css_for`] with the user's frame and button colours.
pub fn css(folder: Option<&Path>, bars: bool, glass: bool) -> String {
    let folder = folder.map(Path::to_path_buf).unwrap_or_else(runtime_dir);
    css_for(&frame::frame(), &folder, bars, glass, &crate::trafficlights::style())
}

/// GTK 3 apps' title bar buttons -- Chrome / Chromium in GTK mode draw
/// theirs from these rules too: the colours chosen in Settings (Vini: they
/// kept the theme's), and room at the edge they're on (on the right, the
/// close button came out cut by the window's edge).
pub fn css3_for(f: &Frame, folder: &Path) -> String {
    let (d, gap) = (f.dot as f64, f.dot_gap as f64);
    // GTK 3 keeps the 16 px icon inside each button whatever its CSS says:
    // the dots' centres are 16 px + the margins + the box's spacing apart
    // (22 at the closest, Sonata's are 20), and the first one's centre is
    // the header bar's padding, a margin and 8 px in.
    // The room from the edge is the header bar's own padding: Chrome and
    // Electron apps (WhatsApp, VS Code) draw their buttons from it and the
    // buttons' margins, and never see the box inside -- with the padding at
    // 0 their close button touched the edge, cut by the window's corner.
    // A GTK 3 header bar puts its box 1 px further out, given back here.
    // Both sides at once: an app reads this when it starts.
    let margin = ((gap + d - GTK3_ICON - GTK3_SPACING) / 2.0).max(0.0);
    let edge_pad = (f.dot_left - GTK3_ICON / 2.0 - margin).max(0.0);
    let lift = (2.0 * (GTK3_BAR_CENTRE - f.dot_top)).max(0.0);
    let w = "headerbar button.titlebutton";
    let di = f.dot;
    let mut out = vec![
        "/* Sonata's window buttons for GTK 3 apps -- written by sonata2/adwstyle.py at login */".to_string(),
        format!("headerbar {{ padding-left: {p}px; padding-right: {p}px; }}", p = g(edge_pad)),
        format!("headerbar > box.left {{ padding-left: {}px; }}", g(-GTK3_BOX_INSET)),
        format!("headerbar > box.right {{ padding-right: {}px; }}", g(-GTK3_BOX_INSET)),
        format!("{w}.close, {w}.minimize, {w}.maximize {{ min-width: {di}px; min-height: {di}px; padding: 0;"),
        format!("  margin: 0 {}px {}px; border: none; box-shadow: none; background-color: transparent;", g(margin), g(lift)),
        format!("  background-repeat: no-repeat; background-position: center; background-size: {di}px {di}px; }}"),
    ];
    for n in ["close", "minimize", "maximize"] {
        out.push(format!("{w}.{n}, {w}.{n}:backdrop {{ background-image: {}; }}", url(folder, n)));
        out.push(format!("{w}.{n}:hover, {w}.{n}:backdrop:hover {{ background-image: {}; }}", url(folder, &format!("{n}-hover"))));
    }
    out.push(format!("{w}:active {{ opacity: 0.85; }}"));
    // the theme's own glyphs (x, -, a square) were drawn over the dots all
    // the time: the dots' pictures carry theirs, on hover
    out.push(format!("{w} image {{ opacity: 0; }}"));
    out.join("\n") + "\n"
}

pub fn css3(folder: Option<&Path>) -> String {
    let folder = folder.map(Path::to_path_buf).unwrap_or_else(runtime_dir);
    css3_for(&frame::frame(), &folder)
}

const OFF: &str = "/* off: Settings > Appearance */\n";

/// At login (and when the look changes): the pictures and the
/// stylesheets (empty when turned off). glass None: as Settings says.
pub fn write(on: bool, glass: Option<bool>) -> io::Result<PathBuf> {
    write_to(&runtime_dir(), on, glass)
}

/// [`write`] into `folder` (libadwaita.css, gtk3.css and the pictures).
pub fn write_to(folder: &Path, on: bool, glass: Option<bool>) -> io::Result<PathBuf> {
    let folder = folder.to_path_buf();
    std::fs::create_dir_all(&folder)?;
    if on {
        // Settings' button colours
        for (dark, suffix) in [(false, ""), (true, "-dark")] {
            let src = crate::trafficlights::folder(dark);
            for n in LIGHTS {
                std::fs::copy(src.join(format!("sonata-tl-{n}.svg")), folder.join(format!("sonata-tl-{n}{suffix}.svg")))?;
            }
        }
    }
    let glass = glass.unwrap_or_else(crate::titlebars::glass_bars);
    let text = if on { css(Some(&folder), media_queries(), glass) } else { OFF.to_string() };
    sonata_config::atomic_write(&folder.join("libadwaita.css"), text.as_bytes(), false)?;
    let text3 = if on { css3(Some(&folder)) } else { OFF.to_string() };
    sonata_config::atomic_write(&folder.join("gtk3.css"), text3.as_bytes(), false)?;
    Ok(folder.join("libadwaita.css"))
}

/// Sonata's one line at the top of a gtk.css (an @import must come first);
/// anything else in the file is kept as it is.
pub fn link(path: &Path, target: &Path) -> io::Result<()> {
    let block = format!("{BEGIN}\n@import url(\"file://{}\");\n{END}\n", target.display());
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let rest = match (text.find(BEGIN), text.find(END)) {
        (Some(b), Some(e)) if e >= b => format!("{}{}", &text[..b], text[e + END.len()..].trim_start_matches('\n')),
        _ => text.clone(),
    };
    let new = block + &rest;
    if new != text {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        sonata_config::atomic_write(path, new.as_bytes(), false)?;
    }
    Ok(())
}

/// Both stylesheets written for `on` and linked from GTK 4's and GTK 3's
/// gtk.css (titlebars::apply: at every Sonata login and from Settings).
pub fn apply(on: bool) -> io::Result<()> {
    write(on, None)?;
    link(&user_css(), &css_path())?;
    link(&user_css3(), &css_path3()) // GTK 3 apps, Chrome's GTK mode
}

/// At every Sonata login.
pub fn install() -> io::Result<()> {
    apply(crate::titlebars::enabled())
}

/// The session ends: other desktops' apps never see it.
pub fn stop() {
    for p in [css_path(), css_path3()] {
        let _ = std::fs::remove_file(p);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Map};


    fn frame_of(v: serde_json::Value) -> Frame {
        frame::frame_from(v.as_object().unwrap())
    }

    #[test]
    fn test_rules() {
        let css = css3_for(&frame_of(json!({"buttons_side": "right"})), Path::new("/x"));
        assert!(css.contains("headerbar > box.right { padding-right:"));
        assert!(css.contains("url(\"file:///x/sonata-tl-close.svg\")"));
        assert!(css.contains("button.titlebutton.close:hover"));
        assert!(css3_for(&frame_of(json!({})), Path::new("/x")).contains("headerbar > box.left { padding-left:"));
    }

    #[test]
    fn test_linked_in_gtk3() {
        let d = tempfile::tempdir().unwrap();
        let path = d.path().join("gtk-3.0").join("gtk.css");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, "window { color: red; }\n").unwrap();
        link(&path, Path::new("/run/x/gtk3.css")).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.starts_with(BEGIN));
        assert!(text.contains("@import url(\"file:///run/x/gtk3.css\");"));
        assert!(text.contains("window { color: red; }"));
        link(&path, Path::new("/run/x/gtk3.css")).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap().matches("@import").count(), 1);
    }

    /// Bazaar, left running while the buttons moved right: 16 px from the
    /// edge, 23 px down and 23 px apart (the left side's rules only).
    #[test]
    fn test_libadwaita_buttons_right_with_the_left_sides_stylesheet() {
        let css = css_for(&frame_of(json!({"buttons_side": "left"})), Path::new("/x"), false, false, "color");
        for (bx, edge, side) in [("start", "first-child", "left"), ("end", "last-child", "right")] {
            assert!(css.contains(&format!("headerbar windowcontrols.{bx} {{ border-spacing: 0;")), "{bx}");
            assert!(css.contains(&format!("windowcontrols.{bx} > button:{edge} {{ margin-{side}:")), "{bx}");
        }
        assert!(css.contains("headerbar { padding-left: 0; padding-right: 0; }"));
    }

    /// A rounder window corner moves the buttons in: GTK 4 apps' too.
    #[test]
    fn test_window_buttons_crowded_a_rounder_corner() {
        let f = frame_of(json!({"radius": {"window": 24}}));
        let css = css_for(&f, Path::new("/x"), false, false, "color");
        assert!(css.contains(&format!("margin-left: {}px", g(f.dot_left - frame::DOT as f64 / 2.0 - ADW_START_INSET))));
        assert!(css.contains("border-radius: 24px"));
    }

    /// Vini: the window buttons' size in Appearance reaches GTK 4 and GTK 3 apps.
    #[test]
    fn test_button_size_keeps_the_room_from_the_edges() {
        let f = frame_of(json!({"button_size": 16}));
        assert!(css_for(&f, Path::new("/x"), false, false, "color").contains("min-width: 16px"));
        assert!(css3_for(&f, Path::new("/x")).contains("min-width: 16px"));
    }

    /// Settings > Appearance > Glass title bars: GNOME apps' header band too.
    #[test]
    fn test_glass_title_bars_toggle() {
        let f = frame_of(json!({}));
        let css = css_for(&f, Path::new("/x"), true, true, "color");
        assert!(css.contains(&sonata_ui::tokens::palette(true)["titlebar_glass"]));
        assert!(css.contains(&format!("transparent {ADW_HEADER_H}px"))); // only the header's band
        let plain = css_for(&f, Path::new("/x"), true, false, "color");
        assert!(!plain.contains("background-color: transparent; background-image"));
    }

    #[test]
    fn mono_buttons_are_white_on_dark_apps() {
        let f = frame::frame_from(&Map::new());
        assert!(css_for(&f, Path::new("/x"), true, false, "mono").contains("sonata-tl-close-hover-dark.svg"));
        assert!(!css_for(&f, Path::new("/x"), true, false, "color").contains("-dark.svg"));
    }

    /// Off: both files say so (an app reading them gets nothing of Sonata's).
    #[test]
    fn written_off() {
        let run = tempfile::tempdir().unwrap();
        let p = write_to(run.path(), false, Some(false)).unwrap();
        assert_eq!(std::fs::read_to_string(p).unwrap(), OFF);
        assert_eq!(std::fs::read_to_string(run.path().join("gtk3.css")).unwrap(), OFF);
    }

    /// The paths Python's adwstyle uses: the session's runtime folder and
    /// the user's GTK 4 / GTK 3 stylesheets.
    #[test]
    fn same_paths_as_python() {
        assert!(css_path().ends_with("sonata2/adw/libadwaita.css"));
        assert!(css_path3().ends_with("sonata2/adw/gtk3.css"));
        assert!(user_css().ends_with("gtk-4.0/gtk.css") && user_css3().ends_with("gtk-3.0/gtk.css"));
    }
}
