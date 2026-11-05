//! Screenshots and screen recording, macOS Big Sur style
//! (sonata2/shell/capture.py; the menu bar process hosts it).
//!
//! - Thumbnail: after a screenshot, a small picture floats at the bottom
//!   right for a few seconds (click to open it), like macOS.
//! - Capture toolbar (Super+Shift+5): capture or record a display (picked
//!   from a list when there are several), a window (picked from a list) or
//!   a selected portion; Options: where screenshots go (Pictures, Desktop,
//!   Documents, Clipboard, another folder) and where recordings go (Videos,
//!   Desktop, Documents, another folder), each on its own; timer; sound.
//! - Recording (wl-screenrec, else wf-recorder): a small control over the
//!   middle of the recorded display's menu bar shows the time and stops it;
//!   the movie lands in the chosen folder.
//!
//! Tools: grim, slurp, wf-recorder / wl-screenrec, wl-copy (optional;
//! missing ones are reported).
//!
//! `sonata2-rs screenshot` (sonata-session's keys.rs) is the other half:
//! the key bindings call it, it asks the running menu bar's "capture"
//! action ([`install_actions`] here) and only shoots by itself -- grim, a
//! file on the Desktop, a notification -- when no menu bar answers.

use crate::{livedraw, replay::Replay, sharepicker};
use gtk::{gdk, gdk_pixbuf::Pixbuf, gio, glib, prelude::*};
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use serde_json::{json, Map, Value};
use sonata_backend::{run::which, sounds, userdirs, wfipc};
use sonata_i18n::{fmt, gettext, n_, tr};
use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::rc::{Rc, Weak};
use std::time::Duration;

pub type Cfg = Map<String, Value>;

pub const THUMB_MS: u64 = 5000;
/// a clipboard-only screenshot's one reused file
pub const CLIPBOARD_SHOT: &str = "sonata2-clipboard-shot.png";
pub const THUMB_W: i32 = 200;
/// a steady 60 fps, not the display's 144/180 Hz
pub const FPS: u32 = 60;
/// a kept toolbar comes back once the editor has opened
pub const TOOLBAR_BACK_MS: u64 = 450;
/// the capture toolbar opens after the panel that asked for it closed (__main__.py)
pub const TOOLBAR_DELAY_MS: u64 = 300;
pub const RENDER_NODE: &str = "/dev/dri/renderD128";

/// shots_to: pictures | desktop | documents | clipboard | other (shots_dir);
/// movies_to: videos | desktop | documents | other (movies_dir); audio: none
/// | system | mic | both; shots_copy: also on the clipboard when saved to a
/// folder (Vini: both at once); mode: the toolbar's last one (Vini);
/// encoder: the recorder encoder that worked last.
pub fn defaults() -> Value {
    json!({"shots_to": "pictures", "shots_dir": "", "shots_copy": false, "movies_to": "videos", "movies_dir": "",
           "timer": 0, "audio": "system", "mode": "display", "encoder": ""})
}

pub fn load_cfg() -> Cfg {
    sonata_config::load("capture", &defaults())
}

// -- small helpers over Python-written JSON ------------------------------------------------------

/// Python's truthiness, for values another process wrote.
pub(crate) fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn num(v: Option<&Value>) -> f64 {
    v.and_then(Value::as_f64).unwrap_or(0.0)
}

/// cfg[key] as text ("" for anything else, `default` when missing).
pub(crate) fn text(cfg: &Cfg, key: &str, default: &str) -> String {
    match cfg.get(key) {
        None => default.to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(_) => String::new(),
    }
}

fn geometry(o: &Value) -> Value {
    o.get("geometry").filter(|g| g.is_object()).cloned().unwrap_or_else(|| json!({}))
}

// -- folders --------------------------------------------------------------------------------------

/// The folder a capture goes to (made if missing).
pub fn dir_for(place: &str, other: &str) -> PathBuf {
    let home = glib::home_dir();
    let path = if place == "other" && !other.is_empty() {
        PathBuf::from(other)
    } else {
        let kind = match place {
            "documents" => userdirs::UserDirectory::Documents,
            "pictures" => userdirs::UserDirectory::Pictures,
            "videos" => userdirs::UserDirectory::Videos,
            _ => userdirs::UserDirectory::Desktop,
        };
        userdirs::special(kind, true).unwrap_or_else(|| home.clone())
    };
    if std::fs::create_dir_all(&path).is_err() {
        return home;
    }
    path
}

pub fn shots_dir(cfg: &Cfg) -> PathBuf {
    dir_for(&text(cfg, "shots_to", "pictures"), &text(cfg, "shots_dir", ""))
}

pub fn movies_dir(cfg: &Cfg) -> PathBuf {
    dir_for(&text(cfg, "movies_to", "videos"), &text(cfg, "movies_dir", ""))
}

// -- displays and windows (Wayfire) -------------------------------------------------------------

/// The capture options for a shortcut: as saved, but never waiting for
/// the timer (gamebar.py's _cfg).
pub fn shortcut_cfg() -> Cfg {
    for_shortcut(load_cfg())
}

pub fn for_shortcut(mut cfg: Cfg) -> Cfg {
    cfg.insert("timer".into(), json!(0));
    cfg
}

/// The app in front, from Wayfire's windows: (its app id, its title) --
/// the focused app window; None on the desktop (nothing focused). The
/// Game Bar names the game by it.
pub fn front_app(views: &[Value]) -> Option<(String, String)> {
    let v = views.iter().find(|v| {
        let role = v.get("role").and_then(Value::as_str).unwrap_or("toplevel");
        role == "toplevel" && truthy(v.get("activated")) && !truthy(v.get("minimized"))
    })?;
    let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let app = s("app-id");
    (!app.is_empty()).then(|| (app, s("title")))
}

/// The display with the focus (Wayfire); None: let the recorder decide.
/// Without -o, wf-recorder asks on the terminal when there are two
/// displays, and quits (no terminal).
pub fn focused_output() -> Option<String> {
    wfipc::focused_output()
}

/// [{"id", "name", "geometry"}] of the displays (Wayfire).
pub fn outputs() -> Vec<Value> {
    match wfipc::call("window-rules/list-outputs", json!({})) {
        Some(Value::Array(a)) => a.into_iter().filter(Value::is_object).collect(),
        _ => Vec::new(),
    }
}

pub fn views() -> Vec<Value> {
    match wfipc::call("window-rules/list-views", json!({})) {
        Some(Value::Array(a)) => a,
        _ => Vec::new(),
    }
}

/// A window one can pick: "x,y wxh" in layout coordinates, its title and app id.
#[derive(Clone, Debug, PartialEq)]
pub struct Win {
    pub geo: String,
    pub title: String,
    pub app: String,
}

/// The windows one can pick: mapped app windows on the shown workspace,
/// front ones first.
pub fn window_list(views: &[Value], outs: &[Value]) -> Vec<Win> {
    let place = |id: Option<&Value>| outs.iter().find(|o| o.get("id") == id).map(geometry);
    let mut vs: Vec<&Value> = views.iter().filter(|v| v.is_object()).collect();
    vs.sort_by(|a, b| num(b.get("last-focus-timestamp")).partial_cmp(&num(a.get("last-focus-timestamp"))).unwrap_or(std::cmp::Ordering::Equal));
    let mut boxes = Vec::new();
    for v in vs {
        let role = v.get("role").map_or("toplevel".into(), |r| r.as_str().unwrap_or("").to_string());
        let mapped = v.get("mapped").is_none() || truthy(v.get("mapped"));
        let layer = v.get("layer").map_or("workspace".into(), |r| r.as_str().unwrap_or("").to_string());
        if role != "toplevel" || !mapped || truthy(v.get("minimized")) || layer != "workspace" {
            continue;
        }
        let g = geometry(v);
        let (w, h) = (num(g.get("width")), num(g.get("height")));
        let Some(o) = place(v.get("output-id")) else { continue };
        if w <= 0.0 || h <= 0.0 {
            continue;
        }
        // output-local; other workspaces lie outside
        let (x, y) = (num(g.get("x")), num(g.get("y")));
        if x + w <= 0.0 || y + h <= 0.0 || x >= num(o.get("width")) || y >= num(o.get("height")) {
            continue;
        }
        let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        boxes.push(Win {
            geo: format!("{},{} {}x{}", (num(o.get("x")) + x) as i64, (num(o.get("y")) + y) as i64, w as i64, h as i64),
            title: s("title"),
            app: s("app-id"),
        });
    }
    boxes
}

/// "x,y wxh" (layout coordinates) of the windows one can pick.
pub fn window_boxes(views: &[Value], outs: &[Value]) -> Vec<String> {
    window_list(views, outs).into_iter().map(|w| w.geo).collect()
}

fn app_info(app_id: &str) -> Option<gio::DesktopAppInfo> {
    sonata_desktop::appmatch::match_app_id(app_id).and_then(|key| sonata_ui::apps::lookup(&key))
}

/// The app's icon (Dock artwork) for a window's app id.
pub fn app_gicon(app_id: &str) -> Option<gio::Icon> {
    app_info(app_id).map(|i| sonata_ui::icons::app_icon(i.upcast_ref()))
}

pub fn app_name(app_id: &str) -> String {
    match app_info(app_id) {
        Some(i) => sonata_ui::apps::name(&i),
        None if !app_id.is_empty() => app_id.to_string(),
        None => tr!("Window"),
    }
}

