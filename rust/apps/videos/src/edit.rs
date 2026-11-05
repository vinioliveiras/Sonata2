//! Videos' editing (videos/edit.py; QuickTime's Trim, and Crop): the
//! commands and the work behind the bars, no widgets here.
//!
//! The original is never touched: the result is a new file beside it
//! ("Holiday (Trimmed).mp4"), then opened in the same window. ffmpeg does
//! the work, re-encoding so the cut is exact to the frame (copying streams
//! cuts at the nearest keyframe, seconds off).

use sonata_i18n::n_;
use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};

/// containers that take H.264 + AAC as they are; any other movie becomes .mp4
const VIDEO_KEEP: [&str; 4] = [".mp4", ".m4v", ".mov", ".mkv"];
/// the shortest trim, seconds
pub const MIN_LENGTH: f64 = 0.5;
/// px of the movie: the smallest crop
pub const MIN_CROP: f64 = 32.0;
pub const RATIOS: [(&str, &str, Option<f64>); 5] = [("free", n_!("Free"), None), ("16:9", "16:9", Some(16.0 / 9.0)), ("4:3", "4:3", Some(4.0 / 3.0)), ("1:1", "1:1", Some(1.0)), ("9:16", "9:16", Some(9.0 / 16.0))];

/// a song keeps its format: the encoder for it
fn audio_codec(ext: &str) -> Option<&'static [&'static str]> {
    Some(match ext {
        ".mp3" => &["-c:a", "libmp3lame", "-q:a", "2"],
        ".flac" => &["-c:a", "flac"],
        ".ogg" | ".oga" => &["-c:a", "libvorbis", "-q:a", "6"],
        ".opus" => &["-c:a", "libopus", "-b:a", "160k"],
        ".m4a" | ".aac" => &["-c:a", "aac", "-b:a", "256k"],
        ".wav" => &["-c:a", "pcm_s16le"],
        _ => return None,
    })
}

const VIDEO_CODECS: [&str; 14] = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"];

pub fn available() -> bool {
    sonata_backend::run::which("ffmpeg").is_some()
}

fn ext_of(p: &Path) -> String {
    p.extension().map(|e| format!(".{}", e.to_string_lossy().to_lowercase())).unwrap_or_default()
}

/// Beside the original, never over a file: "name (Trimmed).ext", then
/// "name (Trimmed 2).ext"...
pub fn output_path(path: &Path, what: &str, audio: bool) -> PathBuf {
    let folder = path.parent().unwrap_or(Path::new(""));
    let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let mut ext = ext_of(path);
    if !audio && !VIDEO_KEEP.contains(&ext.as_str()) {
        ext = ".mp4".into();
    }
    if audio && audio_codec(&ext).is_none() {
        ext = ".m4a".into();
    }
    let mut n = 1;
    loop {
        let tag = if n == 1 { what.to_string() } else { format!("{what} {n}") };
        let out = folder.join(format!("{stem} ({tag}){ext}"));
        if !out.exists() {
            return out;
        }
        n += 1;
    }
}

fn codecs(dst: &Path, audio: bool) -> Vec<String> {
    let v: Vec<&str> = if audio {
        let mut a = vec!["-vn", "-map", "0:a:0"];
        a.extend(audio_codec(&ext_of(dst)).unwrap_or(audio_codec(".m4a").expect("m4a")));
        a
    } else {
        let mut a = vec!["-map", "0:v:0", "-map", "0:a?"];
        a.extend(VIDEO_CODECS);
        a
    };
    v.into_iter().map(String::from).collect()
}

fn base() -> Vec<String> {
    ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats"].into_iter().map(String::from).collect()
}

/// A trim inside the movie, at least MIN_LENGTH long.
pub fn clamp_range(start: f64, end: f64, duration: f64) -> (f64, f64) {
    let duration = duration.max(MIN_LENGTH);
    let start = start.min(duration - MIN_LENGTH).max(0.0);
    let end = end.min(duration).max(start + MIN_LENGTH);
    (start, end)
}

/// Keep start..end (seconds). -ss after -i: exact to the frame.
pub fn trim_command(src: &Path, dst: &Path, start: f64, end: f64, audio: bool) -> Vec<String> {
    let mut c = base();
    c.extend(["-i".into(), src.display().to_string(), "-ss".into(), format!("{start:.3}"), "-to".into(), format!("{end:.3}")]);
    c.extend(codecs(dst, audio));
    c.push(dst.display().to_string());
    c
}

