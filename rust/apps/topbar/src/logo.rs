//! The menu bar's logo (where macOS has the Apple), sonata2/ui/logo.py:
//! Settings > Appearance > "Menu bar logo" (appearance.json "menu_logo").
//!
//! ```text
//! "distro"          the distribution's logo (os-release LOGO=) as a
//!                   one-colour silhouette in the menu bar's text colour
//! "distro-colour"   the same logo in its own colours
//! "sonata"          Sonata's logo
//! "shape:<name>"    a geometric shape (SHAPES)
//! "icon:<name>"     a ready-made symbol (ICONS)
//! "text:sonata"     the word Sonata
//! "text:user"       your name (first name of the account's full name)
//! "text:custom"     your own text (appearance.json "menu_text"); emoji
//!                   welcome, drawn in one colour like the apps' tray icons
//! ```
//!
//! [`LogoGlyph`] draws any of them at the menu bar's icon size and follows
//! the setting live. The Rust bar showed the distro's logo in its own
//! colours whatever was chosen: the default -- a silhouette in the bar's
//! text colour -- and every other choice of Settings went unseen.

use gtk::{gdk, glib, graphene, gsk, pango, prelude::*, subclass::prelude::*};
use serde_json::json;
use std::cell::{Cell, RefCell};

pub const DEFAULT: &str = "distro";
/// An emoji's font size per pixel of logo height (its glyph is ~1.3 em tall).
pub const EMOJI_EM: f64 = 0.85;
/// Characters of custom text.
pub const TEXT_MAX: usize = 24;

/// The menu_logo / menu_text pair as saved (missing or empty: the default).
pub fn current() -> (String, String) {
    let cfg = sonata_config::load("appearance", &json!({"menu_logo": DEFAULT, "menu_text": ""}));
    let kind = cfg.get("menu_logo").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).unwrap_or(DEFAULT).to_string();
    let text = cfg.get("menu_text").and_then(|v| v.as_str()).unwrap_or("").to_string();
    (kind, text)
}

fn is_emoji(ch: char) -> bool {
    let o = ch as u32;
    o >= 0x1F000 || (0x2600..=0x27BF).contains(&o) || (0x2B00..=0x2BFF).contains(&o) || o == 0x3297 || o == 0x3299
        // presentation selector, joiner, keycap: part of one
        || o == 0xFE0F || o == 0x200D || o == 0x20E3
}

/// [(start, end)] UTF-8 byte ranges of the emoji in `text` (Pango attributes).
pub fn emoji_runs(text: &str) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let mut start = None;
    for (pos, ch) in text.char_indices() {
        if is_emoji(ch) {
            start.get_or_insert(pos);
        } else if let Some(s) = start.take() {
            runs.push((s, pos));
        }
    }
    if let Some(s) = start {
        runs.push((s, text.len()));
    }
    runs
}

/// The words a text: logo shows ("" for other kinds); `real` and `user`:
/// the account's full name and login.
pub fn text_with(kind: &str, custom: &str, real: &str, user: &str) -> String {
    match kind {
        "text:sonata" => "Sonata".into(),
        "text:user" => {
            if real.is_empty() || real == "Unknown" {
                user.to_string()
            } else {
                real.split(' ').next().unwrap_or("").to_string()
            }
        }
        "text:custom" => {
            let words: String = custom.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(TEXT_MAX).collect();
            if words.is_empty() { "Sonata".into() } else { words }
        }
        _ => String::new(),
    }
}

pub fn text_for(kind: &str, custom: &str) -> String {
    text_with(kind, custom, &glib::real_name().to_string_lossy(), &glib::user_name().to_string_lossy())
}