/// slurp without blocking the main loop (the frozen screen must draw
/// under it); done("x,y wxh" or None).
fn slurp_async(done: impl FnOnce(Option<String>) + 'static) {
    let proc = match gio::Subprocess::newv(&[std::ffi::OsStr::new("slurp")], gio::SubprocessFlags::STDOUT_PIPE | gio::SubprocessFlags::STDERR_SILENCE) {
        Ok(p) => p,
        Err(_) => return done(None),
    };
    let p = proc.clone();
    proc.communicate_utf8_async(None, gio::Cancellable::NONE, move |res| {
        let out = res.ok().and_then(|(o, _)| o).map(|o| o.trim().to_string()).unwrap_or_default();
        done((p.is_successful() && !out.is_empty()).then_some(out));
    });
}

/// "x,y wxh" -> (x, y, w, h), None when it isn't one.
pub fn parse_box(geo: &str) -> Option<(f64, f64, f64, f64)> {
    let (xy, wh) = geo.split_once(' ')?;
    if wh.contains(' ') {
        return None;
    }
    let pair = |s: &str, sep: char| -> Option<(f64, f64)> {
        let (a, b) = s.split_once(sep)?;
        Some((a.trim().parse().ok()?, b.trim().parse().ok()?))
    };
    let ((x, y), (w, h)) = (pair(xy, ',')?, pair(wh, 'x')?);
    Some((x, y, w, h))
}

/// The display holding a recorded window / portion ("x,y wxh").
pub fn output_at(geo: &str, outs: &[Value]) -> Option<String> {
    let (x, y) = {
        let (a, b) = geo.split(' ').next()?.split_once(',')?;
        (a.parse::<f64>().ok()?.trunc(), b.parse::<f64>().ok()?.trunc())
    };
    outs.iter().find_map(|o| {
        let g = geometry(o);
        let (gx, gy) = (num(g.get("x")), num(g.get("y")));
        (gx <= x && x < gx + num(g.get("width")) && gy <= y && y < gy + num(g.get("height"))).then(|| o.get("name").and_then(Value::as_str).unwrap_or("").to_string())
    })
}

// -- the screen frozen --------------------------------------------------------------------------------

/// Every display as it was when a screenshot started. Picking a portion
/// or a window takes the keyboard, and an open menu (the menu bar's, a
/// right-click one) closed before it was in the picture (Vini): the
/// picture now comes from this, not from the live screen.
pub struct Frozen {
    pub pb: Pixbuf,
    x0: f64,
    y0: f64,
    scale: f64,
    outs: Vec<(String, Value)>,
}

impl Frozen {
    pub fn new(pb: Pixbuf, outs: &[Value]) -> Self {
        let mut geos: Vec<Value> = outs.iter().map(geometry).collect();
        if geos.is_empty() {
            geos.push(json!({"width": pb.width(), "height": pb.height()}));
        }
        let x0 = geos.iter().map(|g| num(g.get("x"))).fold(f64::INFINITY, f64::min);
        let y0 = geos.iter().map(|g| num(g.get("y"))).fold(f64::INFINITY, f64::min);
        let width = geos.iter().map(|g| num(g.get("x")) + num(g.get("width"))).fold(f64::NEG_INFINITY, f64::max) - x0;
        // grim: the largest display scale
        let scale = if width > 0.0 { pb.width() as f64 / width } else { 1.0 };
        let outs = outs.iter().map(|o| (o.get("name").and_then(Value::as_str).unwrap_or("").to_string(), geometry(o))).collect();
        Frozen { pb, x0, y0, scale, outs }
    }

    /// None without grim (or when it fails): the live screen then.
    pub fn take() -> Option<Frozen> {
        which("grim")?;
        let dir = glib::user_runtime_dir();
        let path = dir.join("sonata2-frozen.png");
        // no compression: fast
        let ok = sonata_backend::run::ok(&["grim", "-l", "0", &path.display().to_string()], 5);
        let pb = if ok { Pixbuf::from_file(&path).ok() } else { None };
        let _ = std::fs::remove_file(&path);
        Some(Frozen::new(pb?, &outputs()))
    }

    /// The part "x,y wxh" (layout coordinates), None when it's off screen.
    pub fn crop(&self, geo: &str) -> Option<Pixbuf> {
        let (bx, by, bw, bh) = parse_box(geo)?;
        let s = self.scale;
        // Python's round(): halves to even
        let r = |v: f64| v.round_ties_even() as i32;
        let (x, y) = (r((bx - self.x0) * s).max(0), r((by - self.y0) * s).max(0));
        let x2 = r((bx + bw - self.x0) * s).min(self.pb.width());
        let y2 = r((by + bh - self.y0) * s).min(self.pb.height());
        if x2 <= x || y2 <= y {
            return None;
        }
        self.pb.new_subpixbuf(x, y, x2 - x, y2 - y).copy()
    }

    pub fn output(&self, name: &str) -> Option<Pixbuf> {
        let g = &self.outs.iter().find(|(n, _)| n == name)?.1;
        if g.as_object().is_none_or(|m| m.is_empty()) {
            return None;
        }
        self.crop(&format!("{},{} {}x{}", num(g.get("x")), num(g.get("y")), num(g.get("width")), num(g.get("height"))))
    }
}

/// The frozen screen shown under the portion selection, so what one
/// selects (an open menu too) is what lands in the picture.
struct FrozenCover {
    wins: Vec<gtk::Window>,
}

impl FrozenCover {
    fn new(app: &gtk::Application, frozen: &Frozen) -> Self {
        let mut wins = Vec::new();
        if !sonata_shell::layer::available() {
            return FrozenCover { wins };
        }
        for mon in sonata_shell::layer::monitors() {
            let Some(pb) = frozen.output(&sonata_shell::layer::connector(&mon)) else { continue };
            let w = gtk::Window::builder().application(app).decorated(false).css_classes(["sonata-capture"]).build();
            let pic = gtk::Picture::for_paintable(&gdk::Texture::for_pixbuf(&pb));
            pic.set_content_fit(gtk::ContentFit::Fill);
            pic.set_can_shrink(true);
            w.set_child(Some(&pic));
            w.init_layer_shell();
            w.set_namespace(Some("sonata2-capture-frozen"));
            w.set_layer(Layer::Overlay);
            for e in [Edge::Top, Edge::Bottom, Edge::Left, Edge::Right] {
                w.set_anchor(e, true);
            }
            w.set_exclusive_zone(-1);
            w.set_keyboard_mode(KeyboardMode::None);
            w.set_monitor(Some(&mon));
            w.present();
            wins.push(w);
        }
        FrozenCover { wins }
    }

    fn close(&mut self) {
        for w in self.wins.drain(..) {
            w.destroy();
        }
    }
}

// -- recording: sound, encoders, commands ---------------------------------------------------------

/// PulseAudio / PipeWire name to record: what the speakers play (the
/// default output's monitor) or the default microphone. None: no audio.
pub fn audio_device(kind: &str) -> Option<String> {
    if kind != "system" && kind != "mic" {
        return None;
    }
    let (code, out) = sonata_backend::run::run(&["pactl", if kind == "system" { "get-default-sink" } else { "get-default-source" }], 2);
    device_name(kind, code, &out)
}

/// pactl's answer as a device to record.
pub fn device_name(kind: &str, code: i32, out: &str) -> Option<String> {
    let name = out.trim();
    if code != 0 || name.is_empty() {
        return None;
    }
    Some(if kind == "system" && !name.ends_with(".monitor") { format!("{name}.monitor") } else { name.to_string() })
}

/// H.264 that plays everywhere, easiest on the games first: the GPU's own
/// encoder (NVIDIA NVENC, then VA-API on AMD / Intel), else x264 on the
/// CPU. One that fails to start is skipped (Capture tries the next).
pub fn encoder_args(encoder: &str) -> Vec<&'static str> {
    match encoder {
        "nvenc" => vec!["-c", "h264_nvenc", "-x", "nv12", "-p", "preset=p2", "-p", "rc=vbr", "-p", "cq=23"],
        "vaapi" => vec!["-c", "h264_vaapi", "-d", RENDER_NODE],
        _ => vec!["-c", "libx264", "-x", "yuv420p", "-p", "preset=superfast", "-p", "crf=23"],
    }
}

/// wl-screenrec first when it's there (Vini: the drawing palette must stay
/// out of recordings): it captures over ext-image-copy-capture --
/// sonata-corners' capture, without Sonata's controls -- where wf-recorder
/// only knows wlr-screencopy (the screen as shown). Its GPU encoder
/// (VA-API), else the CPU.
pub const SCREENREC: [&str; 2] = ["screenrec", "screenrec-sw"];

/// The encoders to try, in order (the one that worked last time first);
/// "screenrec*" are wl-screenrec's, the others wf-recorder's.
pub fn encoders(preferred: Option<&str>) -> Vec<String> {
    encoders_with(preferred, &|n| which(n).is_some(), &|p| Path::new(p).exists())
}

pub fn encoders_with(preferred: Option<&str>, has: &dyn Fn(&str) -> bool, exists: &dyn Fn(&str) -> bool) -> Vec<String> {
    let mut found: Vec<String> = if has("wl-screenrec") { SCREENREC.iter().map(|s| s.to_string()).collect() } else { Vec::new() };
    if has("wf-recorder") || found.is_empty() {
        for (e, ok) in [("nvenc", exists("/dev/nvidia0")), ("vaapi", exists(RENDER_NODE)), ("x264", true)] {
            if ok {
                found.push(e.into());
            }
        }
    }
    if let Some(p) = preferred.filter(|p| found.iter().any(|f| f == p)) {
        // within its own group only: wl-screenrec stays ahead of wf-recorder
        // (a stored "vaapi" -- older settings, or one fallback -- put
        // wf-recorder first, and the drawing palette was in the video again)
        let screenrec = |e: &str| SCREENREC.contains(&e);
        let first = found.iter().find(|e| screenrec(e) == screenrec(p)).cloned().unwrap_or_default();
        let start = found.iter().position(|e| *e == first).unwrap_or(0);
        found.retain(|e| e != p);
        found.insert(start, p.to_string());
    }
    found
}

