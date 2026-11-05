use super::*;

/// A w x h RGBA picture: `paint(x, y)` gives each pixel.
fn picture(w: usize, h: usize, paint: impl Fn(usize, usize) -> [u8; 4]) -> Pixels {
    let mut data = Vec::with_capacity(w * h * 4);
    for y in 0..h {
        for x in 0..w {
            data.extend_from_slice(&paint(x, y));
        }
    }
    Pixels { w, h, n: 4, stride: w * 4, data }
}

const ORANGE: [u8; 4] = [0xd9, 0x77, 0x57, 255];
const CLEAR: [u8; 4] = [0, 0, 0, 0];

/// A rounded tile filling `inset`..size-`inset`, its corners cut by `r`.
fn tile(size: usize, inset: usize, r: f64, colour: [u8; 4], logo: Option<[u8; 4]>) -> Pixels {
    picture(size, size, |x, y| {
        let (lo, hi) = (inset as f64, (size - inset) as f64 - 1.0);
        let (fx, fy) = (x as f64, y as f64);
        if fx < lo || fx > hi || fy < lo || fy > hi {
            return CLEAR;
        }
        let cx = fx.clamp(lo + r, hi - r);
        let cy = fy.clamp(lo + r, hi - r);
        if (fx - cx).hypot(fy - cy) > r {
            return CLEAR;
        }
        let mid = size as f64 / 2.0;
        match logo {
            Some(l) if (fx - mid).abs() < size as f64 * 0.15 && (fy - mid).abs() < size as f64 * 0.15 => l,
            _ => colour,
        }
    })
}

#[test]
fn a_coloured_tile_is_one_tone_and_fills_the_frame() {
    let px = tile(64, 4, 12.0, ORANGE, Some([255, 255, 255, 255]));
    assert_eq!(edge_tone(&px, false).as_deref(), Some("#d97757"));
    let (kind, colour) = shape_of(&px).unwrap();
    assert_eq!((kind.as_str(), colour.as_str()), ("tile", "#d97757"));
    let (x, y, w, h) = tile_box(&px);
    assert!((x - 4.0 / 64.0).abs() < 1e-9 && (y - 4.0 / 64.0).abs() < 1e-9);
    assert!((w - 56.0 / 64.0).abs() < 1e-9 && (h - 56.0 / 64.0).abs() < 1e-9);
}

#[test]
fn a_white_tile_needs_asking() {
    let px = tile(64, 4, 12.0, [250, 250, 250, 255], None);
    assert_eq!(edge_tone(&px, false), None); // white already: the usual plate
    assert_eq!(edge_tone(&px, true).as_deref(), Some("#fafafa"));
}

#[test]
fn a_logo_on_transparency_stays_on_the_plate() {
    let px = picture(64, 64, |x, y| if (20..44).contains(&x) && (20..44).contains(&y) { ORANGE } else { CLEAR });
    assert_eq!(edge_tone(&px, false), None);
    assert_eq!(shape_of(&px), None);
}

#[test]
fn a_disc_and_a_square() {
    let disc = picture(64, 64, |x, y| if (x as f64 - 31.5).hypot(y as f64 - 31.5) <= 30.0 { [20, 120, 220, 255] } else { CLEAR });
    assert_eq!(shape_of(&disc), Some(("circle".into(), "#1478dc".into())));
    let square = picture(32, 32, |_, _| [10, 20, 30, 255]);
    assert_eq!(shape_of(&square), Some(("square".into(), "#0a141e".into())));
}

#[test]
fn the_logo_keyed_out_of_its_tile() {
    let px = tile(64, 4, 12.0, ORANGE, Some([255, 255, 255, 255]));
    let (pixels, (x, y, w, h)) = key_out(&px, "#d97757").unwrap();
    // the logo: the middle 30 %
    assert!((x - 23.0 / 64.0).abs() < 0.02 && (y - 23.0 / 64.0).abs() < 0.02, "{x} {y}");
    assert!((w - 19.0 / 64.0).abs() < 0.03 && (h - 19.0 / 64.0).abs() < 0.03, "{w} {h}");
    assert_eq!(pixels[(10 * 64 + 10) * 4 + 3], 0); // the tile's colour: gone
    assert_eq!(pixels[(32 * 64 + 32) * 4 + 3], 255); // the logo: kept
    assert_eq!(content_box(&px, "#d97757").map(|b| (b.0 * 64.0).round() as i32), Some(23));
}

