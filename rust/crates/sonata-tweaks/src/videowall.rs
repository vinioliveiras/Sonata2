//! Videos as wallpapers (sonata2/videowall.py). The poster making lives in
//! sonata-shell (the lock and login screens use it); this adds what
//! Settings needs on top: `still`, `duration`, `forget_others`.

use std::path::{Path, PathBuf};

pub use sonata_shell::wallpaper::{brightness, is_video, poster, poster_path, EXTENSIONS};

const TIMEOUT_S: u64 = 20;

/// A picture for `path`: its poster for a video, the path itself otherwise.
pub fn still(path: &Path) -> Option<PathBuf> {
    if is_video(&path.display().to_string()) { poster(path) } else { Some(path.to_path_buf()) }
}

/// The video's length in seconds (ffprobe), 0 when unknown.
pub fn duration(video: &Path) -> f64 {
    let v = video.display().to_string();
    let (rc, out) = sonata_backend::run::run(&["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", &v], TIMEOUT_S);
    if rc != 0 {
        return 0.0;
    }
    out.trim().parse::<f64>().map(|d| d.max(0.0)).unwrap_or(0.0)
}

/// Drop posters of videos no longer used (a few MB each), keeping `keep`'s.
pub fn forget_others(keep: &[&Path]) {
    let Some(folder) = poster_path(Path::new("x")).parent().map(Path::to_path_buf) else { return };
    let keep: Vec<_> = keep.iter().filter_map(|v| poster_path(v).file_name().map(|n| n.to_os_string())).collect();
    let Ok(rd) = std::fs::read_dir(&folder) else { return };
    for e in rd.flatten() {
        let name = e.file_name();
        if name.to_string_lossy().ends_with(".jpg") && !keep.contains(&name) {
            let _ = std::fs::remove_file(e.path());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn still_of_a_picture_is_itself() {
        assert_eq!(still(Path::new("/x/a.jpg")), Some(PathBuf::from("/x/a.jpg")));
        assert!(is_video("/x/Clip.MOV") && !is_video("/x/a.png"));
        assert_eq!(duration(Path::new("/no/such/video.mp4")), 0.0);
    }
}
