//! The icons: the pixmap to draw, and the one-tone silhouette the menu bar
//! shows (macOS menu extras are template images).

use crate::item::IconSource;
use crate::ICON_PX;
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, glib, graphene, gsk};
use std::cell::RefCell;

/// The pixmap (w, h, bytes) closest to `target`: the smallest one >=
/// target, else the largest. Entries with too little data are skipped.
pub fn pick_pixmap(sizes: &[(i32, i32, usize)], target: i32) -> Option<usize> {
    let mut best: Option<((u8, i32), usize)> = None;
    for (i, &(w, h, len)) in sizes.iter().enumerate() {
        if w <= 0 || h <= 0 || len < (w as usize) * (h as usize) * 4 {
            continue;
        }
        let s = w.max(h);
        let key = if s >= target { (0, s) } else { (1, -s) };
        if best.is_none_or(|(k, _)| key < k) {
            best = Some((key, i));
        }
    }
    best.map(|(_, i)| i)
}

/// ARGB32 big-endian (A, R, G, B in memory) -> RGBA8, straight alpha both.
pub fn argb_to_rgba(argb: &[u8], w: usize, h: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(w * h * 4);
    for px in argb.chunks_exact(4).take(w * h) {
        out.extend_from_slice(&[px[1], px[2], px[3], px[0]]);
    }
    out
}

fn lum(r: u8, g: u8, b: u8) -> f64 {
    (0.2126 * r as f64 + 0.7152 * g as f64 + 0.0722 * b as f64) / 255.0
}

/// RGBA8 pixels -> the solid one-tone shape (RGBA8, only its alpha set),
/// None when the icon is empty. The light details drawn on a dark body
/// (Discord's logo on its disc) become holes, like the template images
/// macOS apps give their menu extras. The shape is then made fully solid:
/// icons drawn half see-through (Discord's grey tray logo) came out grey
/// instead of white in Dark Mode / black in Light.
pub fn mono_mask(px: &[u8], stride: usize, w: usize, h: usize) -> Option<Vec<u8>> {
    let mut lums = Vec::new();
    for y in 0..h {
        for x in 0..w {
            let i = y * stride + x * 4;
            if px[i + 3] > 128 {
                lums.push(lum(px[i], px[i + 1], px[i + 2]));
            }
        }
    }
    let (lo, hi) = if lums.len() >= 8 {
        lums.sort_by(|a, b| a.total_cmp(b));
        (lums[lums.len() / 10], lums[lums.len() * 9 / 10])
    } else {
        (0.0, 0.0)
    };
    let two_tone = hi - lo >= 0.3;
    let mut out = vec![0u8; w * h * 4];
    let mut alphas = Vec::new();
    for y in 0..h {
        for x in 0..w {
            let (i, o) = (y * stride + x * 4, (y * w + x) * 4);
            let mut a = px[i + 3];
            if two_tone && a != 0 {
                let l = lum(px[i], px[i + 1], px[i + 2]);
                a = (a as f64 * ((hi - l) / (hi - lo)).clamp(0.0, 1.0)) as u8;
            }
            out[o + 3] = a;
            if a > 8 {
                alphas.push(a);
            }
        }
    }
    if alphas.is_empty() {
        return None;
    }
    alphas.sort_unstable();
    let top = alphas[alphas.len() * 9 / 10]; // the body's own opacity (edges are softer)
    if top < 250 {
        let k = 255.0 / (top.max(1) as f64);
        for o in (3..out.len()).step_by(4) {
            out[o] = (out[o] as f64 * k).min(255.0) as u8;
        }
    }
    Some(out)
}

/// What a silhouette replaces: the new shape, or -- a blank picture -- the
/// last one shown, else a generic icon (Vini: Claude's icon went
/// invisible for a while: an app sent a blank frame).
#[derive(Debug, PartialEq)]
pub(crate) enum Next {
    Shape(Vec<u8>),
    Keep,
    Generic,
}

pub(crate) fn next_shape(mask: Option<Vec<u8>>, has_last: bool) -> Next {
    match mask {
        Some(m) => Next::Shape(m),
        None if has_last => Next::Keep,
        None => Next::Generic,
    }
}

