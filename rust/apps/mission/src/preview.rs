//! Development preview (shell/preview.py): a shell surface over a sample
//! wallpaper, in a normal window, so it can be judged (and screenshotted)
//! without a Wayland session.
//!
//! The compositor's backdrop blur doesn't exist here, so the preview hands
//! its caller a blurred, saturated copy of the wallpaper to paint under a
//! glass plate -- the same recipe as the Wayfire blur settings in
//! config/wayfire.ini. `PREVIEW_WALLPAPER` (a real photo) judges glass and
//! contrast better; `PREVIEW_SIZE` (WxH) is the virtual screen
//! (tools/wl-preview.sh).
//!
//! Differences from the Python one: the pictures are made in memory (no PNG
//! cache under ~/.cache/sonata2), with cairo and the `image` crate instead
//! of Pillow, and the abstract wallpaper's random fields come from a small
//! generator of its own -- the same kind of picture, not the same pixels.

use gtk::gdk;
use gtk::prelude::*;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;
use std::time::Duration;

/// match [blur] in config/wayfire.ini
pub const BLUR_RADIUS: f32 = 18.0;
pub const SATURATION: f32 = 1.6;

const CSS: &str = "
.preview-wall { background-image: linear-gradient(160deg, #1d3b8f 0%%, #6b3fa0 38%%, #e0567a 70%%, #f3a452 100%%); }
";

/// "1280x800" -> (1280, 800).
pub fn parse_size(s: &str) -> Option<(i32, i32)> {
    let (w, h) = s.split_once('x')?;
    let (w, h) = (w.trim().parse().ok()?, h.trim().parse().ok()?);
    (w > 0 && h > 0).then_some((w, h))
}

/// PREVIEW_SIZE, else `default`.
pub fn preview_size(default: (i32, i32)) -> (i32, i32) {
    std::env::var("PREVIEW_SIZE").ok().and_then(|s| parse_size(&s)).unwrap_or(default)
}

/// Random.Random(11)'s job: the same picture every time.
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        // xorshift64*
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    /// a <= n <= b, as random.randint
    fn int(&mut self, a: i32, b: i32) -> i32 {
        a + (self.next() % ((b - a).max(0) as u64 + 1)) as i32
    }

    fn pick<T: Copy>(&mut self, of: &[T]) -> T {
        of[(self.next() % of.len() as u64) as usize]
    }
}

/// ImageEnhance.Color: each pixel away from (or toward) its grey.
pub fn saturate(img: &mut image::RgbImage, factor: f32) {
    for p in img.pixels_mut() {
        let [r, g, b] = p.0.map(f32::from);
        let l = r * 0.299 + g * 0.587 + b * 0.114;
        p.0 = [r, g, b].map(|c| (l + (c - l) * factor).round().clamp(0.0, 255.0) as u8);
    }
}

fn surface_to_image(s: &mut gtk::cairo::ImageSurface) -> Option<image::RgbImage> {
    s.flush();
    let (w, h, stride) = (s.width() as u32, s.height() as u32, s.stride() as usize);
    let data = s.data().ok()?;
    // RGB24: one native-endian 32-bit word per pixel, xRGB
    Some(image::RgbImage::from_fn(w, h, |x, y| {
        let i = y as usize * stride + x as usize * 4;
        let px = u32::from_ne_bytes([data[i], data[i + 1], data[i + 2], data[i + 3]]);
        image::Rgb([(px >> 16) as u8, (px >> 8) as u8, px as u8])
    }))
}

fn image_to_surface(img: &image::RgbImage) -> Option<gtk::cairo::ImageSurface> {
    let mut s = gtk::cairo::ImageSurface::create(gtk::cairo::Format::Rgb24, img.width() as i32, img.height() as i32).ok()?;
    let stride = s.stride() as usize;
    {
        let mut data = s.data().ok()?;
        for (x, y, p) in img.enumerate_pixels() {
            let i = y as usize * stride + x as usize * 4;
            let px = (p.0[0] as u32) << 16 | (p.0[1] as u32) << 8 | p.0[2] as u32;
            data[i..i + 4].copy_from_slice(&px.to_ne_bytes());
        }
    }
    Some(s)
}

pub fn texture_of(img: &image::RgbImage) -> gdk::Texture {
    let (w, h) = (img.width() as i32, img.height() as i32);
    let bytes = glib::Bytes::from(img.as_raw().as_slice());
    gdk::MemoryTexture::new(w, h, gdk::MemoryFormat::R8g8b8, &bytes, w as usize * 3).upcast()
}

fn image_of(tex: &gdk::Texture) -> image::RgbImage {
    let mut dl = gdk::TextureDownloader::new(tex);
    dl.set_format(gdk::MemoryFormat::R8g8b8);
    let (bytes, stride) = dl.download_bytes();
    let (w, h) = (tex.width() as u32, tex.height() as u32);
    image::RgbImage::from_fn(w, h, |x, y| {
        let i = y as usize * stride + x as usize * 3;
        image::Rgb([bytes[i], bytes[i + 1], bytes[i + 2]])
    })
}

/// The compositor's glass over `img`: blurred (at a quarter size: the
/// same look, a sixteenth of the work), then saturated.
pub fn frosted(img: &image::RgbImage) -> image::RgbImage {
    let (w, h) = ((img.width() / 4).max(1), (img.height() / 4).max(1));
    let small = image::imageops::resize(img, w, h, image::imageops::FilterType::Triangle);
    let mut soft = image::imageops::blur(&small, BLUR_RADIUS / 4.0);
    saturate(&mut soft, SATURATION);
    image::imageops::resize(&soft, img.width(), img.height(), image::imageops::FilterType::Triangle)
}

/// The generated abstract wallpaper: big soft colour fields, then sharp
/// details so the blur is visible.
pub fn generated(w: i32, h: i32, dark: bool) -> Option<image::RgbImage> {
    use gtk::cairo::{Context, Format, ImageSurface};
    let mut rnd = Rng(11);
    let base: (u8, u8, u8) = if dark { (18, 24, 60) } else { (40, 80, 170) };
    let palette: [(u8, u8, u8); 4] = if dark { [(90, 40, 140), (150, 40, 90), (30, 60, 130), (160, 90, 40)] } else { [(120, 70, 200), (240, 90, 120), (250, 170, 80), (60, 170, 220)] };
    let rgb = |cr: &Context, c: (u8, u8, u8)| cr.set_source_rgb(c.0 as f64 / 255.0, c.1 as f64 / 255.0, c.2 as f64 / 255.0);
    // the fields at an eighth of the size: blurred 60 px anyway
    const K: i32 = 8;
    let mut fields = ImageSurface::create(Format::Rgb24, (w / K).max(1), (h / K).max(1)).ok()?;
    {
        let cr = Context::new(&fields).ok()?;
        rgb(&cr, base);
        cr.paint().ok()?;
        for _ in 0..9 {
            let (x, y, r) = (rnd.int(0, w), rnd.int(0, h), rnd.int(h / 3, h));
            rgb(&cr, rnd.pick(&palette));
            cr.arc(x as f64 / K as f64, y as f64 / K as f64, r as f64 / K as f64, 0.0, std::f64::consts::TAU);
            cr.fill().ok()?;
        }
    }
    let soft = image::imageops::blur(&surface_to_image(&mut fields)?, 60.0 / K as f32);
    let soft = image_to_surface(&soft)?;
    let mut img = ImageSurface::create(Format::Rgb24, w, h).ok()?;
    {
        let cr = Context::new(&img).ok()?;
        cr.scale(K as f64, K as f64);
        cr.set_source_surface(&soft, 0.0, 0.0).ok()?;
        cr.source().set_filter(gtk::cairo::Filter::Bilinear);
        cr.paint().ok()?;
        cr.identity_matrix();
        cr.set_line_width(3.0);
        let details = [(255, 255, 255), (255, 214, 10), (48, 209, 88), (10, 132, 255)];
        for _ in 0..40 {
            let (x, y, r) = (rnd.int(0, w), rnd.int(0, h), rnd.int(4, 26));
            rgb(&cr, rnd.pick(&details));
            cr.new_sub_path();
            cr.arc(x as f64, y as f64, r as f64, 0.0, std::f64::consts::TAU);
            cr.stroke().ok()?;
        }
        cr.set_line_width(2.0);
        cr.set_source_rgb(1.0, 1.0, 1.0);
        for i in (0..w).step_by(48) {
            cr.move_to(i as f64, (h - 90) as f64);
            cr.line_to((i + 30) as f64, h as f64);
        }
        cr.stroke().ok()?;
    }
    surface_to_image(&mut img)
}

/// A photo cover-fit to w x h.
fn photo(path: &str, w: i32, h: i32) -> Option<image::RgbImage> {
    let img = image_of(&gdk::Texture::from_filename(path).ok()?);
    let k = (w as f64 / img.width() as f64).max(h as f64 / img.height() as f64);
    let (rw, rh) = ((img.width() as f64 * k) as u32 + 1, (img.height() as f64 * k) as u32 + 1);
    let big = image::imageops::resize(&img, rw, rh, image::imageops::FilterType::Lanczos3);
    Some(image::imageops::crop_imm(&big, (rw - w as u32) / 2, (rh - h as u32) / 2, w as u32, h as u32).to_image())
}

/// (sharp, blurred) pictures of the preview's wallpaper, or None.
pub fn wallpaper(w: i32, h: i32, dark: bool) -> Option<(gdk::Texture, gdk::Texture)> {
    let img = match std::env::var("PREVIEW_WALLPAPER").ok().filter(|p| std::path::Path::new(p).is_file()) {
        Some(p) => photo(&p, w, h)?,
        None => generated(w, h, dark)?,
    };
    Some((texture_of(&img), texture_of(&frosted(&img))))
}

pub type OnBackdrop = Box<dyn Fn(&gdk::Texture)>;

/// A normal window: the sample wallpaper, `child` over it.
pub struct PreviewWindow {
    pub win: gtk::ApplicationWindow,
    /// the wallpaper, and what is shown over it
    pub over: gtk::Overlay,
    wall: gtk::Picture,
    size: (i32, i32),
    walls: RefCell<HashMap<bool, Option<(gdk::Texture, gdk::Texture)>>>,
    /// gets the blurred wallpaper (a glass plate's backdrop: the caller
    /// decides whether its glass is on)
    on_backdrop: Option<OnBackdrop>,
}

impl PreviewWindow {
    pub fn new(app: &gtk::Application, child: Option<&gtk::Widget>, size: (i32, i32), on_backdrop: Option<OnBackdrop>) -> Rc<PreviewWindow> {
        sonata_ui::register("preview", CSS);
        let win = gtk::ApplicationWindow::builder().application(app).title("Sonata 2 preview").default_width(size.0).default_height(size.1).decorated(false).resizable(false).build();
        let wall = gtk::Picture::builder().content_fit(gtk::ContentFit::Fill).can_shrink(true).hexpand(true).vexpand(true).css_classes(["preview-wall"]).build();
        let over = gtk::Overlay::new();
        over.set_child(Some(&wall));
        if let Some(c) = child {
            over.add_overlay(c);
        }
        win.set_child(Some(&over));
        let me = Rc::new(PreviewWindow { win, over, wall, size, walls: RefCell::default(), on_backdrop });
        let weak = Rc::downgrade(&me);
        adw::StyleManager::default().connect_dark_notify(move |_| {
            let weak = weak.clone();
            glib::idle_add_local_once(move || {
                if let Some(me) = weak.upgrade() {
                    me.paint();
                }
            });
        });
        let weak = Rc::downgrade(&me);
        me.win.connect_map(move |_| {
            let weak = weak.clone();
            glib::timeout_add_local_once(Duration::from_millis(150), move || {
                if let Some(me) = weak.upgrade() {
                    me.paint();
                }
            });
        });
        me
    }

    fn paint(&self) {
        let dark = sonata_ui::is_dark();
        let walls = self.walls.borrow_mut().entry(dark).or_insert_with(|| wallpaper(self.size.0, self.size.1, dark)).clone();
        let Some((sharp, blurred)) = walls else { return };
        self.wall.set_paintable(Some(&sharp));
        if let Some(f) = &self.on_backdrop {
            f(&blurred);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sizes() {
        assert_eq!(parse_size("960x260"), Some((960, 260)));
        assert_eq!(parse_size(" 1280 x 800"), Some((1280, 800)));
        assert_eq!(parse_size("0x5"), None);
        assert_eq!(parse_size("big"), None);
    }

    #[test]
    fn saturation_moves_away_from_grey() {
        let mut img = image::RgbImage::from_pixel(1, 1, image::Rgb([200, 100, 100]));
        saturate(&mut img, SATURATION);
        let [r, g, b] = img.get_pixel(0, 0).0;
        assert!(r > 200 && g < 100 && g == b);
        let mut grey = image::RgbImage::from_pixel(1, 1, image::Rgb([90, 90, 90]));
        saturate(&mut grey, SATURATION);
        assert_eq!(grey.get_pixel(0, 0).0, [90, 90, 90]);
    }

    /// The same picture every time, and its glass the same size.
    #[test]
    fn the_wallpaper_is_made_and_frosted() {
        let a = generated(160, 100, true).unwrap();
        assert_eq!(a.dimensions(), (160, 100));
        assert_eq!(a, generated(160, 100, true).unwrap());
        assert_ne!(a, generated(160, 100, false).unwrap());
        assert_eq!(frosted(&a).dimensions(), (160, 100));
    }
}