/// Print Screen's capture, from the toolbar's last mode: the same, as a
/// still (Record a Window -> Capture a Window).
pub fn print_mode(last: &str) -> &str {
    let still = last.strip_prefix("rec-").unwrap_or(last);
    if ["display", "window", "area"].contains(&still) {
        still
    } else {
        "display"
    }
}

fn nice() -> Vec<String> {
    if which("nice").is_some() {
        vec!["nice".into(), "-n".into(), "10".into()]
    } else {
        Vec::new()
    }
}

/// wf-recorder at a constant FPS with a low CPU priority: the game or app
/// being recorded goes first. "screenrec*": wl-screenrec instead.
pub fn recorder_command(path: &str, geo: Option<&str>, output: Option<&str>, audio: Option<&str>, encoder: &str) -> Vec<String> {
    recorder_command_with(nice(), path, geo, output, audio, encoder)
}

pub fn recorder_command_with(nice: Vec<String>, path: &str, geo: Option<&str>, output: Option<&str>, audio: Option<&str>, encoder: &str) -> Vec<String> {
    let mut cmd = nice;
    let s = |v: &[&str]| v.iter().map(|a| a.to_string()).collect::<Vec<_>>();
    if SCREENREC.contains(&encoder) {
        cmd.extend(s(&["wl-screenrec", "-f", path]));
        if encoder == "screenrec-sw" {
            cmd.push("--no-hw".into());
        }
        if let Some(g) = geo.filter(|g| !g.is_empty()) {
            cmd.extend(s(&["-g", g]));
        } else if let Some(o) = output.filter(|o| !o.is_empty()) {
            cmd.extend(s(&["-o", o]));
        }
        if let Some(a) = audio.filter(|a| !a.is_empty()) {
            cmd.extend(s(&["--audio", "--audio-device", a]));
        }
        return cmd;
    }
    cmd.extend(s(&["wf-recorder", "-y", "-f", path, "-r", &FPS.to_string()]));
    cmd.extend(s(&encoder_args(encoder)));
    if let Some(g) = geo.filter(|g| !g.is_empty()) {
        cmd.extend(s(&["-g", g]));
    } else if let Some(o) = output.filter(|o| !o.is_empty()) {
        cmd.extend(s(&["-o", o]));
    }
    if let Some(a) = audio.filter(|a| !a.is_empty()) {
        cmd.push(format!("--audio={a}")); // AAC in the same file
    }
    cmd
}

/// Microphone and the system's sound at once (Vini): separate tracks in
/// the movie. The recorders take one sound device, so the picture is
/// recorded silent and FFmpeg records both devices beside it (one AAC
/// track each). At the end: the picture copied as it is, a first track
/// with both mixed -- the one players play (they play one track: with only
/// the two apart, the microphone went unheard) -- then the system's sound
/// and the microphone apart, for editing. (Track names in the file: not
/// translated, as in Python.)
pub const TRACK_TITLES: [&str; 3] = ["System Sound + Microphone", "System Sound", "Microphone"];

/// FFmpeg recording the system's sound and the microphone as two tracks.
pub fn tracks_command(path: &str, system: &str, mic: &str) -> Vec<String> {
    ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "pulse", "-i", system, "-f", "pulse", "-i", mic, "-map", "0:a", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", path]
        .iter()
        .map(|s| s.to_string())
        .collect()
}

/// The silent movie and its sound in one file: both mixed first (the
/// default track), then each apart -- only the mix is encoded again.
pub fn mux_command(video: &str, audio: &str, out: &str) -> Vec<String> {
    let mut cmd: Vec<String> = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", video, "-i", audio,
        "-filter_complex", "[1:a:0][1:a:1]amix=inputs=2:duration=longest:normalize=0[mix]",
        "-map", "0:v", "-map", "[mix]", "-map", "1:a:0", "-map", "1:a:1",
        "-c:v", "copy", "-c:a:0", "aac", "-b:a:0", "192k", "-c:a:1", "copy", "-c:a:2", "copy",
        "-disposition:a:0", "default", "-disposition:a:1", "0", "-disposition:a:2", "0",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    // (MP4 names a track by its handler)
    for (i, t) in TRACK_TITLES.iter().enumerate() {
        cmd.extend([format!("-metadata:s:a:{i}"), format!("title={t}"), format!("-metadata:s:a:{i}"), format!("handler_name={t}")]);
    }
    cmd.extend(["-movflags".into(), "+faststart".into(), out.into()]);
    cmd
}

/// "dir/.name<ext>" beside `path` (its extension dropped).
pub fn hidden_beside(path: &Path, ext: &str) -> PathBuf {
    let stem = path.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    path.with_file_name(format!(".{stem}{ext}"))
}

/// "Screenshot 2026-09-29 at 01.52.10.png" (the names macOS gives).
pub fn file_name(prefix: &str, ext: &str, now: &glib::DateTime) -> String {
    now.format(&format!("{prefix} %Y-%m-%d at %H.%M.%S.{ext}")).map(|s| s.to_string()).unwrap_or_else(|_| format!("{prefix}.{ext}"))
}

pub fn name_now(prefix: &str, ext: &str) -> String {
    let now = glib::DateTime::now_local().or_else(|_| glib::DateTime::now_utc()).expect("a clock");
    file_name(prefix, ext, &now)
}

fn spawn_quiet(cmd: &[String]) -> Option<Child> {
    let (prog, args) = cmd.split_first()?;
    Command::new(prog).args(args).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().ok()
}

pub(crate) fn signal(child: &Child, sig: i32) -> bool {
    // SAFETY: kill(2) on our own child's pid; it can't touch memory
    unsafe { libc::kill(child.id() as i32, sig) == 0 }
}

pub(crate) fn running(child: &mut Child) -> bool {
    matches!(child.try_wait(), Ok(None))
}

fn non_empty(path: &Path) -> bool {
    std::fs::metadata(path).is_ok_and(|m| m.len() > 0)
}

/// The menu bar's height (shell/menubar_size.py): 32 px like the MacBooks
/// with a notch (the default), or 24 px. Ported here, not taken from the
/// menu bar's crate: that one hosts Capture, so it depends on this one.
pub fn menubar_height() -> i32 {
    let cfg = sonata_config::load("topbar", &json!({"tall": true}));
    if cfg.get("tall").is_none() || truthy(cfg.get("tall")) {
        32
    } else {
        24
    }
}

fn logs_path(name: &str) -> PathBuf {
    let dir = sonata_session::logs::log_dir();
    let _ = std::fs::create_dir_all(&dir);
    dir.join(name)
}

fn notify(app: &str, icon: &str, summary: &str, body: &str, desktop: &str) {
    sonata_desktop::notify::send(summary, body, app, icon, desktop, false);
}

// -- what a capture does: decided before anything is shown ----------------------------------------