/// Keep the w x h rectangle at (x, y) of the picture (even sizes: H.264).
pub fn crop_command(src: &Path, dst: &Path, x: i64, y: i64, w: i64, h: i64) -> Vec<String> {
    let (w, h) = ((w - w % 2).max(2), (h - h % 2).max(2));
    let mut c = base();
    c.extend(["-i".into(), src.display().to_string(), "-vf".into(), format!("crop={w}:{h}:{x}:{y}")]);
    c.extend(codecs(dst, false));
    c.push(dst.display().to_string());
    c
}

/// ffmpeg's -progress lines: 0..1 from out_time_us / out_time_ms, else None.
pub fn progress_of(line: &str, duration: f64) -> Option<f64> {
    let (key, value) = line.trim().split_once('=')?;
    if !["out_time_us", "out_time_ms"].contains(&key) || duration <= 0.0 {
        return None;
    }
    let n: i64 = value.parse().ok()?;
    Some((n as f64 / 1e6 / duration).clamp(0.0, 1.0))
}

/// ffmpeg in the background; on_progress(0..1) and on_done(ok, message) on
/// the main loop. cancel() stops it and removes the half-written file.
pub struct Export {
    child: Arc<Mutex<Option<std::process::Child>>>,
    cancelled: Arc<std::sync::atomic::AtomicBool>,
}

impl Export {
    pub fn start(cmd: Vec<String>, duration: f64, on_progress: impl Fn(f64) + 'static, on_done: impl FnOnce(bool, String) + 'static) -> Self {
        use std::sync::atomic::{AtomicBool, Ordering};
        let dst = PathBuf::from(cmd.last().cloned().unwrap_or_default());
        let out = dst.clone();
        let cancelled = Arc::new(AtomicBool::new(false));
        let child: Arc<Mutex<Option<std::process::Child>>> = Arc::default();
        let (tx, rx) = std::sync::mpsc::channel::<Result<f64, (bool, String)>>();
        let (c, cx) = (child.clone(), cancelled.clone());
        std::thread::spawn(move || {
            let spawned = Command::new(&cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn();
            let mut proc = match spawned {
                Ok(p) => p,
                Err(e) => {
                    let _ = tx.send(Err((false, e.to_string())));
                    return;
                }
            };
            let (out, mut err) = (proc.stdout.take(), proc.stderr.take());
            *c.lock().unwrap_or_else(|e| e.into_inner()) = Some(proc);
            if let Some(out) = out {
                for line in BufReader::new(out).lines().map_while(Result::ok) {
                    if let Some(p) = progress_of(&line, duration) {
                        let _ = tx.send(Ok(p));
                    }
                }
            }
            let mut text = String::new();
            if let Some(e) = err.as_mut() {
                let _ = e.read_to_string(&mut text);
            }
            let code = c.lock().unwrap_or_else(|e| e.into_inner()).as_mut().and_then(|p| p.wait().ok()).map(|s| s.success()).unwrap_or(false);
            let ok = code && !cx.load(Ordering::Acquire) && dst.exists();
            let _ = tx.send(Err((ok, text.trim().lines().last().unwrap_or("").to_string())));
        });
        let cx = cancelled.clone();
        let done = std::cell::RefCell::new(Some(on_done));
        glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
            while let Ok(m) = rx.try_recv() {
                match m {
                    Ok(p) => on_progress(p),
                    Err((ok, message)) => {
                        if !ok {
                            let _ = std::fs::remove_file(&out); // nothing half-made left beside the original
                        }
                        if let Some(f) = done.take() {
                            if !cx.load(Ordering::Acquire) {
                                f(ok, message);
                            }
                        }
                        return glib::ControlFlow::Break;
                    }
                }
            }
            glib::ControlFlow::Continue
        });
        Self { child, cancelled }
    }

    pub fn cancel(&self) {
        self.cancelled.store(true, std::sync::atomic::Ordering::Release);
        if let Some(p) = self.child.lock().unwrap_or_else(|e| e.into_inner()).as_mut() {
            let _ = p.kill();
        }
    }
}