/// A text logo in a single colour (white or black, the menu bar's text):
/// each character on its own, its main tone solid and the parts in a
/// contrasting tone cut out -- a smiley's face stays, its eyes and mouth
/// are holes; a dark game controller stays, its light buttons are holes.
/// Plain letters (one tone) are just solid. Only edges keep soft alpha.
/// `px`: RGBA rows of `stride` bytes; `bands`: each character's columns.
pub fn one_colour(px: &[u8], stride: usize, w: usize, h: usize, bands: &[(usize, usize)]) -> Vec<u8> {
    let mut out = vec![0u8; px.len()];
    let lum = |i: usize| (0.2126 * px[i] as f64 + 0.7152 * px[i + 1] as f64 + 0.0722 * px[i + 2] as f64) / 255.0;
    for &(x0, x1) in bands {
        let x1 = x1.min(w);
        let mut lums = Vec::new();
        for y in 0..h {
            for x in x0..x1 {
                let i = y * stride + x * 4;
                if px[i + 3] > 128 {
                    lums.push(lum(i));
                }
            }
        }
        let (mut two_tone, mut lo, mut hi, mut mid) = (false, 0.0, 0.0, 0.0);
        if lums.len() >= 8 {
            lums.sort_by(|a, b| a.total_cmp(b));
            let n = lums.len();
            (lo, hi, mid) = (lums[n / 10], lums[n * 9 / 10], lums[n / 2]);
            two_tone = hi - lo >= 0.3;
        }
        for y in 0..h {
            for x in x0..x1 {
                let i = y * stride + x * 4;
                let mut a = px[i + 3] as i64;
                if two_tone && a != 0 {
                    let t = (lum(i) - mid).abs() / (hi - lo);
                    a = (a as f64 * ((0.55 - t) / 0.2).clamp(0.0, 1.0)) as i64; // far from the main tone: a hole
                }
                let a = ((a - 48) * 255).div_euclid(160).clamp(0, 255) as u8; // no see-through greys
                out[i + 3] = out[i + 3].max(a);
            }
        }
    }
    out
}

/// Logos drawn on a solid tile (Ubuntu's orange square): the silhouette
/// must be the glyph, not the tile -- pixels of the tile's colour become
/// transparent. None when the logo has no such tile.
fn glyph_of_tile(path: &std::path::Path, px: i32) -> Option<gdk::Texture> {
    let p = sonata_ui::icons::pixels_at(path, px)?;
    let tone = sonata_ui::icons::edge_tone(&p, false)?;
    let [tr, tg, tb] = sonata_ui::icons::hex(&tone);
    let mut data = Vec::with_capacity(p.w * p.h * 4);
    for y in 0..p.h {
        for x in 0..p.w {
            let [r, g, b, a] = p.at(x, y);
            let tile = (r as i32 - tr).abs() + (g as i32 - tg).abs() + (b as i32 - tb).abs() < 90;
            data.extend_from_slice(&[r, g, b, if tile { 0 } else { a }]);
        }
    }
    Some(gdk::MemoryTexture::new(p.w as i32, p.h as i32, gdk::MemoryFormat::R8g8b8a8, &glib::Bytes::from_owned(data), p.w * 4).upcast())
}