/// What `run()` does next, once the mode, the displays and the windows are known.
#[derive(Clone, Debug, PartialEq)]
pub enum Step {
    /// captured (or recorded) now -- after the timer when there is one
    Go { geo: Option<String>, output: Option<String> },
    /// a display picked from a list (their names)
    PickDisplay(Vec<String>),
    /// a window picked from a list
    PickWindow(Vec<Win>),
    /// a portion drawn by hand (slurp)
    Area,
    /// nothing to pick from
    Nothing,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Plan {
    pub edit: bool,
    pub rec: bool,
    /// the screen frozen first: before a list or the selection takes the
    /// keyboard (and closes menus)
    pub freeze: bool,
    pub timer: i64,
    pub cfg: Cfg,
    pub step: Step,
}

/// (edit, rec, what) for a mode, and the settings it runs with.
fn target(mode: &str, cfg: &Cfg) -> (bool, bool, String, Cfg) {
    let edit = mode == "edit";
    let mut cfg = cfg.clone();
    let mode = if edit {
        cfg.insert("timer".into(), json!(0));
        print_mode(cfg.get("mode").and_then(Value::as_str).filter(|m| !m.is_empty()).unwrap_or("display")).to_string()
    } else {
        mode.to_string()
    };
    let rec = mode.starts_with("rec-");
    let what = mode.strip_prefix("rec-").unwrap_or(&mode).to_string();
    (edit, rec, what, cfg)
}

/// Whether the plan needs the displays (Wayfire) first.
pub fn needs_outputs(mode: &str, cfg: &Cfg) -> bool {
    let (_, _, what, _) = target(mode, cfg);
    what == "display" || what == "window"
}

/// mode: screen (every display: Super+Shift+3) | display | window | area,
/// edit (Print Screen, see below), or rec-display | rec-window | rec-area
/// (rec-screen: the focused display). A display or a window is picked
/// from a list (thumbnails).
///
/// edit (Vini): Print captures at once and opens the shot in Markup, with
/// what the toolbar used last -- its mode (a recording mode: the same kind
/// of still), folder and clipboard; no timer, like every shortcut. No list
/// to pick from: the display in front, or the window in front; a portion
/// is still drawn by hand.
pub fn plan(mode: &str, cfg: &Cfg, outs: &[Value], views: &dyn Fn() -> Vec<Value>, focused: &dyn Fn() -> Option<String>) -> Plan {
    let (edit, rec, what, cfg) = target(mode, cfg);
    // a picture of a portion / window / display: the screen as it is now,
    // before a list or the selection takes the keyboard (and closes menus).
    // Recording pickers take it too: one grim for every thumbnail.
    let picker = !edit && (what == "window" || (what == "display" && outs.len() > 1));
    let freeze = (!rec && what != "screen") || (rec && picker);
    let timer = cfg.get("timer").and_then(|t| t.as_i64().or_else(|| t.as_f64().map(|f| f as i64))).unwrap_or(0);
    let name = |o: &Value| o.get("name").and_then(Value::as_str).unwrap_or("").to_string();
    let step = match what.as_str() {
        "display" if edit => Step::Go { geo: None, output: focused() },
        // the window in front (front ones first); none: its display
        "window" if edit => match window_list(&views(), outs).into_iter().next() {
            Some(w) => Step::Go { geo: Some(w.geo), output: None },
            None => Step::Go { geo: None, output: focused() },
        },
        "display" if outs.len() <= 1 => Step::Go { geo: None, output: outs.first().map(name).filter(|n| !n.is_empty()).or_else(focused) },
        "display" => Step::PickDisplay(outs.iter().map(name).collect()),
        "window" => {
            let wins = window_list(&views(), outs);
            if wins.is_empty() {
                Step::Nothing
            } else {
                Step::PickWindow(wins)
            }
        }
        "area" => Step::Area,
        // screen: all displays; rec-screen: the focused one
        _ => Step::Go { geo: None, output: if rec { focused() } else { None } },
    };
    Plan { edit, rec, freeze, timer, cfg, step }
}

/// A shot opens in the editor (Print Screen, or the toolbar kept open
/// after it) rather than floating as a thumbnail.
pub fn shot_opens_editor(edit: bool, kept: bool) -> bool {
    edit || kept
}

/// A screenshot in Preview's Markup (draw, crop, save); anything else (a
/// movie) in its default app. False: it could not be opened.
pub fn open_shot(path: &str) -> bool {
    if path.to_lowercase().ends_with(".png") && Path::new(path).is_file() {
        let argv = sonata_session::keep::command_for(&["preview".into(), "--markup".into(), path.into()]);
        return Command::new(&argv[0]).args(&argv[1..]).spawn().is_ok();
    }
    sonata_ui::apps::launch_default_for_uri(&gio::File::for_path(path).uri(), None).is_ok()
}

// -- the floating thumbnail ---------------------------------------------------------------------------

fn layer_ok() -> bool {
    sonata_shell::layer::available()
}

/// The floating screenshot at the bottom right.
pub struct Thumbnail {
    win: gtk::Window,
    pic: gtk::Picture,
    rev: gtk::Revealer,
    path: RefCell<Option<String>>,
    src: RefCell<Option<glib::SourceId>>,
}

impl Thumbnail {
    pub fn new(app: &gtk::Application) -> Rc<Self> {
        let win = gtk::Window::builder().application(app).title("Screenshot").decorated(false).resizable(false).build();
        win.add_css_class("sonata-shot");
        // the shadow needs room: margins inside the transparent window
        let pic = gtk::Picture::builder().css_classes(["shot-thumb"]).margin_start(24).margin_top(24).margin_end(4).margin_bottom(4).overflow(gtk::Overflow::Hidden).build();
        let rev = gtk::Revealer::builder().child(&pic).transition_type(gtk::RevealerTransitionType::SlideLeft).transition_duration(250).build();
        win.set_child(Some(&rev));
        if layer_ok() {
            win.init_layer_shell();
            win.set_namespace(Some("sonata2-screenshot"));
            win.set_layer(Layer::Overlay);
            win.set_anchor(Edge::Bottom, true);
            win.set_anchor(Edge::Right, true);
            win.set_margin(Edge::Bottom, 16);
            win.set_margin(Edge::Right, 16);
            win.set_keyboard_mode(KeyboardMode::None);
        }
        let t = Rc::new(Thumbnail { win, pic, rev, path: RefCell::new(None), src: RefCell::new(None) });
        let click = gtk::GestureClick::new();
        let w = Rc::downgrade(&t);
        click.connect_released(move |_, _, _, _| {
            if let Some(t) = w.upgrade() {
                t.open();
            }
        });
        t.pic.add_controller(click);
        t
    }

    /// path: the picture shown; open_path: what a click opens (a movie).
    pub fn show_shot(self: &Rc<Self>, path: &str, open_path: Option<&str>) {
        *self.path.borrow_mut() = Some(open_path.unwrap_or(path).to_string());
        // a small texture: the window takes the picture's natural size
        let Ok(pb) = Pixbuf::from_file_at_scale(path, THUMB_W, THUMB_W, true) else { return };
        self.pic.set_paintable(Some(&gdk::Texture::for_pixbuf(&pb)));
        self.win.present();
        self.rev.set_reveal_child(true);
        if let Some(src) = self.src.borrow_mut().take() {
            src.remove();
        }
        let w = Rc::downgrade(self);
        let src = glib::timeout_add_local_once(Duration::from_millis(THUMB_MS), move || {
            if let Some(t) = w.upgrade() {
                t.src.borrow_mut().take();
                t.hide();
            }
        });
        *self.src.borrow_mut() = Some(src);
    }

    fn hide(&self) {
        if let Some(src) = self.src.borrow_mut().take() {
            src.remove();
        }
        self.rev.set_reveal_child(false);
        let win = self.win.clone();
        glib::timeout_add_local_once(Duration::from_millis(260), move || win.set_visible(false));
    }

    /// A screenshot: Preview in Markup, to draw on it (Vini); a movie: its player.
    fn open(&self) {
        if let Some(p) = self.path.borrow().clone() {
            open_shot(&p);
        }
        self.hide();
    }
}

// -- the pills over the menu bar ----------------------------------------------------------------------

/// The pills' stop button: a white square on red (the pills' only colour).
pub fn stop_button(tooltip: &str) -> gtk::Button {
    let b = gtk::Button::builder().tooltip_text(tooltip).valign(gtk::Align::Center).build();
    b.set_child(Some(&gtk::Box::builder().css_classes(["stop-glyph"]).halign(gtk::Align::Center).valign(gtk::Align::Center).build()));
    b
}

/// A pill over the middle of the menu bar (macOS' recording / screen
/// sharing controls): put widgets in `hbox` (a .rec-pill), then show_on(output).
pub struct BarPill {
    pub win: gtk::Window,
    pub hbox: gtk::Box,
}

impl BarPill {
    pub fn new(app: Option<&gtk::Application>, title: &str, namespace: &str) -> Self {
        sonata_ui::css::register_module("shell/capture.py");
        let win = gtk::Window::builder().title(title).decorated(false).resizable(false).build();
        if let Some(app) = app {
            win.set_application(Some(app));
        }
        win.add_css_class("sonata-rec");
        let hbox = gtk::Box::builder().spacing(6).css_classes(["rec-pill"]).valign(gtk::Align::Center).build();
        win.set_child(Some(&hbox));
        // as tall as the menu bar, the pill centred in it (Vini: with the taller
        // 32 px bar it sat at the top, its own height under the bar's middle)
        win.set_size_request(-1, menubar_height());
        if layer_ok() {
            win.init_layer_shell();
            win.set_namespace(Some(namespace));
            win.set_layer(Layer::Overlay);
            win.set_anchor(Edge::Top, true);
            win.set_exclusive_zone(-1); // over the menu bar, not below it
            win.set_keyboard_mode(KeyboardMode::None);
        }
        BarPill { win, hbox }
    }

    pub fn show_on(&self, output: Option<&str>) {
        if let Some(out) = output.filter(|o| !o.is_empty()) {
            if self.win.is_layer_window() {
                if let Some(m) = sonata_shell::layer::monitors().into_iter().find(|m| sonata_shell::layer::connector(m) == out) {
                    self.win.set_monitor(Some(&m));
                }
            }
        }
        self.win.present();
    }
}

/// "0:07", "1:02:03".
pub fn elapsed_text(s: i64) -> String {
    if s >= 3600 {
        format!("{}:{:02}:{:02}", s / 3600, s / 60 % 60, s % 60)
    } else {
        format!("{}:{:02}", s / 60, s % 60)
    }
}

/// While recording: a red dot, the time, the sound being recorded and a
/// stop button, over the middle of the recorded display's menu bar.
pub struct RecordingControl {
    pill: BarPill,
    time: gtk::Label,
    sound: gtk::Image,
    output: Rc<RefCell<Option<String>>>,
    src: RefCell<Option<glib::SourceId>>,
    t0: Cell<i64>,
    app: gtk::Application,
}

impl RecordingControl {
    fn new(app: &gtk::Application, owner: Weak<Capture>) -> Rc<Self> {
        let pill = BarPill::new(Some(app), &tr!("Screen Recording"), "sonata2-recording");
        pill.hbox.append(&gtk::Box::builder().css_classes(["rec-dot"]).valign(gtk::Align::Center).build());
        let time = gtk::Label::new(Some("0:00"));
        pill.hbox.append(&time);
        let sound = gtk::Image::builder().css_classes(["rec-sound"]).build();
        pill.hbox.append(&sound);
        // draw on the screen (livedraw.rs)
        let output: Rc<RefCell<Option<String>>> = Rc::default();
        let o = output.clone();
        pill.hbox.append(&livedraw::pen_button(app, Rc::new(move || o.borrow().clone())));
        let stop = stop_button(&tr!("Stop Recording"));
        stop.connect_clicked(move |_| {
            if let Some(c) = owner.upgrade() {
                c.stop_recording();
            }
        });
        pill.hbox.append(&stop);
        Rc::new(RecordingControl { pill, time, sound, output, src: RefCell::new(None), t0: Cell::new(0), app: app.clone() })
    }

