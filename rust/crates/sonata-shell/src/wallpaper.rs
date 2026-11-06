//! The wallpaper of the moment (prefs.wallpaper_uri, videowall.py): the
//! picture in system.json for Light / Dark, else Sonata's "Mountains"; a
//! video wallpaper's poster for what can't play a video (the login and
//! lock screens' blurred backdrop). No GTK here but gdk-pixbuf's loaders.

use gio::prelude::*;
use std::path::{Path, PathBuf};


const BG: &str = "org.gnome.desktop.background";
pub const EXTENSIONS: [&str; 7] = [".mp4", ".m4v", ".webm", ".mkv", ".mov", ".ogv", ".avi"];
/// longest side: a 4K display (the backdrops shrink it further)
const POSTER_MAX: u32 = 3840;
/// a moment in: the first frame is often black (a fade in)
const POSTER_AT_S: f64 = 1.0;
/// ...and a long fade can still be black there: later moments are tried
/// (shares of the video's length), the first bright enough is kept, else the brightest
const POSTER_TRIES: [f64; 3] = [0.1, 0.25, 0.5];
/// mean brightness (0-255) under which a frame counts as black
const DARK_MEAN: f64 = 18.0;
/// the Python Sonata's: both make and share the same posters
const POSTER_VERSION: u32 = 2;

pub fn uri_of(path: &Path) -> String {
    format!("file://{}", path.display())
}

pub fn path_of(uri: &str) -> Option<PathBuf> {
    gio::File::for_uri(uri).path()
}

/// Sonata's own "Mountains": snowy peaks in Light, under the stars in Dark.
pub fn default_uri(dark: bool) -> String {
    uri_of(&sonata_config::data_path("wallpapers").join(if dark { "starry-night.jpg" } else { "snow-peaks.jpg" }))
}

/// A file's path or its content type ("video/mp4").
pub fn is_video(path_or_type: &str) -> bool {
    let s = path_or_type.to_lowercase();
    s.starts_with("video/") || EXTENSIONS.iter().any(|e| s.ends_with(e))
}

/// A picture that's there and can be shown (another desktop's default can
/// be a format no pixbuf loader reads, or a file it removed).
pub fn usable_picture(uri: &str) -> bool {
    let Some(path) = (!uri.is_empty()).then(|| path_of(uri)).flatten() else { return false };
    if is_video(&path.display().to_string()) {
        return path.is_file();
    }
    path.is_file() && gtk::gdk_pixbuf::Pixbuf::file_info(&path).is_some()
}

/// The desktop picture for Light / Dark: the chosen one, else Sonata's.
pub fn wallpaper_uri(dark: bool) -> String {
    let data = sonata_config::stored("system");
    let get = |k: &str| data.get(&format!("{BG}/{k}")).and_then(|v| v.as_str()).filter(|s| !s.is_empty()).map(String::from);
    let uri = (if dark { get("picture-uri-dark") } else { None }).or_else(|| get("picture-uri")).unwrap_or_default();
    if usable_picture(&uri) {
        return uri;
    }
    stock_moved(&uri, &sonata_config::data_path("wallpapers")).filter(|u| usable_picture(u)).unwrap_or_else(|| default_uri(dark))
}

/// One of Sonata's own wallpapers named by where an older install kept it
/// (the Python Sonata's ~/.local/share/sonata2/sonata2/data/wallpapers, the
/// Rust trial's ~/.local/share/sonata2-rs/wallpapers, a clone's
/// sonata2/data/wallpapers): the same picture in this install's folder.
/// The user's choice in system.json stays as it is.
pub fn stock_moved(uri: &str, folder: &Path) -> Option<String> {
    let path = path_of(uri)?;
    let name = path.file_name()?;
    let parent = path.parent()?;
    if parent.file_name()? != "wallpapers" || parent == folder {
        return None;
    }
    let here = folder.join(name);
    here.is_file().then(|| uri_of(&here))
}

/// The wallpaper as a picture: a video's poster (made the first time: call
/// it off the UI thread), else the picture itself; Sonata's default when
/// the video has no frame to give.
pub fn still_uri(dark: bool) -> String {
    let uri = wallpaper_uri(dark);
    let Some(path) = path_of(&uri) else { return uri };
    if !is_video(&path.display().to_string()) {
        return uri;
    }
    poster(&path).map(|p| uri_of(&p)).unwrap_or_else(|| default_uri(dark))
}