/// The middle of each of `count` equal parts of the movie.
pub fn thumbnail_times(duration: f64, count: usize) -> Vec<f64> {
    let count = count.max(1);
    (0..count).map(|i| duration * (i as f64 + 0.5) / count as f64).collect()
}

/// The filmstrip's frames, one by one in the background: on_thumb(i, png)
/// on the main loop. stop() true: no more.
pub fn thumbnails(path: &Path, duration: f64, count: usize, height: i32, on_thumb: impl Fn(usize, Vec<u8>) + 'static, stop: impl Fn() -> bool + 'static) {
    let (tx, rx) = std::sync::mpsc::channel::<(usize, Vec<u8>)>();
    let path = path.to_path_buf();
    let quit = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let q = quit.clone();
    std::thread::spawn(move || {
        for (i, t) in thumbnail_times(duration, count).into_iter().enumerate() {
            if q.load(std::sync::atomic::Ordering::Acquire) {
                return;
            }
            let out = Command::new("ffmpeg").args(["-hide_banner", "-nostdin", "-loglevel", "error", "-ss", &format!("{t:.3}"), "-i"]).arg(&path).args(["-frames:v", "1", "-vf", &format!("scale=-2:{}", height * 2), "-f", "image2pipe", "-vcodec", "png", "-"]).stdin(Stdio::null()).stderr(Stdio::null()).output();
            match out {
                Ok(o) if !o.stdout.is_empty() => {
                    if tx.send((i, o.stdout)).is_err() {
                        return;
                    }
                }
                Ok(_) => {}
                Err(_) => return,
            }
        }
    });
    glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
        if stop() {
            quit.store(true, std::sync::atomic::Ordering::Release);
            return glib::ControlFlow::Break;
        }
        loop {
            match rx.try_recv() {
                Ok((i, png)) => on_thumb(i, png),
                Err(std::sync::mpsc::TryRecvError::Empty) => return glib::ControlFlow::Continue,
                Err(std::sync::mpsc::TryRecvError::Disconnected) => return glib::ControlFlow::Break,
            }
        }
    });
}

// -- crop geometry (video pixels) -------------------------------------------------------------
/// Where a movie iw x ih shows in a w x h area, letterboxed: (x, y, scale).
pub fn video_rect(iw: i64, ih: i64, w: f64, h: f64) -> (f64, f64, f64) {
    let s = (w / iw.max(1) as f64).min(h / ih.max(1) as f64);
    ((w - iw as f64 * s) / 2.0, (h - ih as f64 * s) / 2.0, s)
}

/// Python's round(): halves to even.
fn pyround(v: f64) -> i64 {
    v.round_ties_even() as i64
}

/// The biggest rectangle of `ratio` (w / h; None: the whole picture), centred.
pub fn ratio_rect(iw: i64, ih: i64, ratio: Option<f64>) -> (i64, i64, i64, i64) {
    let Some(r) = ratio.filter(|r| *r > 0.0) else { return (0, 0, iw, ih) };
    let (mut w, mut h) = (iw as f64, iw as f64 / r);
    if h > ih as f64 {
        w = ih as f64 * r;
        h = ih as f64;
    }
    let (w, h) = (pyround(w), pyround(h));
    ((iw - w).div_euclid(2), (ih - h).div_euclid(2), w, h)
}