    fn start(self: &Rc<Self>, output: Option<String>, audio: bool, kind: &str) {
        self.sound.set_visible(audio);
        self.sound.set_icon_name(Some(if kind == "mic" || kind == "both" { "audio-input-microphone-symbolic" } else { "audio-volume-high-symbolic" }));
        let tip = (kind == "both").then(|| tr!("System sound and microphone: mixed, and each on its own track"));
        self.sound.set_tooltip_text(tip.as_deref());
        *self.output.borrow_mut() = output.clone();
        self.t0.set(glib::monotonic_time());
        self.tick();
        if self.src.borrow().is_none() {
            let w = Rc::downgrade(self);
            let src = glib::timeout_add_local(Duration::from_secs(1), move || match w.upgrade() {
                Some(r) => {
                    r.tick();
                    glib::ControlFlow::Continue
                }
                None => glib::ControlFlow::Break,
            });
            *self.src.borrow_mut() = Some(src);
        }
        self.pill.show_on(output.as_deref());
    }

    fn tick(&self) {
        let s = (glib::monotonic_time() - self.t0.get()) / 1_000_000;
        self.time.set_label(&elapsed_text(s));
    }

    fn stop(&self) {
        if let Some(src) = self.src.borrow_mut().take() {
            src.remove();
        }
        self.pill.win.set_visible(false);
        // the recording ended: the drawing goes too
        livedraw::get(&self.app).stop();
    }
}

// -- Capture: toolbar + recorder + thumbnail; one per menu bar process ------------------------------

#[derive(Default)]
struct RecState {
    geo: Option<String>,
    output: Option<String>,
    audio: Option<String>,
    /// encoders left to try
    encoders: Vec<String>,
    /// the one running (or tried last)
    encoder: Option<String>,
}

/// two sound tracks beside a silent picture (tracks_command)
struct Tracks {
    proc: Option<Child>,
    video: PathBuf,
    audio: PathBuf,
    out: PathBuf,
}

pub struct Capture {
    app: gtk::Application,
    thumb: RefCell<Option<Rc<Thumbnail>>>,
    toolbar: RefCell<Option<Rc<Toolbar>>>,
    /// the recorder while recording
    recorder: RefCell<Option<Child>>,
    /// which recorder a pending start check is about
    rec_serial: Cell<u64>,
    rec_path: RefCell<Option<PathBuf>>,
    rec: RefCell<RecState>,
    pill: RefCell<Option<Rc<RecordingControl>>>,
    /// kept open (Vini): after Print the toolbar stays -- more shots, other
    /// options -- until its close button or Esc; each shot opens in Markup
    keep: Cell<bool>,
    tracks: RefCell<Option<Tracks>>,
    /// instant replay (replay.rs), made when first asked for
    replay: RefCell<Option<Rc<Replay>>>,
    me: Weak<Capture>,
}

impl Capture {
    pub fn new(app: &gtk::Application) -> Rc<Self> {
        sonata_ui::css::register_module("shell/capture.py");
        Rc::new_cyclic(|me| Capture {
            app: app.clone(),
            thumb: RefCell::new(None),
            toolbar: RefCell::new(None),
            recorder: RefCell::new(None),
            rec_serial: Cell::new(0),
            rec_path: RefCell::new(None),
            rec: RefCell::default(),
            pill: RefCell::new(None),
            keep: Cell::new(false),
            tracks: RefCell::new(None),
            replay: RefCell::new(None),
            me: me.clone(),
        })
    }

    pub fn app(&self) -> &gtk::Application {
        &self.app
    }

    /// Recording now (the Game Bar's record button follows it).
    pub fn recording(&self) -> bool {
        self.recorder.borrow().is_some()
    }

    /// When the recording started (glib::monotonic_time, µs); 0 when not
    /// recording. The Game Bar's Stop shows the same time as the pill.
    pub fn recording_since_us(&self) -> i64 {
        if !self.recording() {
            return 0;
        }
        self.pill.borrow().as_ref().map_or(0, |p| p.t0.get())
    }

    /// A screenshot of `output` at once, as the capture options say but
    /// never waiting for the timer (the Game Bar's Screenshot: the game's
    /// display, the Game Bar already gone).
    pub fn shoot_output(&self, output: Option<&str>) {
        self.shoot(None, &shortcut_cfg(), output, None, false);
    }

    /// A recording of `output` at once (the Game Bar's Record).
    pub fn record_output(&self, output: Option<&str>) {
        self.record(None, &shortcut_cfg(), output.map(String::from));
    }

    pub fn replay(&self) -> Rc<Replay> {
        self.replay
            .borrow_mut()
            .get_or_insert_with(|| {
                let me = self.me.clone();
                Replay::new(Box::new(move |clip| {
                    if let Some(c) = me.upgrade() {
                        c.saved(Some(&clip));
                    }
                }))
            })
            .clone()
    }

    /// The instant replay's buffer goes with the process.
    pub fn shutdown(&self) {
        if let Some(r) = self.replay.borrow().as_ref() {
            r.stop();
        }
        if self.recorder.borrow().is_some() {
            self.stop_recording();
        }
    }

    // -- thumbnail --------------------------------------------------------------------------------
    fn shot_taken(&self, path: &str, edit: bool) {
        // Print Screen: straight into Markup, no thumbnail
        if shot_opens_editor(edit, self.keep.get()) {
            open_shot(path);
            self.keep.set(true); // the toolbar stays for the next ones (Vini)
            self.toolbar_back();
            return;
        }
        let thumb = self.thumb.borrow_mut().get_or_insert_with(|| Thumbnail::new(&self.app)).clone();
        thumb.show_shot(path, None);
    }

    // -- toolbar ----------------------------------------------------------------------------------
    pub fn show_toolbar(&self) {
        // Super+Shift+5 while recording: stop (macOS menu)
        if self.recording() {
            self.stop_recording();
            return;
        }
        let tb = self.toolbar.borrow_mut().get_or_insert_with(|| Toolbar::new(&self.app, self.me.clone())).clone();
        tb.restore();
        tb.set_kept(self.keep.get());
        tb.win.present();
    }

    /// Its close button or Esc: gone, and no longer kept.
    pub fn close_toolbar(&self) {
        self.keep.set(false);
        if let Some(tb) = self.toolbar.borrow().as_ref() {
            tb.win.set_visible(false);
        }
    }

    /// Kept: the toolbar again once a shot is done (or a choice
    /// cancelled), after the editor has opened (so it comes above it).
    fn toolbar_back(&self) {
        if self.keep.get() && !self.recording() {
            let me = self.me.clone();
            glib::timeout_add_local_once(Duration::from_millis(TOOLBAR_BACK_MS), move || {
                if let Some(c) = me.upgrade() {
                    if c.keep.get() && !c.recording() {
                        c.show_toolbar();
                    }
                }
            });
        }
    }

    /// mode: screen | area | edit | display | window | rec-display |
    /// rec-window | rec-area | rec-screen (see [`plan`]).
    pub fn run(&self, mode: &str, cfg: Cfg) {
        let shown = self.toolbar.borrow().as_ref().is_some_and(|t| t.win.is_visible());
        if shown {
            if let Some(t) = self.toolbar.borrow().as_ref() {
                t.win.set_visible(false);
            }
        }
        // the toolbar: let it disappear first; a shortcut: at once (an open
        // menu still there). The timer runs once the display, window or
        // portion is chosen
        let (me, mode) = (self.me.clone(), mode.to_string());
        glib::timeout_add_local_once(Duration::from_millis(if shown { 250 } else { 0 }), move || {
            if let Some(c) = me.upgrade() {
                c.run_now(&mode, &cfg);
            }
        });
    }

    fn run_now(&self, mode: &str, cfg: &Cfg) {
        let outs = if needs_outputs(mode, cfg) { outputs() } else { Vec::new() };
        let p = plan(mode, cfg, &outs, &views, &focused_output);
        let frozen = if p.freeze { Frozen::take().map(Rc::new) } else { None };
        let (me, plan_rc) = (self.me.clone(), Rc::new(p.clone()));
        let fz = frozen.clone();
        // What was chosen, captured -- after the timer when there is one
        // (Vini: the display / window list came after the countdown; it's
        // first now, as on macOS). After a timer the screen is taken then,
        // not the one frozen when choosing.
        let go: Rc<dyn Fn(Option<String>, Option<String>)> = Rc::new(move |geo, output| {
            let Some(c) = me.upgrade() else { return };
            let p = plan_rc.clone();
            if p.timer > 0 {
                let me = c.me.clone();
                glib::timeout_add_local_once(Duration::from_millis(p.timer as u64 * 1000), move || {
                    if let Some(c) = me.upgrade() {
                        c.now(&p, geo, output, None);
                    }
                });
            } else {
                c.now(&p, geo, output, fz.clone());
            }
        });
        let action = if p.rec { tr!("Record") } else { tr!("Capture") };
        match p.step {
            Step::Go { geo, output } => go(geo, output),
            Step::Nothing => {}
            Step::PickDisplay(names) => {
                let items = names
                    .iter()
                    .map(|n| sharepicker::PickItem {
                        name: sharepicker::display_name(n),
                        texture: frozen.as_ref().and_then(|f| sharepicker::fit_texture(f.output(n).as_ref())),
                        icon: "video-display-symbolic".into(),
                        badge: None,
                        value: n.clone(),
                    })
                    .collect();
                let title = if p.rec { tr!("Choose a display to record") } else { tr!("Choose a display to capture") };
                self.pick(&title, items, &action, Box::new(move |out| go(None, Some(out))));
            }
            Step::PickWindow(wins) => {
                let items = wins
                    .iter()
                    .map(|w| sharepicker::PickItem {
                        name: if w.title.is_empty() { app_name(&w.app) } else { w.title.clone() },
                        texture: frozen.as_ref().and_then(|f| sharepicker::fit_texture(f.crop(&w.geo).as_ref())),
                        icon: "window-symbolic".into(),
                        badge: app_gicon(&w.app),
                        value: w.geo.clone(),
                    })
                    .collect();
                let title = if p.rec { tr!("Choose a window to record") } else { tr!("Choose a window to capture") };
                self.pick(&title, items, &action, Box::new(move |geo| go(Some(geo), None)));
            }
            Step::Area => {
                if which("slurp").is_none() {
                    self.missing("slurp");
                    return;
                }
                let cover = Rc::new(RefCell::new(frozen.as_ref().map(|f| FrozenCover::new(&self.app, f))));
                let shown = cover.borrow().as_ref().is_some_and(|c| !c.wins.is_empty());
                let me = self.me.clone();
                let picked = move |geo: Option<String>| {
                    if let Some(c) = cover.borrow_mut().as_mut() {
                        c.close();
                    }
                    match geo {
                        Some(g) => go(Some(g), None),
                        None => {
                            // Esc on the selection: the kept toolbar again
                            if let Some(c) = me.upgrade() {
                                c.toolbar_back();
                            }
                        }
                    }
                };
                // once the frozen screen is up (slurp maps over it)
                glib::timeout_add_local_once(Duration::from_millis(if shown { 60 } else { 0 }), move || slurp_async(picked));
            }
        }
    }