fn cache() -> PathBuf {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    std::env::var_os("XDG_CACHE_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| home.join(".cache")).join("sonata2").join("wallpapers")
}

/// Where the video's poster goes: named after its path, size and time
/// (another video at the same path gets a new poster).
pub fn poster_path(video: &Path) -> PathBuf {
    use std::os::unix::fs::MetadataExt;
    let stamp = std::fs::metadata(video).map(|m| format!("{}:{}", m.len(), m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128)).unwrap_or_default();
    let abs = std::path::absolute(video).unwrap_or_else(|_| video.to_path_buf());
    let key = format!("{}\0{stamp}\0{POSTER_VERSION}", abs.display());
    let sum = glib::compute_checksum_for_data(glib::ChecksumType::Sha1, key.as_bytes()).map(|s| s.to_string()).unwrap_or_default();
    cache().join(format!("{}.jpg", &sum[..20.min(sum.len())]))
}

fn ffmpeg(video: &Path, out: &Path, at: f64) -> bool {
    if sonata_backend::run::which("ffmpeg").is_none() {
        return false;
    }
    let scale = format!("scale='if(gt(iw,ih),min({POSTER_MAX},iw),-2)':'if(gt(iw,ih),-2,min({POSTER_MAX},ih))'");
    let at = format!("{at}");
    let (v, o) = (video.display().to_string(), out.display().to_string());
    let (rc, _) = sonata_backend::run::run(&["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", &at, "-i", &v, "-frames:v", "1", "-vf", &scale, "-q:v", "3", "-f", "image2", &o], 20);
    let _ = rc;
    std::fs::metadata(out).is_ok_and(|m| m.len() > 0)
}

fn thumbnailer(video: &Path, out: &Path) -> bool {
    let (v, o) = (video.display().to_string(), out.display().to_string());
    sonata_backend::run::run(&["ffmpegthumbnailer", "-i", &v, "-o", &o, "-s", "0", "-c", "jpeg", "-q", "8"], 20);
    std::fs::metadata(out).is_ok_and(|m| m.len() > 0)
}

/// The video's length in seconds (ffprobe), 0 when unknown.
fn duration(video: &Path) -> f64 {
    let v = video.display().to_string();
    let (rc, out) = sonata_backend::run::run(&["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", &v], 20);
    if rc == 0 { out.trim().parse::<f64>().unwrap_or(0.0).max(0.0) } else { 0.0 }
}

/// A picture's mean brightness 0-255 (255 when it can't be measured: kept),
/// with Pillow's "L" weights, as the Python Sonata measures it.
pub fn brightness(path: &Path) -> f64 {
    let Ok(img) = image::open(path) else { return 255.0 };
    let small = img.resize_exact(64, 64, image::imageops::FilterType::Triangle).to_rgb8();
    let sum: f64 = small.pixels().map(|p| (p[0] as f64 * 299.0 + p[1] as f64 * 587.0 + p[2] as f64 * 114.0) / 1000.0).sum();
    sum / (64.0 * 64.0)
}

/// A frame that isn't black, into `out`: at POSTER_AT_S, else later on.
fn frame(video: &Path, out: &Path) -> bool {
    if !(ffmpeg(video, out, POSTER_AT_S) || ffmpeg(video, out, 0.0)) {
        return thumbnailer(video, out);
    }
    let mut best_b = brightness(out);
    if best_b >= DARK_MEAN {
        return true;
    }
    let length = duration(video);
    let best = PathBuf::from(format!("{}.best.jpg", out.display()));
    let _ = std::fs::rename(out, &best);
    if length > 0.0 {
        for share in POSTER_TRIES {
            if ffmpeg(video, out, length * share) {
                let b = brightness(out);
                if b >= DARK_MEAN {
                    let _ = std::fs::remove_file(&best);
                    return true;
                }
                if b > best_b {
                    let _ = std::fs::rename(out, &best);
                    best_b = b;
                }
            }
        }
    }
    let _ = std::fs::rename(&best, out); // all dark: the brightest
    true
}

/// The video's poster, made now if it isn't there yet (a fraction of a
/// second; off the UI thread the first time). None when the video can't
/// be read, or nothing can take a frame of it.
pub fn poster(video: &Path) -> Option<PathBuf> {
    if !video.is_file() {
        return None;
    }
    let out = poster_path(video);
    if out.is_file() {
        return Some(out);
    }
    std::fs::create_dir_all(out.parent()?).ok()?;
    let tmp = PathBuf::from(format!("{}.{}.tmp.jpg", out.display(), std::process::id()));
    let made = frame(video, &tmp) && std::fs::rename(&tmp, &out).is_ok(); // whole or not at all: another process may read it
    let _ = std::fs::remove_file(&tmp);
    made.then_some(out)
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn videos_by_name_or_type() {
        assert!(is_video("/x/Clip.MP4") && is_video("video/webm"));
        assert!(!is_video("/x/photo.jpg"));
    }

    #[test]
    fn posters_are_named_like_the_python_ones() {
        let d = tempfile::tempdir().unwrap();
        let v = d.path().join("clip.mp4");
        std::fs::write(&v, b"12345").unwrap();
        use std::os::unix::fs::MetadataExt;
        let m = std::fs::metadata(&v).unwrap();
        let key = format!("{}\0{}:{}\0{POSTER_VERSION}", v.display(), m.len(), m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128);
        let want = glib::compute_checksum_for_data(glib::ChecksumType::Sha1, key.as_bytes()).unwrap();
        assert_eq!(poster_path(&v).file_name().unwrap().to_str().unwrap(), format!("{}.jpg", &want[..20]));
        // (the same as Python's: hashlib.sha1(f"{abspath}\0{size}:{mtime_ns}\0{2}").hexdigest()[:20])
        let py = std::process::Command::new("python3")
            .args(["-c", "import hashlib,os,sys;p=sys.argv[1];s=os.stat(p);print(hashlib.sha1(f'{os.path.abspath(p)}\\0{s.st_size}:{s.st_mtime_ns}\\0{2}'.encode()).hexdigest()[:20])", v.to_str().unwrap()])
            .output();
        if let Ok(o) = py {
            if o.status.success() {
                assert_eq!(String::from_utf8_lossy(&o.stdout).trim(), &want[..20]);
            }
        }
    }

    #[test]
    fn brightness_of_a_black_and_a_white_picture() {
        let d = tempfile::tempdir().unwrap();
        let (b, w) = (d.path().join("b.png"), d.path().join("w.png"));
        image::RgbImage::from_pixel(8, 8, image::Rgb([0, 0, 0])).save(&b).unwrap();
        image::RgbImage::from_pixel(8, 8, image::Rgb([255, 255, 255])).save(&w).unwrap();
        assert!(brightness(&b) < 1.0 && brightness(&w) > 254.0);
        assert_eq!(brightness(&d.path().join("none.jpg")), 255.0);
    }

    /// Upgrading from the Python Sonata: the desktop picture chosen from
    /// Sonata's own wallpapers was a path inside the old install folder,
    /// which the installer moves to the backup -- the desktop fell back to
    /// Mountains. The same picture is found in this install's folder.
    #[test]
    fn test_stock_wallpaper_lost_after_the_upgrade() {
        let d = tempfile::tempdir().unwrap();
        let folder = d.path().join("share/sonata2-rs/data/wallpapers");
        std::fs::create_dir_all(&folder).unwrap();
        std::fs::write(folder.join("pier.jpg"), b"jpg").unwrap();
        let old = "file:///home/v/.local/share/sonata2/sonata2/data/wallpapers/pier.jpg";
        assert_eq!(stock_moved(old, &folder), Some(uri_of(&folder.join("pier.jpg"))));
        assert_eq!(stock_moved("file:///home/v/.local/share/sonata2-rs/wallpapers/pier.jpg", &folder), Some(uri_of(&folder.join("pier.jpg"))));
        // the user's own pictures, and names Sonata has no picture for: no
        assert_eq!(stock_moved("file:///home/v/Pictures/pier.jpg", &folder), None);
        assert_eq!(stock_moved("file:///old/wallpapers/mine.jpg", &folder), None);
    }
}