const GENERIC: &str = "application-x-executable-symbolic";

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct MonoIcon {
        pub paintable: RefCell<Option<gdk::Paintable>>,
        // (icon serial, scale) shown: the same one again costs nothing
        pub src: RefCell<Option<(u64, i32)>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for MonoIcon {
        const NAME: &'static str = "SonataRsTrayMonoIcon";
        type Type = super::MonoIcon;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for MonoIcon {}

    impl WidgetImpl for MonoIcon {
        fn measure(&self, _o: gtk::Orientation, _for_size: i32) -> (i32, i32, i32, i32) {
            (ICON_PX, ICON_PX, -1, -1)
        }

        fn snapshot(&self, snap: &gtk::Snapshot) {
            let Some(p) = self.paintable.borrow().clone() else { return };
            let px = ICON_PX as f32;
            let rect = graphene::Rect::new(0.0, 0.0, px, px);
            snap.push_mask(gsk::MaskMode::Alpha);
            p.snapshot(snap, px as f64, px as f64);
            snap.pop();
            snap.append_color(&self.obj().color(), &rect);
            snap.pop();
        }
    }
}

glib::wrapper! {
    /// An app's tray icon as a silhouette in the menu bar's text colour
    /// (white in Dark, black in Light, like macOS menu extras): whatever
    /// colours the app draws, only the shape (alpha) is kept.
    pub struct MonoIcon(ObjectSubclass<imp::MonoIcon>) @extends gtk::Widget;
}

impl Default for MonoIcon {
    fn default() -> Self {
        Self::new()
    }
}

impl MonoIcon {
    pub fn new() -> Self {
        let w: Self = glib::Object::new();
        w.set_valign(gtk::Align::Center);
        w.set_halign(gtk::Align::Center);
        w
    }

    /// The paintable drawn (as a mask), if any.
    pub fn paintable(&self) -> Option<gdk::Paintable> {
        self.imp().paintable.borrow().clone()
    }

    fn named(&self, name: &str, scale: i32) -> gdk::Paintable {
        let theme = gtk::IconTheme::for_display(&self.display());
        theme.lookup_icon(name, &[], ICON_PX, scale, gtk::TextDirection::None, gtk::IconLookupFlags::FORCE_REGULAR).upcast()
    }

    /// Show `src` (None: a generic icon). `serial` is the item's icon
    /// serial: the same serial and scale again keep what is drawn.
    pub fn set_icon(&self, src: Option<&IconSource>, serial: u64, scale: i32) {
        let imp = self.imp();
        if src.is_some() && *imp.src.borrow() == Some((serial, scale)) {
            return; // unchanged: no new silhouette
        }
        imp.src.replace(Some((serial, scale)));
        let shape = match src {
            None => Some(self.named(GENERIC, scale)),
            Some(IconSource::Name(n)) => Some(self.named(n, scale)),
            Some(IconSource::File(path)) => {
                let mask = gdk::Texture::from_filename(path).ok().map(|t| {
                    let mut d = gdk::TextureDownloader::new(&t);
                    d.set_format(gdk::MemoryFormat::R8g8b8a8);
                    let (bytes, stride) = d.download_bytes();
                    (mono_mask(&bytes, stride, t.width() as usize, t.height() as usize), t.width(), t.height())
                });
                match mask {
                    Some((m, w, h)) => self.shape(m, w, h, scale),
                    None => Some(self.named(GENERIC, scale)), // unreadable
                }
            }
            Some(IconSource::Pixmap(p)) => {
                let (w, h) = (p.width as usize, p.height as usize);
                let rgba = argb_to_rgba(&p.argb, w, h);
                self.shape(mono_mask(&rgba, w * 4, w, h), p.width, p.height, scale)
            }
        };
        if let Some(s) = shape {
            imp.paintable.replace(Some(s));
            self.queue_draw();
        }
    }

    /// None: keep what is shown.
    fn shape(&self, mask: Option<Vec<u8>>, w: i32, h: i32, scale: i32) -> Option<gdk::Paintable> {
        match next_shape(mask, self.imp().paintable.borrow().is_some()) {
            Next::Shape(m) => Some(gdk::MemoryTexture::new(w, h, gdk::MemoryFormat::R8g8b8a8, &glib::Bytes::from_owned(m), (w * 4) as usize).upcast()),
            Next::Keep => None, // the last one stays
            Next::Generic => Some(self.named(GENERIC, scale)),
        }
    }
}