/// The crop `rect` with `part` dragged by (dx, dy): "move", an edge ("l",
/// "r", "t", "b") or a corner ("tl", "tr", "bl", "br"). Stays inside the
/// picture, at least MIN_CROP, and keeps `ratio` when set (the corner or
/// edge opposite the one dragged stays put).
pub fn drag_crop(rect: (i64, i64, i64, i64), part: &str, dx: f64, dy: f64, iw: i64, ih: i64, ratio: Option<f64>) -> (i64, i64, i64, i64) {
    let (x, y, w, h) = (rect.0 as f64, rect.1 as f64, rect.2 as f64, rect.3 as f64);
    let (iwf, ihf) = (iw as f64, ih as f64);
    if part == "move" {
        return ((x + dx).min(iwf - w).max(0.0) as i64, (y + dy).min(ihf - h).max(0.0) as i64, rect.2, rect.3);
    }
    let (mut left, mut top, mut right, mut bottom) = (x, y, x + w, y + h);
    if part.contains('l') {
        left = (left + dx).max(0.0).min(right - MIN_CROP);
    }
    if part.contains('r') {
        right = (right + dx).min(iwf).max(left + MIN_CROP);
    }
    if part.contains('t') {
        top = (top + dy).max(0.0).min(bottom - MIN_CROP);
    }
    if part.contains('b') {
        bottom = (bottom + dy).min(ihf).max(top + MIN_CROP);
    }
    if let Some(ratio) = ratio {
        let (mut nw, mut nh) = (right - left, bottom - top);
        if part == "t" || part == "b" {
            nw = nh * ratio;
        } else {
            nh = nw / ratio; // an edge l / r, or a corner: the width leads
        }
        // the space there is from the corner / edge that stays
        let ax = if part.contains('l') { right } else { left };
        let ay = if part.contains('t') { bottom } else { top };
        let room_w = if part.contains('l') { ax } else if part != "t" && part != "b" { iwf - ax } else { (x + w / 2.0).min(iwf - x - w / 2.0) * 2.0 };
        let room_h = if part.contains('t') { ay } else if part != "l" && part != "r" { ihf - ay } else { (y + h / 2.0).min(ihf - y - h / 2.0) * 2.0 };
        let k = 1.0f64.min(room_w / nw.max(1.0)).min(room_h / nh.max(1.0));
        nw = MIN_CROP.max(nw * k);
        nh = (if ratio < 1.0 { MIN_CROP / ratio } else { MIN_CROP }).max(nh * k);
        left = if part == "t" || part == "b" { x + w / 2.0 - nw / 2.0 } else if part.contains('l') { ax - nw } else { ax };
        top = if part == "l" || part == "r" { y + h / 2.0 - nh / 2.0 } else if part.contains('t') { ay - nh } else { ay };
        right = left + nw;
        bottom = top + nh;
    }
    let (left, top) = (left.max(0.0), top.max(0.0));
    let (right, bottom) = (right.min(iwf), bottom.min(ihf));
    (pyround(left), pyround(top), pyround(right - left), pyround(bottom - top))
}

