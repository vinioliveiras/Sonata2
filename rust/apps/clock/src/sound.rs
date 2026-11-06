//! An alarm sound (shell/alarmservice.py's Ringer): played once to
//! preview it in Clock, or looped, getting louder, while the menu bar
//! rings an alarm (sonata-topbar's alarms.rs).

use crate::alarms::{SOUND, SOUNDS};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};

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

/// The first plays, softer; then full volume (a gentle wake-up).
pub const RAMP: [f64; 4] = [0.3, 0.45, 0.6, 0.8];

fn play(v: f64, player: &str) -> String {
    if player == "pw-play" {
        // Python's text of a float: 1.0, not 1
        let v = if v.fract() == 0.0 { format!("{v:.1}") } else { v.to_string() };
        format!("pw-play --volume {v} \"$0\"")
    } else {
        format!("paplay --volume {} \"$0\"", (v * 65536.0) as i64)
    }
}

/// sh -c: play the sound getting louder, then on and on (loop) -- or once
/// (a preview). alarmservice.ring_command's text.
pub fn ring_command(path: &str, player: &str, looped: bool) -> Vec<String> {
    let script = if looped {
        let ramp = RAMP.iter().map(|v| format!("{}; sleep 0.6", play(*v, player))).collect::<Vec<_>>().join("; ");
        format!("{ramp}; while :; do {}; sleep 0.6; done", play(1.0, player))
    } else {
        play(0.8, player)
    };
    vec!["sh".into(), "-c".into(), script, path.into()]
}

/// What plays the sound once (a preview).
pub fn preview_command(path: &str, player: &str) -> Vec<String> {
    ring_command(path, player, false)
}

#[derive(Default)]
pub struct Ringer {
    child: Option<Child>,
}

impl Ringer {
    /// Plays it once (Clock's preview).
    pub fn start(&mut self, key: &str) {
        self.play(key, false);
    }

    /// Plays it on and on, until stop() (an alarm ringing).
    pub fn ring(&mut self, key: &str) {
        self.play(key, true);
    }

    pub fn ringing(&mut self) -> bool {
        self.child.as_mut().is_some_and(|c| matches!(c.try_wait(), Ok(None)))
    }

    fn play(&mut self, key: &str, looped: bool) {
        self.stop();
        let path = sound_path(key);
        let Some(player) = ["pw-play", "paplay"].into_iter().find(|p| which(p)) else { return };
        if !path.exists() {
            return;
        }
        let cmd = ring_command(&path.display().to_string(), player, looped);
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
            // waited for in a thread: try_wait right after the signal came
            // too soon, and the player stayed a zombie
            std::thread::spawn(move || {
                let _ = c.wait();
            });
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

    /// True once `pid` is gone, false while it is a zombie or still runs
    /// (waits up to 3 s).
    fn reaped(pid: u32) -> bool {
        let end = std::time::Instant::now() + std::time::Duration::from_secs(3);
        while std::time::Instant::now() < end {
            if !std::path::Path::new(&format!("/proc/{pid}")).exists() {
                return true;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        false
    }

    #[test]
    fn test_stopped_sound_stayed_a_zombie() {
        //! A sound stopped while playing stayed a defunct player for as
        //! long as the app (the menu bar's alarms) ran.
        let child = Command::new("sleep").arg("30").process_group(0).spawn().unwrap();
        let pid = child.id();
        let mut r = Ringer { child: Some(child) };
        r.stop();
        assert!(reaped(pid), "the stopped player is reaped");
    }

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

    #[test]
    fn test_ramp_then_loop() {
        let cmd = ring_command("/s.oga", "pw-play", true);
        let first = format!("--volume {}", RAMP[0]);
        assert!(cmd[2].contains(&first));
        assert!(cmd[2].contains("while :"));
        assert!(cmd[2].find(&first).unwrap() < cmd[2].find("while :").unwrap());
        assert!(cmd[2].starts_with("pw-play --volume 0.3 \"$0\"; sleep 0.6; pw-play --volume 0.45"));
        assert!(cmd[2].ends_with("while :; do pw-play --volume 1.0 \"$0\"; sleep 0.6; done"));
        let once = ring_command("/s.oga", "paplay", false);
        assert!(!once[2].contains("while"));
        assert!(once[2].contains("paplay --volume"));
    }
}