    fn now(&self, p: &Plan, geo: Option<String>, output: Option<String>, still: Option<Rc<Frozen>>) {
        if p.rec {
            self.record(geo, &p.cfg, output);
        } else {
            self.shoot(geo.as_deref(), &p.cfg, output.as_deref(), still.as_deref(), p.edit);
        }
    }

    /// The list to pick from; the choice runs once the list has gone.
    fn pick(&self, title: &str, items: Vec<sharepicker::PickItem>, action: &str, then: Box<dyn Fn(String)>) {
        let me = self.me.clone();
        let picker = sharepicker::Picker::new(
            &self.app,
            title,
            None,
            items,
            action,
            Box::new(move |value| match value {
                // not in the picture
                Some(v) => {
                    glib::timeout_add_local_once(Duration::from_millis(250), move || then(v));
                }
                None => {
                    if let Some(c) = me.upgrade() {
                        c.toolbar_back();
                    }
                }
            }),
        );
        picker.present();
    }

    fn shoot(&self, geo: Option<&str>, cfg: &Cfg, output: Option<&str>, frozen: Option<&Frozen>, edit: bool) {
        if which("grim").is_none() {
            self.missing("grim");
            return;
        }
        let clip_only = text(cfg, "shots_to", "") == "clipboard";
        // in the folder and on the clipboard (Vini)
        let to_clip = clip_only || truthy(cfg.get("shots_copy"));
        // clipboard only: one reused file in the runtime dir (tmpfs, gone
        // at logout), not a new one in /tmp per shot
        let path = if clip_only { glib::user_runtime_dir().join(CLIPBOARD_SHOT) } else { shots_dir(cfg).join(name_now("Screenshot", "png")) };
        let p = path.display().to_string();
        if let Some(f) = frozen {
            let pb = match (geo, output) {
                (Some(g), _) => f.crop(g),
                (None, Some(o)) => f.output(o),
                (None, None) => Some(f.pb.clone()),
            };
            let Some(pb) = pb else { return };
            if pb.savev(&path, "png", &[]).is_err() {
                return;
            }
        } else {
            let mut cmd = Command::new("grim");
            match (geo, output) {
                (Some(g), _) => cmd.args(["-g", g]),
                (None, Some(o)) => cmd.args(["-o", o]),
                _ => &mut cmd,
            };
            if !cmd.arg(&p).status().is_ok_and(|s| s.success()) {
                return;
            }
        }
        sounds::play("screenshot");
        if to_clip && which("wl-copy").is_some() {
            if let Ok(f) = std::fs::File::open(&path) {
                let _ = Command::new("wl-copy").args(["--type", "image/png"]).stdin(f).status();
            }
        }
        self.shot_taken(&p, edit);
    }

    fn record(&self, geo: Option<String>, cfg: &Cfg, output: Option<String>) {
        if which("wf-recorder").is_none() && which("wl-screenrec").is_none() {
            self.missing("wf-recorder");
            return;
        }
        let out = movies_dir(cfg).join(name_now("Screen Recording", "mp4"));
        let output = if geo.is_none() && output.is_none() { focused_output() } else { output };
        let mut kind = text(cfg, "audio", "system");
        let two = kind == "both" && which("ffmpeg").is_some() && audio_device("system").is_some() && audio_device("mic").is_some();
        if kind == "both" && !two {
            kind = "system".into(); // no FFmpeg / no microphone: the system's sound alone
        }
        let audio = if two { None } else { audio_device(&kind) };
        // the picture silent, beside it the two tracks
        *self.rec_path.borrow_mut() = Some(if two { hidden_beside(&out, ".video.mp4") } else { out.clone() });
        let preferred = text(&load_cfg(), "encoder", "");
        *self.rec.borrow_mut() = RecState { geo: geo.clone(), output: output.clone(), audio: audio.clone(), encoders: encoders(Some(&preferred)), encoder: None };
        if !self.spawn() {
            *self.rec_path.borrow_mut() = Some(out);
            return;
        }
        let mut with_sound = audio.is_some();
        if two {
            self.start_tracks(&out);
            with_sound = true;
        }
        let pill = self.pill.borrow_mut().get_or_insert_with(|| RecordingControl::new(&self.app, self.me.clone())).clone();
        let at = output.or_else(|| geo.as_deref().and_then(|g| output_at(g, &outputs())));
        pill.start(at, with_sound, &kind);
    }