/// What a press at (px, py) (video pixels) takes: a corner, an edge,
/// "move" inside, or "" outside. grab: how close counts, in video pixels.
pub fn crop_part(rect: (i64, i64, i64, i64), px: f64, py: f64, grab: f64) -> String {
    let (x, y, w, h) = (rect.0 as f64, rect.1 as f64, rect.2 as f64, rect.3 as f64);
    if !(x - grab <= px && px <= x + w + grab && y - grab <= py && py <= y + h + grab) {
        return String::new();
    }
    let (l, r) = ((px - x).abs() <= grab, (px - (x + w)).abs() <= grab);
    let (t, b) = ((py - y).abs() <= grab, (py - (y + h)).abs() <= grab);
    let v = if t { "t" } else if b { "b" } else { "" };
    let hz = if l { "l" } else if r { "r" } else { "" };
    let part = format!("{v}{hz}");
    if part.is_empty() { "move".into() } else { part }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn output_beside_never_over() {
        let d = tempfile::tempdir().unwrap();
        let src = d.path().join("Holiday.MOV");
        std::fs::write(&src, "").unwrap();
        assert_eq!(output_path(&src, "Trimmed", false), d.path().join("Holiday (Trimmed).mov"));
        std::fs::write(d.path().join("Holiday (Trimmed).mov"), "").unwrap();
        assert_eq!(output_path(&src, "Trimmed", false), d.path().join("Holiday (Trimmed 2).mov"));
        assert!(output_path(&d.path().join("a.webm"), "Trimmed", false).to_string_lossy().ends_with("a (Trimmed).mp4"));
        assert!(output_path(&d.path().join("s.mp3"), "Trimmed", true).to_string_lossy().ends_with(".mp3"));
        assert!(output_path(&d.path().join("s.wma"), "Trimmed", true).to_string_lossy().ends_with(".m4a"));
    }

    #[test]
    fn commands() {
        let cmd = trim_command(Path::new("/a.mp4"), Path::new("/b.mp4"), 1.5, 4.25, false);
        assert_eq!(cmd.last().unwrap(), "/b.mp4");
        let i = cmd.iter().position(|c| c == "-i").unwrap();
        assert_eq!(&cmd[i + 1..i + 6], ["/a.mp4", "-ss", "1.500", "-to", "4.250"]); // exact: after -i
        assert!(cmd.contains(&"libx264".to_string()));
        let song = trim_command(Path::new("/a.mp3"), Path::new("/b.mp3"), 0.0, 1.0, true);
        assert!(song.contains(&"libmp3lame".to_string()) && song.contains(&"-vn".to_string()));
        let crop = crop_command(Path::new("/a.mp4"), Path::new("/c.mp4"), 10, 20, 101, 57);
        assert!(crop.contains(&"crop=100:56:10:20".to_string())); // even sizes
    }

    #[test]
    fn range_and_progress() {
        assert_eq!(clamp_range(-3.0, 200.0, 60.0), (0.0, 60.0));
        assert_eq!(clamp_range(30.0, 30.1, 60.0), (30.0, 30.0 + MIN_LENGTH));
        assert_eq!(progress_of("out_time_us=15000000", 30.0), Some(0.5));
        assert_eq!(progress_of("out_time_ms=90000000", 30.0), Some(1.0));
        assert_eq!(progress_of("speed=1x", 30.0), None);
        assert_eq!(progress_of("out_time_us=N/A", 30.0), None);
        assert_eq!(thumbnail_times(10.0, 5), [1.0, 3.0, 5.0, 7.0, 9.0]);
    }

    #[test]
    fn letterbox_and_shapes() {
        assert_eq!(video_rect(1920, 800, 960.0, 800.0), (0.0, 200.0, 0.5));
        assert_eq!(ratio_rect(1920, 1080, Some(1.0)), (420, 0, 1080, 1080));
        assert_eq!(ratio_rect(1920, 1080, None), (0, 0, 1920, 1080));
        let (_, _, w, h) = ratio_rect(1920, 1080, Some(9.0 / 16.0));
        assert!((w as f64 / h as f64 - 9.0 / 16.0).abs() < 0.005);
    }

    #[test]
    fn drags_stay_inside_and_keep_the_shape() {
        let r = (100, 100, 800, 450);
        assert_eq!(drag_crop(r, "move", 5000.0, -50.0, 1920, 1080, None), (1120, 50, 800, 450));
        assert_eq!(drag_crop(r, "r", 5000.0, 0.0, 1920, 1080, None), (100, 100, 1820, 450));
        assert_eq!(drag_crop(r, "l", 5000.0, 0.0, 1920, 1080, None).2, MIN_CROP as i64); // never under the minimum
        for part in ["l", "r", "t", "b", "tl", "tr", "bl", "br", "move"] {
            for (dx, dy) in [(-3000.0, -3000.0), (3000.0, 3000.0), (40.0, -25.0)] {
                for ratio in [None, Some(16.0 / 9.0), Some(1.0), Some(9.0 / 16.0)] {
                    let (x, y, w, h) = drag_crop(r, part, dx, dy, 1920, 1080, ratio);
                    assert!(x >= 0 && y >= 0 && x + w <= 1920 && y + h <= 1080, "{part} {dx} {ratio:?}");
                }
            }
        }
        // tests/test_videos_crop.py: the shape kept, the corner opposite stays
        for part in ["tl", "br", "b", "l"] {
            let (x, y, w, h) = drag_crop(r, part, -700.0, 400.0, 1920, 1080, Some(16.0 / 9.0));
            assert!((w as f64 / h as f64 - 16.0 / 9.0).abs() <= 0.02, "{part}");
            assert!(x >= 0 && y >= 0 && x + w <= 1920 && y + h <= 1080, "{part}");
        }
        let (x, y, _, _) = drag_crop(r, "br", 100.0, 0.0, 1920, 1080, Some(16.0 / 9.0));
        assert_eq!((x, y), (100, 100));
        assert_eq!(crop_part(r, 101.0, 300.0, 10.0), "l");
        assert_eq!(crop_part(r, 899.0, 549.0, 10.0), "br");
        assert_eq!(crop_part(r, 10.0, 10.0, 10.0), "");
        assert_eq!(crop_part(r, 100.0, 100.0, 10.0), "tl");
        assert_eq!(crop_part(r, 500.0, 300.0, 10.0), "move");
        assert_eq!(crop_part(r, 900.0, 300.0, 10.0), "r");
        assert_eq!(crop_part(r, 5.0, 5.0, 10.0), "");
    }
}