#[test]
fn median_colours_ignore_see_through_pixels() {
    assert_eq!(median_colour(&[[1, 2, 3, 255], [1, 2, 3, 255], [200, 200, 200, 10]]), "#010203");
    assert_eq!(median_colour(&[]), "#ffffff");
}

#[test]
fn icon_names_tried_for_an_app() {
    let names = candidates_from(&["spotify-client".into()], None, "com.spotify.Client", "Spotify", "/opt/spotify/spotify %U", "Spotify");
    assert_eq!(names, ["spotify-client", "com.spotify.Client", "com.spotify.client", "Spotify", "spotify", "spotify", "spotify", "Spotify", "spotify"]);
    // an absolute Icon= file: its name, and without "-linux-512"
    let names = candidates_from(&[], Some("spotify-linux-512.png"), "x", "", "", "");
    assert_eq!(&names[..2], ["spotify-linux-512", "spotify"]);
    // a game's shortcut: never the launcher's icon
    let names = candidates_from(&[], None, "game", "", "steam steam://rungameid/1", "My Game");
    assert!(!names.contains(&"steam".to_string()));
    assert!(names.contains(&"My-Game".to_string()));
}

#[test]
fn an_apps_own_choices() {
    let prefs: Map<String, Value> = serde_json::from_value(json!({"shape": "circle", "source": "auto", "apps": {
        "firefox": {"source": "theme", "name": "web-browser", "scale": 2.0},
        "code": {"source": "nonsense", "shape": "rounded"}}})).unwrap();
    let ff = pref_of(&prefs, "firefox");
    assert_eq!((ff.source.as_str(), ff.name.as_str(), ff.scale, ff.shape.as_str()), ("theme", "web-browser", Some(1.0), "circle"));
    let code = pref_of(&prefs, "code");
    assert_eq!((code.source.as_str(), code.shape.as_str(), code.scale), ("auto", "rounded", None));
    assert_eq!(clamp_scale(0.123), 0.3);
    assert_eq!(default_scale(), 0.70);
}

#[test]
fn squircle_points_stay_in_their_box() {
    let pts = squircle_points(10.0, 10.0, 100.0, 100.0, 4.0, 96);
    assert_eq!(pts.len(), 96);
    assert!(pts.iter().all(|(x, y)| (10.0..=110.0).contains(x) && (10.0..=110.0).contains(y)));
    assert!((pts[0].0 - 110.0).abs() < 1e-9 && (pts[0].1 - 60.0).abs() < 1e-9);
}

#[test]
fn generated_icons_are_named_as_the_python_ones() {
    let (name, stamp) = generated_key("firefox", "/usr/share/icons/hicolor/256x256/apps/firefox.png", 1700000000, "squircle", false, None);
    assert_eq!(stamp, "/usr/share/icons/hicolor/256x256/apps/firefox.png\n1700000000\n11\nsquircle\nFalse\nNone");
    let (n2, s2) = generated_key("firefox", "/x.png", 1, "circle", true, Some(0.75));
    assert!(s2.ends_with("\ncircle\nTrue\n0.75"));
    // python3: hashlib.sha1("firefox|squircle|False|None".encode()).hexdigest()[:20]
    let py = std::process::Command::new("python3")
        .args(["-c", "import hashlib;print(hashlib.sha1(b'firefox|squircle|False|None').hexdigest()[:20]);print(hashlib.sha1(b'firefox|circle|True|0.75').hexdigest()[:20])"])
        .output();
    if let Some(o) = py.ok().filter(|o| o.status.success()) {
        assert_eq!(String::from_utf8_lossy(&o.stdout), format!("{name}\n{n2}\n"));
    }
}

#[test]
fn the_bundled_themes_are_found() {
    assert!(icons_dir().join("Sonata").join("index.theme").exists(), "{}", icons_dir().display());
}

/// Vini, in the Rust session: Files, Terminal, Spotify and GitHub Desktop
/// were invisible in the Dock. Their artwork is a symlink in the theme
/// (system-file-manager.svg -> file-manager.svg), and a clone with
/// core.symlinks=false had them as text files naming the target. Every
/// icon the themes ship is a picture GTK can read.
#[test]
fn test_dock_icons_were_invisible() {
    let link = icons_dir().join("Sonata-MacTahoe/apps/scalable/system-file-manager.svg");
    let text = std::fs::read_to_string(&link).unwrap_or_default();
    assert!(text.contains("<svg"), "{} is not a picture: {:?}", link.display(), &text[..text.len().min(40)]);
}
