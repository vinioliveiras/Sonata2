//! Interface sound effects (sonata2/sounds.py): Move to Trash, a copy
//! finished, a screenshot, volume feedback... Settings > Sound turns them
//! off (sounds.json). Played with the first player found, as an event
//! sound named Sonata (never an app in the sound mixer); non-blocking.

use serde_json::json;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Instant;

static LAST: Mutex<Vec<(String, Instant)>> = Mutex::new(Vec::new());

pub fn defaults() -> serde_json::Value {
    json!({"effects": true, "volume_feedback": true, "follow_new_devices": true, "lock_volumes": false})
}

fn file_of(event: &str) -> &str {
    match event {
        "trash" | "empty-trash" => "trash-empty",
        "done" => "complete",
        "screenshot" => "camera-shutter",
        "volume" => "audio-volume-change",
        "error" => "dialog-warning",
        "plug" => "device-added",
        "unplug" => "device-removed",
        e => e,
    }
}

/// Where Sonata's sounds are: `$SONATA2_SOUNDS_DIR`; next to the installed
/// binary (`<prefix>/share/sonata2-rs/data/sounds`); else this clone's.
pub fn sounds_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("SONATA2_SOUNDS_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(d);
    }
    if let Some(prefix) = std::env::current_exe().ok().and_then(|e| e.parent()?.parent().map(PathBuf::from)) {
        let d = prefix.join("share").join("sonata2-rs").join("data").join("sounds");
        if d.is_dir() {
            return d;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../data/sounds")
}

pub fn enabled(event: &str) -> bool {
    let cfg = sonata_config::load("sounds", &defaults());
    let key = if event == "volume" { "volume_feedback" } else { "effects" };
    cfg.get(key).and_then(|v| v.as_bool()).unwrap_or(true)
}

pub fn player(path: &str) -> Option<Vec<String>> {
    let cands: [Vec<&str>; 4] = [
        vec!["pw-play", "--media-role", "Event", "-P", "{ application.name = \"Sonata\" media.role = \"Event\" }", path],
        vec!["paplay", "--client-name=Sonata", "--property=media.role=event", path],
        vec!["canberra-gtk-play", "-f", path],
        vec!["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
    ];
    cands.into_iter().find(|c| crate::run::which(c[0]).is_some()).map(|c| c.into_iter().map(String::from).collect())
}

/// play() shortly, on the main loop: after a slider is let go, so the new
/// volume is in place when the sound plays (sounds.play_soon).
pub fn play_soon(event: &str) {
    let event = event.to_string();
    glib::timeout_add_local_once(std::time::Duration::from_millis(120), move || play(&event));
}

pub fn play(event: &str) {
    if !enabled(event) {
        return;
    }
    {
        // a burst (20 files trashed) plays once
        let mut last = LAST.lock().unwrap_or_else(|e| e.into_inner());
        let now = Instant::now();
        if last.iter().any(|(e, t)| e == event && now.duration_since(*t).as_secs_f64() < 0.12) {
            return;
        }
        last.retain(|(e, _)| e != event);
        last.push((event.to_string(), now));
    }
    let path = sounds_dir().join(format!("{}.oga", file_of(event)));
    if path.exists() {
        if let Some(cmd) = player(&path.display().to_string()) {
            let args: Vec<&str> = cmd.iter().map(String::as_str).collect();
            crate::run::spawn(&args);
        }
    }
}