    /// Start the recorder with the next encoder to try.
    fn spawn(&self) -> bool {
        let (cmd, retry) = {
            let mut r = self.rec.borrow_mut();
            let retry = r.encoder.is_some();
            if r.encoders.is_empty() {
                return false;
            }
            let encoder = r.encoders.remove(0);
            let path = self.rec_path.borrow().as_ref().map(|p| p.display().to_string()).unwrap_or_default();
            let cmd = recorder_command(&path, r.geo.as_deref(), r.output.as_deref(), r.audio.as_deref(), &encoder);
            r.encoder = Some(encoder);
            (cmd, retry)
        };
        // every try of this recording
        let log = std::fs::OpenOptions::new().create(true).append(retry).write(true).truncate(!retry).open(logs_path("recorder.log"));
        let (out, err) = match log {
            Ok(mut f) => {
                use std::io::Write;
                let _ = writeln!(f, "{}", cmd.join(" "));
                match f.try_clone() {
                    Ok(f2) => (Stdio::from(f), Stdio::from(f2)),
                    Err(_) => (Stdio::from(f), Stdio::null()),
                }
            }
            Err(_) => (Stdio::null(), Stdio::null()),
        };
        let Ok(child) = Command::new(&cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(out).stderr(err).spawn() else {
            *self.recorder.borrow_mut() = None;
            return false;
        };
        *self.recorder.borrow_mut() = Some(child);
        let serial = self.rec_serial.get() + 1;
        self.rec_serial.set(serial);
        let me = self.me.clone();
        glib::timeout_add_local_once(Duration::from_millis(1500), move || {
            if let Some(c) = me.upgrade() {
                c.check_started(serial);
            }
        });
        true
    }

    /// The recorder quitting at once: the next encoder, else say so. One
    /// that runs is remembered for next time.
    fn check_started(&self, serial: u64) {
        if serial != self.rec_serial.get() {
            return;
        }
        let alive = match self.recorder.borrow_mut().as_mut() {
            None => return,
            Some(child) => running(child),
        };
        if alive {
            let enc = self.rec.borrow().encoder.clone().unwrap_or_default();
            if text(&load_cfg(), "encoder", "") != enc {
                // one key: the toolbar's options stay as stored
                let _ = sonata_config::update("capture", &json!({"encoder": enc}));
            }
            return;
        }
        self.recorder.borrow_mut().take();
        if !self.rec.borrow().encoders.is_empty() {
            if let Some(p) = self.rec_path.borrow().as_ref() {
                let _ = std::fs::remove_file(p); // the failed start's empty file
            }
            if self.spawn() {
                return;
            }
        }
        self.stop_tracks(true);
        if let Some(p) = self.pill.borrow().as_ref() {
            p.stop();
        }
        let body = fmt(&tr!("Details in {path}"), &[("path", &logs_path("recorder.log").display().to_string())]);
        notify("Screen Recording", "dialog-warning", &tr!("Screen recording didn't start"), &body, "");
    }

    fn start_tracks(&self, out: &Path) {
        let apath = hidden_beside(out, ".audio.mka");
        let cmd = tracks_command(&apath.display().to_string(), &audio_device("system").unwrap_or_default(), &audio_device("mic").unwrap_or_default());
        let video = self.rec_path.borrow().clone().unwrap_or_default();
        *self.tracks.borrow_mut() = Some(Tracks { proc: spawn_quiet(&cmd), video, audio: apath, out: out.to_path_buf() });
    }

    /// The sound tracks' recording ends (SIGINT: FFmpeg finishes its file).
    /// discard: the recording didn't happen -- its files go.
    fn stop_tracks(&self, discard: bool) -> Option<Tracks> {
        let mut t = self.tracks.borrow_mut().take()?;
        if let Some(p) = t.proc.as_mut() {
            if running(p) {
                signal(p, libc::SIGINT);
            }
        }
        if discard {
            if let Some(p) = t.proc.as_mut() {
                let end = std::time::Instant::now() + Duration::from_secs(3);
                while running(p) && std::time::Instant::now() < end {
                    std::thread::sleep(Duration::from_millis(20));
                }
                if running(p) {
                    let _ = p.kill();
                    let _ = p.wait();
                }
            }
            let _ = std::fs::remove_file(&t.video);
            let _ = std::fs::remove_file(&t.audio);
            return None;
        }
        Some(t)
    }

    /// Picture and the two tracks in one movie, then done(path) -- off the
    /// main loop. Without the tracks (FFmpeg stopped at once): the picture.
    fn mux(t: Tracks, done: Box<dyn Fn(PathBuf)>) {
        let finished = move |ok: bool, t: &Tracks| {
            let mut path = t.out.clone();
            if ok && non_empty(&path) {
                let _ = std::fs::remove_file(&t.video);
                let _ = std::fs::remove_file(&t.audio);
            } else if std::fs::rename(&t.video, &path).is_err() {
                // keep what was recorded: the picture alone
                path = t.video.clone();
            }
            done(path);
        };
        if !non_empty(&t.audio) {
            return finished(false, &t);
        }
        let cmd = mux_command(&t.video.display().to_string(), &t.audio.display().to_string(), &t.out.display().to_string());
        let Some(mut proc) = spawn_quiet(&cmd) else { return finished(false, &t) };
        glib::timeout_add_local(Duration::from_millis(150), move || match proc.try_wait() {
            Ok(None) => glib::ControlFlow::Continue,
            res => {
                finished(res.ok().flatten().is_some_and(|s| s.success()), &t);
                glib::ControlFlow::Break
            }
        });
    }

    /// Stop and finish the movie without blocking the main loop.
    pub fn stop_recording(&self) {
        let Some(mut proc) = self.recorder.borrow_mut().take() else { return };
        self.rec_serial.set(self.rec_serial.get() + 1); // a pending start check: not about this one any more
        let path = self.rec_path.borrow().clone();
        let tracks = self.stop_tracks(false);
        if let Some(p) = self.pill.borrow().as_ref() {
            p.stop();
        }
        signal(&proc, libc::SIGINT); // the recorder finishes the file
        let (me, waited, tracks) = (self.me.clone(), Cell::new(0u32), RefCell::new(tracks));
        glib::timeout_add_local(Duration::from_millis(100), move || {
            if running(&mut proc) {
                waited.set(waited.get() + 100);
                if waited.get() < 8000 {
                    return glib::ControlFlow::Continue;
                }
                let _ = proc.kill();
                let _ = proc.wait();
            }
            if let Some(mut t) = tracks.borrow_mut().take() {
                // two sound tracks: together, then saved
                let me = me.clone();
                glib::timeout_add_local(Duration::from_millis(100), move || {
                    if t.proc.as_mut().is_some_and(running) {
                        return glib::ControlFlow::Continue;
                    }
                    let t = Tracks { proc: t.proc.take(), video: t.video.clone(), audio: t.audio.clone(), out: t.out.clone() };
                    let me = me.clone();
                    Capture::mux(
                        t,
                        Box::new(move |p| {
                            if let Some(c) = me.upgrade() {
                                c.saved(Some(&p));
                                c.toolbar_back();
                            }
                        }),
                    );
                    glib::ControlFlow::Break
                });
                return glib::ControlFlow::Break;
            }
            if let Some(c) = me.upgrade() {
                c.saved(path.as_deref());
                c.toolbar_back(); // kept: back after the recording
            }
            glib::ControlFlow::Break
        });
    }

    /// A movie saved (a recording, a replay clip): its thumbnail and a notification.
    pub fn saved(&self, path: Option<&Path>) {
        let Some(path) = path.filter(|p| non_empty(p)) else { return };
        self.movie_thumbnail(path);
        let name = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        notify("Screen Recording", "media-record", &tr!("Screen Recording saved"), &name, "io.github.vinioliveiras.sonata2.files");
    }

    /// The floating thumbnail for a recording too (a frame from it).
    fn movie_thumbnail(&self, path: &Path) {
        if which("ffmpegthumbnailer").is_none() {
            return;
        }
        let png = glib::tmp_dir().join("sonata2-recording-thumb.png");
        let cmd: Vec<String> = ["ffmpegthumbnailer", "-i", &path.display().to_string(), "-o", &png.display().to_string(), "-s", "400", "-t", "10%"].iter().map(|s| s.to_string()).collect();
        let Some(mut proc) = spawn_quiet(&cmd) else { return };
        let (me, movie) = (self.me.clone(), path.display().to_string());
        glib::timeout_add_local(Duration::from_millis(100), move || match proc.try_wait() {
            Ok(None) => glib::ControlFlow::Continue,
            res => {
                if res.ok().flatten().is_some_and(|s| s.success()) && png.exists() {
                    if let Some(c) = me.upgrade() {
                        let thumb = c.thumb.borrow_mut().get_or_insert_with(|| Thumbnail::new(&c.app)).clone();
                        thumb.show_shot(&png.display().to_string(), Some(&movie));
                    }
                }
                glib::ControlFlow::Break
            }
        });
    }

    fn missing(&self, tool: &str) {
        notify("Screenshot", "dialog-warning", &fmt(&tr!("{tool} is not installed"), &[("tool", tool)]), &fmt(&tr!("Install {tool} to use this capture mode."), &[("tool", tool)]), "");
    }
}

// -- the toolbar ------------------------------------------------------------------------------------------

pub const MODES: [(&str, &str, &str); 6] = [
    ("display", "sonata-capture-screen-symbolic", n_!("Capture a Display")),
    ("window", "sonata-capture-window-symbolic", n_!("Capture a Window")),
    ("area", "sonata-capture-area-symbolic", n_!("Capture Selected Portion")),
    ("rec-display", "sonata-record-screen-symbolic", n_!("Record a Display")),
    ("rec-window", "sonata-record-window-symbolic", n_!("Record a Window")),
    ("rec-area", "sonata-record-area-symbolic", n_!("Record Selected Portion")),
];

/// The mode a stored one opens on (unknown ones: display).
pub fn restored_mode(stored: Option<&Value>) -> &'static str {
    let m = stored.and_then(Value::as_str).unwrap_or("");
    MODES.iter().map(|(k, _, _)| *k).find(|k| *k == m).unwrap_or("display")
}

pub struct Toolbar {
    pub win: gtk::Window,
    owner: Weak<Capture>,
    cfg: RefCell<Cfg>,
    mode: RefCell<String>,
    mode_buttons: Vec<(&'static str, gtk::ToggleButton)>,
    go: gtk::Button,
    me: Weak<Toolbar>,
}

impl Toolbar {
    fn new(app: &gtk::Application, owner: Weak<Capture>) -> Rc<Self> {
        let win = gtk::Window::builder().application(app).title("Screenshot").decorated(false).resizable(false).build();
        win.add_css_class("sonata-capture");
        let bar = gtk::Box::builder().spacing(2).css_classes(["cap-bar"]).build();
        let sep = || gtk::Box::builder().css_classes(["cap-sep"]).build();
        let close = gtk::Button::builder().icon_name("window-close-symbolic").tooltip_text(tr!("Close")).build();
        bar.append(&close);
        bar.append(&sep());
        let mut mode_buttons = Vec::new();
        let mut first: Option<gtk::ToggleButton> = None;
        for (i, (mode, icon, tip)) in MODES.iter().enumerate() {
            if i == 3 {
                bar.append(&sep());
            }
            let b = gtk::ToggleButton::builder().icon_name(*icon).tooltip_text(gettext(tip)).active(*mode == "display").build();
            if let Some(f) = &first {
                b.set_group(Some(f));
            } else {
                first = Some(b.clone());
            }
            bar.append(&b);
            mode_buttons.push((*mode, b));
        }
        bar.append(&sep());
        let opts = gtk::Button::with_label(&tr!("Options"));
        bar.append(&opts);
        let go = gtk::Button::builder().label(tr!("Capture")).css_classes(["cap-go"]).build();
        bar.append(&go);
        win.set_child(Some(&bar));
        let tb = Rc::new_cyclic(|me| Toolbar { win, owner: owner.clone(), cfg: RefCell::new(load_cfg()), mode: RefCell::new("display".into()), mode_buttons, go, me: me.clone() });
        let o = owner.clone();
        close.connect_clicked(move |_| {
            if let Some(c) = o.upgrade() {
                c.close_toolbar();
            }
        });
        for (mode, b) in &tb.mode_buttons {
            let (w, mode) = (Rc::downgrade(&tb), *mode);
            b.connect_toggled(move |b| {
                if let (true, Some(t)) = (b.is_active(), w.upgrade()) {
                    t.set_mode(mode);
                }
            });
        }
        let w = Rc::downgrade(&tb);
        opts.connect_clicked(move |b| {
            if let Some(t) = w.upgrade() {
                t.options(b);
            }
        });
        let w = Rc::downgrade(&tb);
        tb.go.connect_clicked(move |_| {
            if let Some(t) = w.upgrade() {
                t.go();
            }
        });
        let keys = gtk::EventControllerKey::new();
        let w = Rc::downgrade(&tb);
        keys.connect_key_pressed(move |_, key, _, _| {
            let Some(t) = w.upgrade() else { return glib::Propagation::Proceed };
            if key == gdk::Key::Escape {
                if let Some(c) = t.owner.upgrade() {
                    c.close_toolbar();
                }
                glib::Propagation::Stop
            } else if key == gdk::Key::Return {
                t.go();
                glib::Propagation::Stop
            } else {
                glib::Propagation::Proceed
            }
        });
        tb.win.add_controller(keys);
        if layer_ok() {
            let win = &tb.win;
            win.init_layer_shell();
            win.set_namespace(Some("sonata2-capture"));
            win.set_layer(Layer::Overlay);
            win.set_anchor(Edge::Bottom, true);
            win.set_margin(Edge::Bottom, 120);
            win.set_keyboard_mode(KeyboardMode::Exclusive); // Esc / Return at once (macOS)
        }
        tb
    }