/// A geometric shape's outline at (x, y), `s` square, on a cairo context.
fn shape_path(cr: &gtk::cairo::Context, name: &str, x: f64, y: f64, s: f64) {
    let (cx, cy, r) = (x + s / 2.0, y + s / 2.0, s / 2.0);
    match name {
        "circle" | "ring" => {
            let rr = if name == "circle" { r } else { r * 0.92 };
            cr.arc(cx, cy, rr, 0.0, std::f64::consts::TAU);
        }
        "square" => {
            let (x0, y0, side, rad) = (x + s * 0.06, y + s * 0.06, s * 0.88, s * 0.22);
            cr.new_sub_path();
            cr.arc(x0 + side - rad, y0 + rad, rad, -std::f64::consts::FRAC_PI_2, 0.0);
            cr.arc(x0 + side - rad, y0 + side - rad, rad, 0.0, std::f64::consts::FRAC_PI_2);
            cr.arc(x0 + rad, y0 + side - rad, rad, std::f64::consts::FRAC_PI_2, std::f64::consts::PI);
            cr.arc(x0 + rad, y0 + rad, rad, std::f64::consts::PI, 3.0 * std::f64::consts::FRAC_PI_2);
            cr.close_path();
        }
        _ => {
            let pts: Vec<(f64, f64)> = match name {
                "triangle" => vec![(90.0, 1.0), (210.0, 1.0), (330.0, 1.0)],
                "diamond" => vec![(90.0, 1.0), (180.0, 0.8), (270.0, 1.0), (0.0, 0.8)],
                "hexagon" => (0..6).map(|i| (90.0 + 60.0 * i as f64, 1.0)).collect(),
                "star" => (0..10).map(|i| (90.0 + 36.0 * i as f64, if i % 2 == 0 { 1.0 } else { 0.45 })).collect(),
                _ => return,
            };
            for (i, (deg, k)) in pts.iter().enumerate() {
                let a = deg.to_radians();
                let (px, py) = (cx + r * k * a.cos(), cy - r * k * a.sin());
                if i == 0 {
                    cr.move_to(px, py);
                } else {
                    cr.line_to(px, py);
                }
            }
            cr.close_path();
        }
    }
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct LogoGlyph {
        pub size: Cell<i32>,
        pub kind: RefCell<String>,
        pub text: RefCell<String>,
        pub picture: RefCell<Option<gdk::Paintable>>,
        /// a text logo's own size (logical px)
        pub text_size: Cell<(i32, i32)>,
        pub monitors: RefCell<Vec<gtk::gio::FileMonitor>>,
        /// pictures made (tests: a redraw alone makes none)
        pub loads: Cell<u32>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for LogoGlyph {
        const NAME: &'static str = "SonataTopbarLogoGlyph";
        type Type = super::LogoGlyph;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for LogoGlyph {
        fn dispose(&self) {
            for m in self.monitors.take() {
                m.cancel();
            }
        }
    }

    impl WidgetImpl for LogoGlyph {
        fn measure(&self, orientation: gtk::Orientation, _for_size: i32) -> (i32, i32, i32, i32) {
            let s = self.size.get();
            if !self.text.borrow().is_empty() && self.picture.borrow().is_some() {
                // words at their own size, like the menus' titles
                let (tw, th) = self.text_size.get();
                let n = if orientation == gtk::Orientation::Horizontal { tw } else { s.max(th) };
                return (n, n, -1, -1);
            }
            (s, s, -1, -1)
        }

        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let s = self.size.get() as f32;
            let (w, h) = (obj.width() as f32, obj.height() as f32);
            let (x, y) = ((w - s) / 2.0, (h - s) / 2.0);
            let color = obj.color();
            let kind = self.kind.borrow().clone();
            if let Some(name) = kind.strip_prefix("shape:") {
                let cr = snap.append_cairo(&graphene::Rect::new(0.0, 0.0, w.max(1.0), h.max(1.0)));
                shape_path(&cr, name, x as f64, y as f64, s as f64);
                cr.set_source_rgba(color.red() as f64, color.green() as f64, color.blue() as f64, color.alpha() as f64);
                if name == "ring" {
                    cr.set_line_width(s as f64 * 0.16);
                    let _ = cr.stroke();
                } else {
                    let _ = cr.fill();
                }
                return;
            }
            let Some(pic) = self.picture.borrow().clone() else { return };
            let mask = |snap: &gtk::Snapshot, tex: &gdk::Texture, rect: &graphene::Rect| {
                snap.push_mask(gsk::MaskMode::Alpha);
                snap.append_texture(tex, rect);
                snap.pop();
                snap.append_color(&color, rect);
                snap.pop();
            };
            if !self.text.borrow().is_empty() {
                // words: one colour, the slot's height, 1:1
                let (tw, th) = self.text_size.get();
                let rect = graphene::Rect::new(((w - tw as f32) / 2.0).round(), ((h - th as f32) / 2.0).round(), tw as f32, th as f32);
                if let Ok(tex) = pic.downcast::<gdk::Texture>() {
                    mask(snap, &tex, &rect);
                }
                return;
            }
            if let Some(icon) = pic.downcast_ref::<gtk::IconPaintable>() {
                // symbolic icons take the text colour
                snap.save();
                snap.translate(&graphene::Point::new(x, y));
                icon.snapshot_symbolic(snap.upcast_ref::<gdk::Snapshot>(), s as f64, s as f64, &[color]);
                snap.restore();
                return;
            }
            let Ok(tex) = pic.downcast::<gdk::Texture>() else { return };
            let (tw, th) = (tex.width() as f32, tex.height() as f32);
            let k = (s / tw).min(s / th);
            let rect = graphene::Rect::new(x + (s - tw * k) / 2.0, y + (s - th * k) / 2.0, tw * k, th * k);
            if kind == "distro-colour" {
                snap.append_texture(&tex, &rect);
            } else {
                mask(snap, &tex, &rect); // one-colour silhouette: the logo's shape, filled with the text colour
            }
        }
    }
}

glib::wrapper! {
    /// The logo, `size` px, following appearance.json live.
    pub struct LogoGlyph(ObjectSubclass<imp::LogoGlyph>) @extends gtk::Widget;
}

impl LogoGlyph {
    pub fn new(size: i32) -> Self {
        let g: Self = glib::Object::builder().property("valign", gtk::Align::Center).property("halign", gtk::Align::Center).build();
        g.imp().size.set(size);
        let (kind, custom) = current();
        g.set_kind(&kind, &custom);
        let w = g.downgrade();
        g.imp().monitors.replace(sonata_config::watch("appearance", move || {
            if let Some(g) = w.upgrade() {
                let (kind, custom) = current();
                g.set_kind(&kind, &custom);
            }
        }));
        // a new display scale: sharp again
        g.connect_scale_factor_notify(|g| g.refresh(true));
        g
    }

    pub fn kind(&self) -> String {
        self.imp().kind.borrow().clone()
    }

    pub fn text(&self) -> String {
        self.imp().text.borrow().clone()
    }

    pub fn text_size(&self) -> (i32, i32) {
        self.imp().text_size.get()
    }

    pub fn has_picture(&self) -> bool {
        self.imp().picture.borrow().is_some()
    }

    /// Pictures made so far (tests).
    pub fn loads(&self) -> u32 {
        self.imp().loads.get()
    }

    /// Drawn anew (Vini: the menu bar's logo went blank, like the Control
    /// Center icon did -- a stale picture kept by GTK). reload=false only
    /// redraws it (every focus change); a reload makes the picture again
    /// (appearance, scale, its menu closed).
    pub fn refresh(&self, reload: bool) {
        if !reload {
            self.queue_draw();
            return;
        }
        let (kind, custom) = current();
        if kind != self.kind() || text_for(&kind, &custom) != self.text() {
            self.set_kind(&kind, &custom); // another logo: measured again
            return;
        }
        // the same logo: only its picture made again and drawn -- no new
        // layout (a relayout as a panel closes crashed GTK in
        // gdk_surface_request_motion)
        self.make_picture();
        self.queue_draw();
    }

    pub fn set_kind(&self, kind: &str, custom: &str) {
        let text = text_for(kind, custom);
        if *self.imp().kind.borrow() == kind && *self.imp().text.borrow() == text {
            return;
        }
        self.imp().kind.replace(kind.to_string());
        self.imp().text.replace(text);
        self.make_picture();
        self.queue_resize();
        self.queue_draw();
    }

    fn make_picture(&self) {
        let kind = self.kind();
        let text = self.text();
        let pic = if kind == "distro" || kind == "distro-colour" || kind == "sonata" || kind.starts_with("icon:") {
            self.load(&kind)
        } else if !text.is_empty() {
            self.text_texture(&text).map(|t| t.upcast())
        } else {
            None
        };
        self.imp().loads.set(self.imp().loads.get() + 1);
        self.imp().picture.replace(pic);
    }

    fn load(&self, kind: &str) -> Option<gdk::Paintable> {
        let scale = self.scale_factor().max(1);
        let size = self.imp().size.get();
        let px = size * scale * 2;
        let mut kind = kind.to_string();
        if kind.starts_with("distro") {
            if let Some(path) = sonata_ui::icons::distro_logo(px) {
                let tex: Option<gdk::Texture> = if path.extension().is_some_and(|e| e == "svg") {
                    gtk::gdk_pixbuf::Pixbuf::from_file_at_scale(&path, px, px, true).ok().map(|pb| {
                        #[allow(deprecated)]
                        gdk::Texture::for_pixbuf(&pb)
                    })
                } else {
                    gdk::Texture::from_filename(&path).ok()
                };
                if let Some(tex) = tex {
                    let tex = if kind == "distro" { glyph_of_tile(&path, px).unwrap_or(tex) } else { tex };
                    return Some(tex.upcast());
                }
            }
            kind = "sonata".into(); // no distro logo: Sonata's
        }
        let name = if kind == "sonata" { "sonata-logo-symbolic".to_string() } else { kind.split_once(':').map(|(_, n)| n.to_string()).unwrap_or_default() };
        let display = gdk::Display::default()?;
        let theme = gtk::IconTheme::for_display(&display);
        Some(theme.lookup_icon(&name, &[], size, scale, gtk::TextDirection::None, gtk::IconLookupFlags::empty()).upcast())
    }

    /// The words in the menu bar's bold font, one colour (emoji too).
    fn text_texture(&self, text: &str) -> Option<gdk::Texture> {
        let scale = (self.scale_factor().max(1) * 2) as f64;
        let layout = self.create_pango_layout(Some(text));
        let vals = sonata_ui::theme::values();
        let fonts = vals.get("font").cloned().unwrap_or_default();
        let family: Vec<String> = fonts.split(',').map(|f| f.trim().trim_matches('"').to_string()).filter(|f| !f.is_empty()).collect();
        let mut desc = pango::FontDescription::from_string(&format!("{} Bold", family.join(",")));
        let body: i32 = vals.get("text_body").and_then(|v| v.trim_end_matches("px").trim().parse().ok()).unwrap_or(13);
        desc.set_absolute_size((body * pango::SCALE) as f64);
        layout.set_font_description(Some(&desc));
        // emoji are drawn taller than letters (a 13 px font's emoji is ~17
        // px): theirs is set so they come out the logo's own size
        let attrs = pango::AttrList::new();
        for (start, end) in emoji_runs(text) {
            let mut a = pango::AttrSize::new_size_absolute((self.imp().size.get() as f64 * EMOJI_EM * pango::SCALE as f64) as i32);
            a.set_start_index(start as u32);
            a.set_end_index(end as u32);
            attrs.insert(a);
        }
        layout.set_attributes(Some(&attrs));
        let (_ink, logical) = layout.pixel_extents();
        let (w, h) = (((logical.width() as f64 * scale) as i32).max(1), ((logical.height() as f64 * scale) as i32).max(1));
        let mut surf = gtk::cairo::ImageSurface::create(gtk::cairo::Format::ARgb32, w, h).ok()?;
        {
            let cr = gtk::cairo::Context::new(&surf).ok()?;
            cr.scale(scale, scale);
            cr.set_source_rgb(0.0, 0.0, 0.0);
            pangocairo::functions::show_layout(&cr, &layout);
        }
        surf.flush();
        let stride = surf.stride() as usize;
        let data = surf.data().ok()?.to_vec();
        let mut rgba = vec![0u8; data.len()];
        for i in (0..data.len()).step_by(4) {
            // premultiplied BGRA -> RGBA
            let (b, g, r, a) = (data[i] as u32, data[i + 1] as u32, data[i + 2] as u32, data[i + 3] as u32);
            let un = |c: u32| if a > 0 { (c * 255 / a).min(255) as u8 } else { c as u8 };
            rgba[i..i + 4].copy_from_slice(&[un(r), un(g), un(b), a as u8]);
        }
        // each character's columns (an emoji: its own tones)
        let mut bands = Vec::new();
        let mut it = layout.iter();
        loop {
            let (_i, rect) = it.cluster_extents();
            let x0 = (rect.x() as f64 / pango::SCALE as f64 * scale) as i64;
            let x1 = x0 + (rect.width() as f64 / pango::SCALE as f64 * scale) as i64 + 1;
            bands.push((x0.max(0) as usize, x1.min(w as i64).max(0) as usize));
            if !it.next_cluster() {
                break;
            }
        }
        self.imp().text_size.set((logical.width(), logical.height()));
        let px = one_colour(&rgba, stride, w as usize, h as usize, &bands);
        Some(gdk::MemoryTexture::new(w, h, gdk::MemoryFormat::R8g8b8a8, &glib::Bytes::from_owned(px), stride).upcast())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_words() {
        assert_eq!(text_with("text:sonata", "", "", ""), "Sonata");
        assert_eq!(text_with("text:user", "", "Vinicius Oliveira", "vini"), "Vinicius");
        assert_eq!(text_with("text:user", "", "Unknown", "vini"), "vini");
        assert_eq!(text_with("text:custom", "  Hi\n there 🎮 ", "", ""), "Hi there 🎮");
        assert_eq!(text_with("text:custom", &"x".repeat(100), "", "").chars().count(), TEXT_MAX);
        assert_eq!(text_with("text:custom", "   ", "", ""), "Sonata");
        assert_eq!(text_with("distro", "", "", ""), "");
    }

    #[test]
    fn test_emoji_runs() {
        assert_eq!(emoji_runs("Vini 🎮"), vec![(5, 9)]);
        assert_eq!(emoji_runs("🍎"), vec![(0, 4)]);
        assert_eq!(emoji_runs("❤️ ok"), vec![(0, 6)]); // with its presentation selector
        assert!(emoji_runs("Sonata").is_empty());
    }

    /// A light face with dark eyes: the face solid (no greys), the eyes holes.
    #[test]
    fn test_emoji_solid_with_holes() {
        let (w, h) = (10usize, 10usize);
        let mut px = vec![0u8; w * h * 4];
        for i in 0..w * h {
            px[i * 4..i * 4 + 4].copy_from_slice(&[255, 210, 40, 255]); // yellow face
        }
        for x0 in [1, 6] {
            for x in x0..x0 + 3 {
                for y in 2..5 {
                    let i = (y * w + x) * 4;
                    px[i..i + 4].copy_from_slice(&[20, 20, 20, 255]); // dark eyes
                }
            }
        }
        let out = one_colour(&px, w * 4, w, h, &[(0, w)]);
        assert_eq!(out[(7 * w + 5) * 4 + 3], 255);
        assert_eq!(out[(3 * w + 2) * 4 + 3], 0);
        let letters: Vec<u8> = (0..w * h).flat_map(|_| [0u8, 0, 0, 255]).collect();
        let solid = one_colour(&letters, w * 4, w, h, &[(0, w)]);
        assert!((0..w * h).all(|i| solid[i * 4 + 3] == 255)); // plain text: one tone, all solid
    }

    /// What Settings writes is what the logo reads: the menu bar drew the
    /// distro's logo in its colours whatever was chosen (and by default,
    /// where Python draws a silhouette in the bar's text colour).
    #[test]
    fn test_logo_setting_ignored() {
        let _e = crate::testenv::env();
        assert_eq!(current(), (DEFAULT.to_string(), String::new()));
        sonata_config::update("appearance", &json!({"menu_logo": "text:custom", "menu_text": "Vini 🎮"})).unwrap();
        assert_eq!(current(), ("text:custom".to_string(), "Vini 🎮".to_string()));
        sonata_config::update("appearance", &json!({"menu_logo": ""})).unwrap();
        assert_eq!(current().0, DEFAULT); // empty: the default, as `or DEFAULT`
    }
}
