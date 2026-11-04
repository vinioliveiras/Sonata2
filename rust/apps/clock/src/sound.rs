//! An alarm sound played once, to preview it (shell/alarmservice.py's
//! Ringer, its preview half: ringing is the menu bar's).

use crate::alarms::{SOUND, SOUNDS};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};

/// Where Sonata's sounds are: `$SONATA2_SOUNDS_DIR`; next to the installed
/// binary (`<prefix>/share/sonata2-rs/sounds`); else this clone's.
pub fn sounds_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("SONATA2_SOUNDS_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(d);
    }
    if let Some(prefix) = std::env::current_exe().ok().and_then(|e| e.parent()?.parent().map(PathBuf::from)) {
        let d = prefix.join("share").join("sonata2-rs").join("sounds");
        if d.is_dir() {
            return d;
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../sonata2/data/sounds")
}

/// The file of an alarm sound (the default one for an unknown key).
pub fn sound_path(key: &str) -> PathBuf {
    let name = SOUNDS.iter().find(|s| s.0 == key).or_else(|| SOUNDS.iter().find(|s| s.0 == SOUND)).map(|s| s.2).unwrap_or("");
    sounds_dir().join(format!("{name}.oga"))
}

fn which(name: &str) -> bool {
    std::env::var_os("PATH")
        .map(|p| std::env::split_paths(&p).any(|d| d.join(name).is_file()))
        .unwrap_or(false)
}

/// What plays the sound once: sh -c (as alarmservice.ring_command, loop off).
pub fn preview_command(path: &str, player: &str) -> Vec<String> {
    let play = if player == "pw-play" { "pw-play --volume 0.8 \"$0\"".to_string() } else { format!("paplay --volume {} \"$0\"", (0.8 * 65536.0) as i64) };
    vec!["sh".into(), "-c".into(), play, path.into()]
}

#[derive(Default)]
pub struct Ringer {
    child: Option<Child>,
}

impl Ringer {
    pub fn start(&mut self, key: &str) {
        self.stop();
        let path = sound_path(key);
        let Some(player) = ["pw-play", "paplay"].into_iter().find(|p| which(p)) else { return };
        if !path.exists() {
            return;
        }
        let cmd = preview_command(&path.display().to_string(), player);
        self.child = Command::new(&cmd[0])
            .args(&cmd[1..])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0) // its own group: stop() ends the player too
            .spawn()
            .ok();
    }

    pub fn stop(&mut self) {
        if let Some(mut c) = self.child.take() {
            if matches!(c.try_wait(), Ok(None)) {
                unsafe {
                    libc::kill(-(c.id() as i32), libc::SIGTERM);
                }
            }
            let _ = c.try_wait();
        }
    }
}

impl Drop for Ringer {
    fn drop(&mut self) {
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_sound_exists() {
        for (key, _, _) in SOUNDS {
            assert!(sound_path(key).exists(), "{key}");
        }
        assert_eq!(sound_path("nonsense"), sound_path(SOUND));
    }

    #[test]
    fn preview_plays_once() {
        assert_eq!(preview_command("/x.oga", "pw-play"), ["sh", "-c", "pw-play --volume 0.8 \"$0\"", "/x.oga"]);
        assert_eq!(preview_command("/x.oga", "paplay")[2], "paplay --volume 52428 \"$0\"");
    }
}