    /// Kept open (after Print): it takes the keyboard only when clicked,
    /// so the editor beside it can be typed in; otherwise at once (Esc /
    /// Return, macOS).
    fn set_kept(&self, kept: bool) {
        if self.win.is_layer_window() {
            self.win.set_keyboard_mode(if kept { KeyboardMode::OnDemand } else { KeyboardMode::Exclusive });
        }
    }

    fn set_mode(&self, m: &str) {
        *self.mode.borrow_mut() = m.to_string();
        self.go.set_label(&if m.starts_with("rec") { tr!("Record") } else { tr!("Capture") });
        // opened next time on the same one (Vini)
        if self.cfg.borrow().get("mode").and_then(Value::as_str) != Some(m) {
            self.cfg.borrow_mut().insert("mode".into(), json!(m));
            let _ = sonata_config::update("capture", &json!({"mode": m}));
        }
    }

    /// Each time it opens: the last mode, and the options as saved.
    fn restore(&self) {
        *self.cfg.borrow_mut() = load_cfg();
        let m = restored_mode(self.cfg.borrow().get("mode"));
        if let Some((_, b)) = self.mode_buttons.iter().find(|(k, _)| *k == m) {
            b.set_active(true);
        }
        self.set_mode(m);
    }

    /// One option changed: only its key written (not a stale copy over the saved encoder).
    fn setv(&self, k: &str, v: Value) {
        self.cfg.borrow_mut().insert(k.into(), v.clone());
        let mut m = Map::new();
        m.insert(k.into(), v);
        let _ = sonata_config::update("capture", &Value::Object(m));
    }

    fn places(&self, key: &'static str, first: (&'static str, String), extra: Vec<(&'static str, String)>) -> Vec<Vec<sonata_ui::menu::Item>> {
        use sonata_ui::menu::Item;
        let c = self.cfg.borrow().clone();
        let to = text(&c, &format!("{key}_to"), "");
        let other = if to == "other" { text(&c, &format!("{key}_dir"), "") } else { String::new() };
        let mut opts = vec![first, ("desktop", tr!("Desktop")), ("documents", tr!("Documents"))];
        opts.extend(extra);
        let mut items: Vec<Item> = opts
            .into_iter()
            .map(|(v, label)| {
                let w = self.me.clone();
                Item::check(&label, to == v, move |_| {
                    if let Some(t) = w.upgrade() {
                        t.setv(&format!("{key}_to"), json!(v));
                    }
                })
            })
            .collect();
        let label = if other.is_empty() {
            tr!("Other Folder…")
        } else {
            let base = Path::new(&other).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            fmt(&tr!("Other: {folder}"), &[("folder", &base)])
        };
        let w = self.me.clone();
        items.push(Item::check(&label, !other.is_empty(), move |_| {
            if let Some(t) = w.upgrade() {
                t.pick_folder(key);
            }
        }));
        vec![items]
    }

    fn options(&self, button: &gtk::Button) {
        use sonata_ui::menu::{popup, Item, Options};
        let c = self.cfg.borrow().clone();
        let check = |label: String, on: bool, k: &'static str, v: Value| {
            let w = self.me.clone();
            Item::check(&label, on, move |_| {
                if let Some(t) = w.upgrade() {
                    t.setv(k, v.clone());
                }
            })
        };
        let timer = c.get("timer").and_then(Value::as_i64).unwrap_or(0);
        let audio = text(&c, "audio", "system");
        let clip = text(&c, "shots_to", "") == "clipboard";
        let w = self.me.clone();
        let sections = vec![
            vec![
                Item::submenu(&tr!("Save Screenshots To"), self.places("shots", ("pictures", tr!("Pictures")), vec![("clipboard", tr!("Clipboard"))])),
                // saved to a folder and copied too (Vini); "Clipboard" above is the clipboard alone
                Item::check(&tr!("Also Copy Screenshots to Clipboard"), truthy(c.get("shots_copy")) || clip, move |on| {
                    if let Some(t) = w.upgrade() {
                        t.setv("shots_copy", json!(on));
                    }
                })
                .enabled(!clip),
                Item::submenu(&tr!("Save Recordings To"), self.places("movies", ("videos", tr!("Videos")), Vec::new())),
            ],
            vec![
                check(tr!("Timer: None"), timer == 0, "timer", json!(0)),
                check(tr!("Timer: 5 Seconds"), timer == 5, "timer", json!(5)),
                check(tr!("Timer: 10 Seconds"), timer == 10, "timer", json!(10)),
            ],
            // recordings: sound (macOS: Options > Microphone)
            vec![
                check(tr!("Record Without Sound"), audio == "none", "audio", json!("none")),
                check(tr!("Record System Sound"), audio == "system", "audio", json!("system")),
                check(tr!("Record Microphone"), audio == "mic", "audio", json!("mic")),
                // both at once, each its own track (Vini)
                check(tr!("Record System Sound and Microphone"), audio == "both", "audio", json!("both")),
            ],
        ];
        popup(button, sections, Options::above());
    }

    /// Another folder for screenshots (key "shots") or recordings ("movies").
    fn pick_folder(&self, key: &'static str) {
        self.win.set_visible(false); // the dialog must not sit under the toolbar
        let title = if key == "shots" { tr!("Save Screenshots To") } else { tr!("Save Recordings To") };
        let dialog = gtk::FileDialog::builder().title(title).accept_label(tr!("Choose")).build();
        let cur = text(&self.cfg.borrow(), &format!("{key}_dir"), "");
        if !cur.is_empty() && Path::new(&cur).is_dir() {
            dialog.set_initial_folder(Some(&gio::File::for_path(&cur)));
        }
        let w = self.me.clone();
        dialog.select_folder(None::<&gtk::Window>, gio::Cancellable::NONE, move |res| {
            let Some(t) = w.upgrade() else { return };
            if let Some(path) = res.ok().and_then(|f| f.path()) {
                let p = path.display().to_string();
                t.cfg.borrow_mut().insert(format!("{key}_to"), json!("other"));
                t.cfg.borrow_mut().insert(format!("{key}_dir"), json!(p));
                let mut m = Map::new();
                m.insert(format!("{key}_to"), json!("other"));
                m.insert(format!("{key}_dir"), json!(p));
                let _ = sonata_config::update("capture", &Value::Object(m));
            }
            t.win.present();
        });
    }

    fn go(&self) {
        // run() hides the toolbar and waits for it to go (Vini: Print, then
        // Capture, sometimes did nothing): hidden here first, run() saw no
        // toolbar and started at once -- the selection or the picture while
        // the toolbar still held the keyboard and was still on screen
        let (mode, cfg) = (self.mode.borrow().clone(), self.cfg.borrow().clone());
        if let Some(c) = self.owner.upgrade() {
            c.run(&mode, cfg);
        }
    }
}

/// Screenshot in Launchpad and the Apps Menu (Vini): opens the capture
/// toolbar, the same as Super+Shift+5. (file name, contents) for the
/// writer of Sonata's .desktop files.
pub fn capture_desktop_entry(command: &str) -> (&'static str, String) {
    (
        "sonata2-screenshot.desktop",
        format!(
            "[Desktop Entry]\nType=Application\nName=Screenshot\n\
             Comment=Capture or record the screen, a window or a selection\n\
             Icon=accessories-screenshot\nCategories=Utility;\n\
             Keywords=screenshot;capture;record;screen;recording;print;\n\
             Exec={command} screenshot toolbar\n"
        ),
    )
}

/// The menu bar's actions for capture (__main__.py's run_topbar):
/// "capture" (s: toolbar | stop | screen | area | edit | display | window |
/// rec-*), "draw" (Super+Shift+D) and "replay-save" (Super+Alt+G).
pub fn install_actions(app: &gtk::Application, cap: &Rc<Capture>) {
    let act = gio::SimpleAction::new("capture", Some(glib::VariantTy::STRING));
    let c = Rc::downgrade(cap);
    act.connect_activate(move |_, param| {
        let (Some(cap), Some(what)) = (c.upgrade(), param.and_then(|p| p.get::<String>())) else { return };
        dispatch(&cap, &what);
    });
    app.add_action(&act);
    let act = gio::SimpleAction::new("draw", None);
    let a = app.clone();
    act.connect_activate(move |_, _| livedraw::get(&a).toggle(None));
    app.add_action(&act);
    let act = gio::SimpleAction::new("replay-save", None);
    let c = Rc::downgrade(cap);
    act.connect_activate(move |_, _| {
        if let Some(cap) = c.upgrade() {
            let rp = cap.replay();
            if rp.running() {
                rp.save();
            }
        }
    });
    app.add_action(&act);
    // the instant replay's buffer goes with the process (replay.rs)
    let c = Rc::downgrade(cap);
    app.connect_shutdown(move |_| {
        if let Some(cap) = c.upgrade() {
            cap.shutdown();
        }
    });
}

/// One "capture" request (the action's parameter).
pub fn dispatch(cap: &Rc<Capture>, what: &str) {
    match what {
        "toolbar" => {
            // after whatever opened it has gone (Apps, Search, a menu):
            // straight away, their closing took the toolbar's keyboard focus
            // and it shut again -- from Apps "it didn't open" (Vini)
            let c = Rc::downgrade(cap);
            glib::timeout_add_local_once(Duration::from_millis(TOOLBAR_DELAY_MS), move || {
                if let Some(c) = c.upgrade() {
                    c.show_toolbar();
                }
            });
        }
        "stop" => cap.stop_recording(),
        // the keyboard shortcuts never wait for the timer
        _ => {
            let mut cfg = load_cfg();
            cfg.insert("timer".into(), json!(0));
            cap.run(what, cfg);
        }
    }
}

#[cfg(test)]
#[path = "capture_tests.rs"]
mod tests;
