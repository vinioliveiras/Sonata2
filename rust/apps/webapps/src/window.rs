//! A web app's window (WebKitGTK 6) and the "New Web App" form
//! (webapps/window.py).
//!
//! `sonata2-rs webapp <id>`: one process per web app, its own application
//! id (the Dock's icon and windows), its own cookies and storage. Links to
//! other sites open in the default browser; a login's redirects stay in the
//! window. Notifications are allowed (a chat app's whole point); the camera
//! and the microphone are asked for. Downloads go to Downloads.
//!
//! `sonata2-rs webapp new`: the form (name and address), then the web app opens.

use crate::links::stays_inside;
use crate::webkit;
use adw::prelude::*;
use serde_json::{Map, Value};
use sonata_i18n::{fmt, tr};
use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Duration;

/// The form's application id (the trial's own, like every trial app's).
pub fn new_app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.webapps"
    } else {
        "io.github.vinioliveiras.sonata2.webapps"
    }
}

/// after its window closes: WebKit writes what the site saved
pub const QUIT_DELAY_MS: u64 = 2500;
// WebKitGTK decodes a video given to an <img> (or createImageBitmap) frame by
// frame into memory: WhatsApp checks a 44 s mp4 that way -> 50 GB, killed.
// Chrome refuses a video there; so do web apps, and sites go on to <video>.
pub const NO_VIDEO_IMAGES_JS: &str = r#"(() => {
  const videos = new Set();
  const make = URL.createObjectURL;
  URL.createObjectURL = function (obj) {
    const url = make.apply(this, arguments);
    if (obj && typeof obj.type === "string" && obj.type.startsWith("video/")) videos.add(url);
    return url;
  };
  const drop = URL.revokeObjectURL;
  URL.revokeObjectURL = function (url) { videos.delete(url); return drop.apply(this, arguments); };
  const BAD = "data:,";
  const desc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
  Object.defineProperty(HTMLImageElement.prototype, "src", {
    configurable: true, enumerable: desc.enumerable, get: desc.get,
    set(v) { desc.set.call(this, videos.has(String(v)) ? BAD : v); },
  });
  const setAttr = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, v) {
    if (this instanceof HTMLImageElement && String(name).toLowerCase() === "src" && videos.has(String(v))) v = BAD;
    return setAttr.call(this, name, v);
  };
  const bitmap = window.createImageBitmap;
  if (bitmap) window.createImageBitmap = function (src) {
    if (src instanceof Blob && src.type.startsWith("video/"))
      return Promise.reject(new DOMException("The source image could not be decoded.", "InvalidStateError"));
    return bitmap.apply(this, arguments);
  };
})();"#;

/// set: that script on every page, the console printed out (a site's problem looked into)
pub const DEBUG_JS_ENV: &str = "SONATA_WEBAPP_JS";

/// The scripts every page of a web app gets (NO_VIDEO_IMAGES_JS).
pub fn page_scripts() -> Vec<String> {
    let mut out = vec![NO_VIDEO_IMAGES_JS.to_string()];
    if let Some(extra) = std::env::var_os(DEBUG_JS_ENV).filter(|p| Path::new(p).is_file()) {
        if let Ok(t) = std::fs::read_to_string(extra) {
            out.push(t);
        }
    }
    out
}

/// window.py's own CSS (not in css.json: the exporter doesn't take
/// webapps/ yet), the same text.
fn register_css() {
    sonata_ui::register(
        "webapp",
        ".wa-form { margin-top: 6px; }\n.wa-form label { color: %(label_secondary)s; }\n.wa-form .wa-icon-note { color: %(label_tertiary)s; }\n",
    );
}

pub const SAFE_SCHEMES: [&str; 4] = ["http", "https", "mailto", "tel"];

fn launch_uri(win: &gtk::Window, uri: &str) {
    gtk::UriLauncher::new(uri).launch(Some(win), gio::Cancellable::NONE, |_| {});
}

/// Web, mail and phone links go to their app; anything else (file://, a
/// custom scheme that runs a program...) only after the user says so.
pub fn open_outside(win: &gtk::Window, name: &str, uri: &str) {
    let scheme = crate::urlsplit(uri).scheme;
    if SAFE_SCHEMES.contains(&scheme.as_str()) {
        launch_uri(win, uri);
        return;
    }
    let name = if name.is_empty() { tr!("This web app") } else { name.to_string() };
    let shown = if uri.chars().count() <= 200 { uri.to_string() } else { format!("{}…", uri.chars().take(200).collect::<String>()) };
    let (w, u) = (win.clone(), uri.to_string());
    sonata_ui::dialog::alert(
        &fmt(&tr!("Open this link outside “{name}”?"), &[("name", &name)]),
        &format!("{shown}\n\n{}", tr!("It opens in another program on this computer.")),
        &[("cancel", tr!("Cancel"), ""), ("open", tr!("Open"), "default")],
        move |rid| {
            if rid == "open" {
                launch_uri(&w, &u);
            }
        },
        Some(win.upcast_ref()),
    );
}

const IMAGE_TYPES: [(&str, &str); 11] = [
    ("png", "image/png"),
    ("jpg", "image/jpeg"),
    ("jpeg", "image/jpeg"),
    ("gif", "image/gif"),
    ("webp", "image/webp"),
    ("bmp", "image/bmp"),
    ("avif", "image/avif"),
    ("heic", "image/heic"),
    ("mp4", "video/mp4"),
    ("webm", "video/webm"),
    ("pdf", "application/pdf"),
];
/// a pasted file the page gets (bigger: the page's own attach button)
pub const PASTE_MAX: u64 = 64 * 1024 * 1024;

/// What Ctrl+V should hand the page itself: "image" (a picture, e.g. a
/// screenshot), "files" (files copied in Files), or "" -- text, and
/// anything else, WebKit pastes by itself.
pub fn paste_kind<S: AsRef<str>>(mimes: &[S]) -> &'static str {
    let has = |m: &str| mimes.iter().any(|x| x.as_ref() == m);
    if has("text/uri-list") || has("application/vnd.portal.files") {
        return "files";
    }
    if mimes.iter().any(|m| m.as_ref().starts_with("image/")) && !has("text/plain") && !has("text/plain;charset=utf-8") {
        return "image";
    }
    ""
}

/// A file a page gets on paste: (name, mime, bytes).
pub type Pasted = (String, String, Vec<u8>);

/// JavaScript that pastes `files` into the focused element, as the
/// browser's own paste would: WhatsApp, Telegram, Discord... read
/// ClipboardEvent.clipboardData.files. (WebKitGTK hands pages only text
/// from the clipboard: pictures never arrived -- Vini.)
pub fn paste_script(files: &[Pasted]) -> String {
    use base64::Engine;
    let js = |s: &str| Value::String(s.into()).to_string();
    let items: Vec<String> = files
        .iter()
        .map(|(n, t, b)| format!("{{\"name\": {}, \"type\": {}, \"b64\": {}}}", js(n), js(t), js(&base64::engine::general_purpose::STANDARD.encode(b))))
        .collect();
    format!(
        "(function(files){{const dt=new DataTransfer();\
         for(const f of files){{const s=atob(f.b64);const a=new Uint8Array(s.length);\
         for(let i=0;i<s.length;i++)a[i]=s.charCodeAt(i);\
         dt.items.add(new File([a],f.name,{{type:f.type}}));}}\
         const t=document.activeElement||document.body;\
         t.dispatchEvent(new ClipboardEvent('paste',{{clipboardData:dt,bubbles:true,cancelable:true}}));\
         }})([{}]);",
        items.join(", ")
    )
}

/// The copied files a page can take.
pub fn read_pasted_files<P: AsRef<Path>>(paths: &[P]) -> Vec<Pasted> {
    let mut out = Vec::new();
    for p in paths {
        let p = p.as_ref();
        let ext = p.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default();
        let Some((_, mime)) = IMAGE_TYPES.iter().find(|t| t.0 == ext) else { continue };
        if std::fs::metadata(p).is_ok_and(|m| m.len() <= PASTE_MAX) {
            if let Ok(b) = std::fs::read(p) {
                out.push((p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(), mime.to_string(), b));
            }
        }
    }
    out
}

/// A free name in `folder` for a download: "x.pdf", then "x 2.pdf"...
pub fn download_path(folder: &Path, name: &str) -> PathBuf {
    let base = Path::new(if name.is_empty() { "download" } else { name }).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "download".into());
    let (stem, ext) = match base.rfind('.').filter(|i| *i > 0) {
        Some(i) => (base[..i].to_string(), base[i..].to_string()),
        None => (base.clone(), String::new()),
    };
    let mut path = folder.join(format!("{stem}{ext}"));
    let mut n = 2;
    while path.exists() {
        path = folder.join(format!("{stem} {n}{ext}"));
        n += 1;
    }
    path
}

// -- the window ---------------------------------------------------------------------------------

pub struct WebAppWindow {
    pub win: gtk::ApplicationWindow,
    pub view: gtk::Widget,
    pub wid: String,
    pub entry: Map<String, Value>,
    back: gtk::Button,
    forward: gtk::Button,
    pub keys: gtk::EventControllerKey,
    quitting: RefCell<Option<(glib::SourceId, gio::ApplicationHoldGuard)>>,
}

fn entry_text<'a>(entry: &'a Map<String, Value>, key: &str) -> &'a str {
    entry.get(key).and_then(Value::as_str).unwrap_or("")
}

impl WebAppWindow {
    pub fn new(app: &adw::Application, wid: &str, entry: Map<String, Value>) -> Option<Rc<Self>> {
        let name = entry_text(&entry, "name").to_string();
        let base = crate::data_dir(wid);
        let session = webkit::network_session(&base.join("data").to_string_lossy(), &base.join("cache").to_string_lossy(), &base.join("cookies.sqlite").to_string_lossy())?;
        let content = webkit::content_manager(&page_scripts())?;
        let view = webkit::web_view(&session, &content)?;
        view.set_vexpand(true);
        view.set_hexpand(true);
        let win = gtk::ApplicationWindow::builder().application(app).title(&name).css_classes(["sonata-webapp"]).build();
        sonata_ui::window::standard(&win);
        sonata_ui::window::remember_size(win.upcast_ref(), &crate::size_key(wid), crate::DEFAULT_SIZE.0, crate::DEFAULT_SIZE.1); // its last size (Vini)
        win.set_size_request(crate::MIN_SIZE.0, crate::MIN_SIZE.1);
        let settings: glib::Object = view.property("settings");
        settings.set_property("enable-developer-extras", false);
        settings.set_property("enable-write-console-messages-to-stdout", std::env::var_os(DEBUG_JS_ENV).is_some_and(|v| !v.is_empty()));
        settings.set_property("javascript-can-open-windows-automatically", false);
        settings.set_property("enable-back-forward-navigation-gestures", true);
        let (v1, v2, v3) = (view.clone(), view.clone(), view.clone());
        let (handle, bar, _) = sonata_ui::window::glass_toolbar(&win, vec![("view-refresh-symbolic", tr!("Reload"), Box::new(move || webkit::reload(&v3)))]);
        // the title shows once, in the title bar (Vini: it was there and in the toolbar too)
        let start = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        let tool = |icon: &str, tip: String, f: Box<dyn Fn()>| {
            let b = gtk::Button::builder().icon_name(icon).tooltip_text(tip).can_focus(false).build();
            b.add_css_class("tool");
            b.connect_clicked(move |_| f());
            start.append(&b);
            b
        };
        let back = tool("go-previous-symbolic", tr!("Back"), Box::new(move || webkit::go_back(&v1)));
        let forward = tool("go-next-symbolic", tr!("Forward"), Box::new(move || webkit::go_forward(&v2)));
        bar.set_start_widget(Some(&start));
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        col.append(&handle);
        col.append(&view);
        win.set_child(Some(&col));
        // before the page: WebKit takes Ctrl+V (and the other keys) itself, so a
        // window controller in the usual bubble phase never saw them -- pictures
        // never reached the paste below (Vini: WhatsApp)
        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        win.add_controller(keys.clone());
        let me = Rc::new(WebAppWindow { win, view, wid: wid.into(), entry, back, forward, keys, quitting: RefCell::new(None) });
        me.connect(&session);
        me.buttons();
        webkit::load_uri(&me.view, entry_text(&me.entry, "url"));
        Some(me)
    }

    fn connect(self: &Rc<Self>, session: &glib::Object) {
        let w = Rc::downgrade(self);
        self.keys.connect_key_pressed(move |_, key, _, state| match w.upgrade() {
            Some(me) if me.key(key, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        let w = Rc::downgrade(self);
        self.win.connect_close_request(move |_| {
            if let Some(me) = w.upgrade() {
                me.close();
            }
            glib::Propagation::Stop
        });
        let w = Rc::downgrade(self);
        self.view.connect_notify_local(Some("title"), move |_, _| {
            if let Some(me) = w.upgrade() {
                let t: Option<String> = me.view.property("title");
                me.win.set_title(Some(t.as_deref().filter(|t| !t.is_empty()).unwrap_or(entry_text(&me.entry, "name"))));
            }
        });
        let w = Rc::downgrade(self);
        self.view.connect_notify_local(Some("favicon"), move |_, _| {
            if let Some(me) = w.upgrade() {
                me.favicon();
            }
        });
        let w = Rc::downgrade(self);
        self.view.connect_notify_local(Some("uri"), move |_, _| {
            if let Some(me) = w.upgrade() {
                me.buttons();
            }
        });
        let w = Rc::downgrade(self);
        self.view.connect_local("decide-policy", false, move |args| {
            let me = w.upgrade()?;
            Some(me.policy(args).to_value())
        });
        let w = Rc::downgrade(self);
        self.view.connect_local("create", false, move |args| {
            if let Some(me) = w.upgrade() {
                let (_, uri) = webkit::navigation_action(webkit::boxed(&args[1]));
                if !uri.is_empty() && uri != "about:blank" {
                    if stays_inside(entry_text(&me.entry, "url"), &uri) {
                        webkit::load_uri(&me.view, &uri);
                    } else {
                        open_outside(me.win.upcast_ref(), entry_text(&me.entry, "name"), &uri);
                    }
                }
            }
            Some(None::<gtk::Widget>.to_value())
        });
        let w = Rc::downgrade(self);
        self.view.connect_local("permission-request", false, move |args| {
            let me = w.upgrade()?;
            let request = webkit::object_arg(&args[1])?;
            me.permission(&request);
            Some(true.to_value())
        });
        session.connect_local("download-started", false, |args| {
            if let Some(download) = args.get(1).and_then(webkit::object_arg) {
                download.connect_local("decide-destination", false, |args| {
                    let download = webkit::object_arg(&args[0])?;
                    let name = args.get(1).and_then(|v| v.get::<String>().ok()).unwrap_or_default();
                    let folder = glib::user_special_dir(glib::UserDirectory::Downloads).unwrap_or_else(glib::home_dir);
                    webkit::set_download_destination(&download, &download_path(&folder, &name).to_string_lossy());
                    Some(true.to_value())
                });
            }
            None
        });
    }

    /// Closing: the window goes at once; the app either keeps running (the
    /// form's "Keep running…": notifications) or quits a moment later, so
    /// WebKit finishes writing the login (Vini: WhatsApp asked for the QR
    /// code again -- quitting at once lost what it was saving).
    fn close(self: &Rc<Self>) {
        self.win.set_visible(false);
        if self.entry.get("background").is_some_and(|b| b.as_bool().unwrap_or(false)) {
            return;
        }
        let Some(app) = self.win.application() else { return };
        if self.quitting.borrow().is_some() {
            return;
        }
        let guard = app.hold();
        let w = Rc::downgrade(self);
        let src = glib::timeout_add_local_once(Duration::from_millis(QUIT_DELAY_MS), move || {
            if let Some(me) = w.upgrade() {
                let pending = me.quitting.take();
                if let Some((_, g)) = pending {
                    drop(g);
                }
            }
            app.quit();
        });
        self.quitting.replace(Some((src, guard)));
    }

    /// Opened again (its icon) while closing or kept in the background.
    pub fn reopen(&self) {
        if let Some((src, guard)) = self.quitting.take() {
            src.remove();
            drop(guard);
        }
        self.win.present();
    }

    fn buttons(&self) {
        self.back.set_sensitive(webkit::can_go_back(&self.view));
        self.forward.set_sensitive(webkit::can_go_forward(&self.view));
    }

    fn zoom(&self, by: Option<f64>) {
        let z: f64 = self.view.property("zoom-level");
        let new = match by {
            Some(d) => (z + d).clamp(0.3, 3.0),
            None => 1.0,
        };
        self.view.set_property("zoom-level", new);
    }

    fn key(self: &Rc<Self>, key: gtk::gdk::Key, state: gtk::gdk::ModifierType) -> bool {
        use gtk::gdk::{Key, ModifierType};
        let ctrl = state.contains(ModifierType::CONTROL_MASK);
        let alt = state.contains(ModifierType::ALT_MASK);
        if (ctrl && (key == Key::r || key == Key::R)) || key == Key::F5 {
            webkit::reload(&self.view);
        } else if alt && key == Key::Left {
            webkit::go_back(&self.view);
        } else if alt && key == Key::Right {
            webkit::go_forward(&self.view);
        } else if ctrl && (key == Key::plus || key == Key::equal) {
            self.zoom(Some(0.1));
        } else if ctrl && key == Key::minus {
            self.zoom(Some(-0.1));
        } else if ctrl && key == Key::_0 {
            self.zoom(None);
        } else if ctrl && (key == Key::v || key == Key::V) {
            return self.paste();
        } else {
            return false;
        }
        true
    }

    /// Ctrl+V with a picture or copied files: handed to the page (true);
    /// text goes on to WebKit's own paste (false).
    fn paste(self: &Rc<Self>) -> bool {
        let clip = self.win.clipboard();
        let mimes: Vec<String> = clip.formats().mime_types().iter().map(|m| m.to_string()).collect();
        match paste_kind(&mimes) {
            "image" => {
                let w = Rc::downgrade(self);
                clip.read_texture_async(gio::Cancellable::NONE, move |res| {
                    let files = match res {
                        Ok(Some(tex)) => vec![("image.png".to_string(), "image/png".to_string(), tex.save_to_png_bytes().to_vec())],
                        _ => Vec::new(),
                    };
                    if let Some(me) = w.upgrade() {
                        me.send_paste(&files);
                    }
                });
                true
            }
            "files" => {
                let w = Rc::downgrade(self);
                clip.read_async(&["text/uri-list"], glib::Priority::DEFAULT, gio::Cancellable::NONE, move |res| {
                    let data = res
                        .ok()
                        .and_then(|(stream, _)| {
                            let b = stream.read_bytes(1 << 20, gio::Cancellable::NONE).ok(); // a list of URIs: small
                            let _ = stream.close(gio::Cancellable::NONE);
                            b
                        })
                        .map(|b| String::from_utf8_lossy(&b).into_owned())
                        .unwrap_or_default();
                    let paths: Vec<PathBuf> = data.lines().map(str::trim).filter(|u| u.starts_with("file://")).filter_map(|u| gio::File::for_uri(u).path()).collect();
                    if let Some(me) = w.upgrade() {
                        me.send_paste(&read_pasted_files(&paths));
                    }
                });
                true
            }
            _ => false,
        }
    }

    /// The files to the page; none it can take (a text file, a folder):
    /// WebKit's own paste, as if we had never stepped in.
    fn send_paste(&self, files: &[Pasted]) {
        if files.is_empty() {
            webkit::execute_editing_command(&self.view, "Paste");
        } else {
            webkit::evaluate_javascript(&self.view, &paste_script(files));
        }
    }

    /// The page's icon, when we don't have one as good (the site's file
    /// couldn't be fetched when the web app was made).
    fn favicon(&self) {
        let tex: Option<gtk::gdk::Texture> = self.view.property("favicon");
        let Some(tex) = tex else { return };
        if tex.width() < crate::ICON_MIN || crate::icon_path(&self.wid).is_file() {
            return;
        }
        let _ = std::fs::create_dir_all(crate::data_dir(&self.wid));
        if tex.save_to_png(crate::icon_path(&self.wid)).is_ok() {
            crate::write_desktop(&self.wid, None);
        }
    }

    fn policy(&self, args: &[glib::Value]) -> bool {
        // WEBKIT_POLICY_DECISION_TYPE_NAVIGATION_ACTION
        if args.len() < 3 || webkit::enum_value(&args[2]) != 0 {
            return false;
        }
        let Some(decision) = webkit::object_arg(&args[1]) else { return false };
        let (kind, uri) = webkit::navigation_action(webkit::decision_action(&decision));
        // WEBKIT_NAVIGATION_TYPE_LINK_CLICKED
        if kind == 0 && !stays_inside(entry_text(&self.entry, "url"), &uri) {
            webkit::decision_ignore(&decision);
            open_outside(self.win.upcast_ref(), entry_text(&self.entry, "name"), &uri);
            return true;
        }
        false
    }

    fn permission(&self, request: &glib::Object) {
        let kind = request.type_().name();
        if kind == "WebKitNotificationPermissionRequest" {
            webkit::permission_allow(request);
            return;
        }
        if kind != "WebKitUserMediaPermissionRequest" {
            webkit::permission_deny(request);
            return;
        }
        let heading = match webkit::media_devices(request) {
            (true, true) => tr!("Allow “{name}” to use the camera and the microphone?"),
            (true, false) => tr!("Allow “{name}” to use the camera?"),
            _ => tr!("Allow “{name}” to use the microphone?"),
        };
        let r = request.clone();
        sonata_ui::dialog::alert(
            &fmt(&heading, &[("name", entry_text(&self.entry, "name"))]),
            "",
            &[("deny", tr!("Don't Allow"), ""), ("allow", tr!("Allow"), "default")],
            move |rid| {
                if rid == "allow" {
                    webkit::permission_allow(&r);
                } else {
                    webkit::permission_deny(&r);
                }
            },
            Some(self.win.upcast_ref()),
        );
    }
}

// -- the "New Web App" form ---------------------------------------------------------------------

/// (engine, label, browser key)
pub type EngineChoice = (&'static str, String, Option<&'static str>);

/// The form's Engine choices -- each Chromium-family browser installed
/// (Brave too: Vini), then WebKit -- as (engine, label, browser key), and
/// the one picked at first.
pub fn form_engines(browsers: &[crate::Browser], entry: Option<&Map<String, Value>>) -> (Vec<EngineChoice>, usize) {
    let mut engines: Vec<EngineChoice> = browsers.iter().map(|b| ("chromium", b.name.to_string(), Some(b.key))).collect();
    engines.push(("webkit", "WebKit".into(), None));
    let (current, mine) = match entry {
        Some(e) => {
            let current = crate::engine(Some(e));
            (current, (current == "chromium").then(|| crate::browser_key(Some(e))))
        }
        None => (if browsers.is_empty() { "webkit" } else { "chromium" }, browsers.first().map(|b| b.key)),
    };
    let start = engines.iter().position(|(e, _, k)| *e == current && (*k == mine || *e == "webkit")).unwrap_or(0);
    (engines, start)
}

/// A picture through the Open panel (appicons_page.pick_picture: Settings >
/// App Icons isn't ported, the same few lines): on_path(path) when one was chosen.
fn pick_picture(parent: Option<&gtk::Window>, on_path: impl Fn(PathBuf) + 'static) {
    let flt = gtk::FileFilter::new();
    flt.set_name(Some(&tr!("Images")));
    for pat in ["*.png", "*.svg", "*.jpg", "*.jpeg", "*.webp"] {
        flt.add_pattern(pat);
    }
    let store = gio::ListStore::new::<gtk::FileFilter>();
    store.append(&flt);
    let dlg = gtk::FileDialog::builder().title(tr!("Choose an Icon")).modal(true).filters(&store).build();
    dlg.open(parent, gio::Cancellable::NONE, move |res| {
        if let Some(p) = res.ok().and_then(|f| f.path()) {
            on_path(p);
        }
    });
}

/// Name and address; Create stays off until the address is one.
/// on_done(id). wid: edit that web app instead (Vini) -- the same form,
/// filled in.
pub fn form(on_done: impl Fn(Option<String>) + 'static, parent: Option<&gtk::Window>, wid: Option<&str>) -> adw::AlertDialog {
    let entry0 = wid.and_then(crate::get);
    let grid = gtk::Grid::builder().row_spacing(8).column_spacing(8).css_classes(["wa-form"]).build();
    let url = sonata_ui::controls::text_field("", "web.whatsapp.com"); // the kit's fields
    url.set_hexpand(true);
    let name = sonata_ui::controls::text_field("", &tr!("Name"));
    name.set_hexpand(true);
    for e in [&url, &name] {
        e.set_activates_default(true);
    }
    for (row, (label, e)) in [(tr!("Address:"), &url), (tr!("Name:"), &name)].into_iter().enumerate() {
        grid.attach(&gtk::Label::builder().label(label).xalign(1.0).build(), 0, row as i32, 1, 1);
        grid.attach(e, 1, row as i32, 1, 1);
    }
    // the icon: the site's own unless you choose a picture (Vini); the same
    // picker as Settings > App Icons, where it can be changed later
    let typed = Rc::new(Cell::new(false));
    let icon: Rc<RefCell<Option<PathBuf>>> = Rc::default();
    let icon_box = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    let icon_img = gtk::Image::builder().icon_name(crate::FALLBACK_ICON).pixel_size(32).build();
    let icon_note = gtk::Label::builder().label(tr!("The site's own")).xalign(0.0).hexpand(true).css_classes(["wa-icon-note"]).build();
    icon_box.append(&icon_img);
    icon_box.append(&icon_note);
    let (img, note, chosen, par) = (icon_img.clone(), icon_note.clone(), icon.clone(), parent.cloned());
    icon_box.append(&sonata_ui::controls::push_button(
        &tr!("Choose…"),
        move || {
            let (img, note, chosen) = (img.clone(), note.clone(), chosen.clone());
            pick_picture(par.as_ref(), move |path| {
                img.set_from_file(Some(&path));
                note.set_label(&path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default());
                chosen.replace(Some(path));
            });
        },
        "",
    ));
    grid.attach(&gtk::Label::builder().label(tr!("Icon:")).xalign(1.0).build(), 0, 2, 1, 1);
    grid.attach(&icon_box, 1, 2, 1, 1);
    // closing its window keeps it running (a chat's notifications keep coming)
    let background = gtk::CheckButton::with_label(&tr!("Keep running when its window is closed"));
    grid.attach(&background, 1, 4, 1, 1);
    // the engine (Vini): a Chromium-family browser (sites made for Chrome just
    // work) -- each one installed, Brave too (Vini) -- or WebKit
    let (engines, start) = form_engines(&crate::chromium_browsers(), entry0.as_ref());
    let picked: Rc<Cell<(&'static str, Option<&'static str>)>> = Rc::new(Cell::new((engines[start].0, engines[start].2)));
    let (p, bg, list) = (picked.clone(), background.clone(), engines.clone());
    let engine_changed = move |i: u32| {
        if let Some(e) = list.get(i as usize) {
            p.set((e.0, e.2));
            bg.set_sensitive(e.0 == "webkit"); // the browser keeps its own windows
        }
    };
    engine_changed(start as u32);
    let engine_row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    let labels: Vec<String> = engines.iter().map(|e| e.1.clone()).collect();
    engine_row.append(&sonata_ui::controls::popup_button(&labels, start as u32, engine_changed));
    if entry0.is_some() {
        engine_row.append(&gtk::Label::builder().label(tr!("Switching asks you to sign in again.")).xalign(0.0).css_classes(["wa-icon-note"]).wrap(true).hexpand(true).build());
    }
    grid.attach(&gtk::Label::builder().label(tr!("Engine:")).xalign(1.0).build(), 0, 3, 1, 1);
    grid.attach(&engine_row, 1, 3, 1, 1);
    if let (Some(e), Some(w)) = (&entry0, wid) {
        url.set_text(entry_text(e, "url"));
        name.set_text(entry_text(e, "name"));
        background.set_active(e.get("background").is_some_and(|b| b.as_bool().unwrap_or(false)));
        typed.set(true); // its name stays as it is
        if crate::icon_path(w).is_file() {
            icon_img.set_from_file(Some(crate::icon_path(w)));
        }
    }
    let (u, n, bg, wid_s, editing) = (url.clone(), name.clone(), background.clone(), wid.map(String::from), entry0.is_some());
    let answer = move |rid: &str| {
        if rid == "create" {
            if let Some(target) = crate::normalize_url(&u.text()) {
                let (engine, browser) = picked.get();
                let done = match (&wid_s, editing) {
                    (Some(w), true) => {
                        let changes = crate::Changes { name: Some(n.text().into()), url: Some(target), background: Some(bg.is_active()), engine: Some(engine.into()), browser: browser.map(String::from) };
                        crate::update(w, &changes, None);
                        Some(w.clone())
                    }
                    _ => crate::create(&n.text(), &target, None, true, bg.is_active(), Some(engine), browser).ok(),
                };
                if let (Some(d), Some(pic)) = (&done, icon.borrow().as_ref()) {
                    let _ = crate::set_custom_icon(d, pic);
                }
                on_done(done);
                return;
            }
        }
        on_done(None);
    };
    let (heading, body, ok) = if editing {
        (tr!("Edit Web App"), tr!("Changes show the next time it opens."), tr!("Save"))
    } else {
        (tr!("New Web App"), tr!("A website in a window of its own, with its own icon and login."), tr!("Create"))
    };
    let dlg = sonata_ui::dialog::alert(&heading, &body, &[("cancel", tr!("Cancel"), ""), ("create", ok, "default")], answer, parent.map(|p| p.upcast_ref()));
    dlg.set_extra_child(Some(&grid));
    let (d, n, t) = (dlg.clone(), name.clone(), typed.clone());
    let changed = move |u: &gtk::Entry| {
        let target = crate::normalize_url(&u.text());
        d.set_response_enabled("create", target.is_some());
        if !t.get() {
            n.set_text(&target.map(|t| crate::default_name(&t)).unwrap_or_default());
        }
    };
    changed(&url);
    url.connect_changed(changed);
    let t = typed.clone();
    name.connect_changed(move |n| t.set(n.has_focus()));
    url.grab_focus();
    dlg
}

/// Said instead of failing silently (Vini: "the web app doesn't open" --
/// webkitgtk-6.0 wasn't installed).
pub fn missing_webkit(app: &adw::Application) {
    let guard = RefCell::new(Some(app.hold()));
    let (heading, body) = crate::missing_webkit();
    sonata_ui::dialog::alert(&heading, &body, &[("ok", "OK".into(), "default")], move |_| drop(guard.take()), None);
}

pub fn open_webapp(wid: &str) -> glib::ExitCode {
    let Some(entry) = crate::get(wid) else {
        eprintln!("sonata2 webapp: no web app {wid}");
        return glib::ExitCode::FAILURE;
    };
    // the Wayland app_id = the desktop entry's StartupWMClass; the real one on
    // trial too: one process per web app, whichever build opened it (lib.rs)
    let id = crate::app_id(wid);
    glib::set_prgname(Some(id.as_str()));
    glib::set_application_name(entry_text(&entry, "name"));
    let app = adw::Application::builder().application_id(&id).build();
    let state: Rc<RefCell<Option<Rc<WebAppWindow>>>> = Rc::default();
    let wid = wid.to_string();
    app.connect_activate(move |a| {
        sonata_ui::setup();
        if !crate::webkit_available() {
            missing_webkit(a);
            return;
        }
        if let Some(w) = state.borrow().as_ref() {
            w.reopen();
            return;
        }
        match WebAppWindow::new(a, &wid, entry.clone()) {
            Some(w) => {
                w.win.present();
                state.replace(Some(w));
            }
            None => missing_webkit(a),
        }
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

/// `sonata2-rs webapp edit <id>`: the form, filled in (Launchpad's and the
/// Dock's "Edit Web App…").
pub fn edit_webapp(wid: &str) -> glib::ExitCode {
    if crate::get(wid).is_none() {
        eprintln!("sonata2 webapp: no web app {wid}");
        return glib::ExitCode::FAILURE;
    }
    new_webapp(Some(wid))
}

pub fn new_webapp(wid: Option<&str>) -> glib::ExitCode {
    glib::set_prgname(Some(new_app_id()));
    glib::set_application_name(&tr!("New Web App"));
    let app = adw::Application::builder().application_id(new_app_id()).build();
    let shown: Rc<RefCell<Option<adw::AlertDialog>>> = Rc::default();
    let started = Rc::new(Cell::new(false));
    let wid = wid.map(String::from);
    app.connect_activate(move |a| {
        if started.replace(true) {
            return;
        }
        sonata_ui::setup();
        register_css();
        if !crate::webkit_available() {
            missing_webkit(a);
            return;
        }
        let guard = RefCell::new(Some(a.hold()));
        let editing = wid.is_some();
        let done = move |made: Option<String>| {
            if let Some(m) = made.filter(|_| !editing) {
                crate::launch(&m); // a new one opens; an edited one stays as it is
            }
            drop(guard.take());
        };
        shown.replace(Some(form(done, None, wid.as_deref())));
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

pub fn main(argv: &[String]) -> glib::ExitCode {
    match argv.first().map(String::as_str) {
        None => {
            eprintln!("usage: sonata2 webapp new | <id>");
            glib::ExitCode::from(2)
        }
        Some("new") => new_webapp(None),
        Some("edit") if argv.len() > 1 => edit_webapp(&argv[1]),
        Some(id) => open_webapp(id),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Browser;

    /// Vini: pictures in the clipboard never pasted into WhatsApp (WebKitGTK
    /// hands pages only text).
    #[test]
    fn test_what_ctrl_v_hands_the_page() {
        assert_eq!(paste_kind(&["image/png"]), "image"); // a screenshot
        assert_eq!(paste_kind(&["text/uri-list", "x-special/gnome-copied-files"]), "files");
        assert_eq!(paste_kind(&["text/plain;charset=utf-8", "text/plain"]), "");
        assert_eq!(paste_kind(&["text/html", "text/plain", "image/png"]), ""); // copied web text
    }

    #[test]
    fn test_script_pastes_files_into_the_focused_element() {
        let js = paste_script(&[("a.png".into(), "image/png".into(), b"\x89PNG".to_vec())]);
        assert!(js.contains("new ClipboardEvent('paste'"));
        assert!(js.contains("document.activeElement"));
        assert!(js.contains("\"type\": \"image/png\""));
        assert!(js.contains("\"b64\": \"iVBORw==\""));
    }

    #[test]
    fn test_only_files_a_page_can_take() {
        let d = tempfile::tempdir().unwrap();
        for n in ["p.JPG", "notes.txt"] {
            std::fs::write(d.path().join(n), b"x").unwrap();
        }
        let got = read_pasted_files(&[d.path().join("p.JPG"), d.path().join("notes.txt"), PathBuf::from("/nope.png")]);
        assert_eq!(got, vec![("p.JPG".to_string(), "image/jpeg".to_string(), b"x".to_vec())]);
    }

    #[test]
    fn downloads_never_overwrite() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(download_path(d.path(), "a.pdf"), d.path().join("a.pdf"));
        std::fs::write(d.path().join("a.pdf"), b"").unwrap();
        assert_eq!(download_path(d.path(), "../a.pdf"), d.path().join("a 2.pdf"));
        assert_eq!(download_path(d.path(), ""), d.path().join("download"));
    }

    fn b(path: &str, name: &'static str, key: &'static str) -> Browser {
        Browser { path: path.into(), name, key }
    }

    /// Vini: Brave installed with Chrome couldn't be picked for a web app
    /// (only the first Chromium-family browser was offered): the Engine
    /// pop-up lists each one.
    #[test]
    fn test_brave_not_offered_beside_chrome() {
        let found = [b("/usr/bin/google-chrome-stable", "Google Chrome", "chrome"), b("/usr/bin/brave", "Brave", "brave")];
        let (engines, start) = form_engines(&found, None);
        let labels: Vec<&str> = engines.iter().map(|e| e.1.as_str()).collect();
        assert_eq!(labels, ["Google Chrome", "Brave", "WebKit"]);
        assert_eq!(start, 0);
        assert_eq!((engines[1].0, engines[1].2), ("chromium", Some("brave")));
        let (engines, start) = form_engines(&[], None);
        assert_eq!((engines.len(), start, engines[0].0), (1, 0, "webkit"));
    }

    #[test]
    fn video_images_script_is_pythons() {
        assert!(NO_VIDEO_IMAGES_JS.contains("URL.createObjectURL = function (obj)"));
        assert!(NO_VIDEO_IMAGES_JS.contains("const BAD = \"data:,\";"));
        assert!(page_scripts()[0].starts_with("(() => {"));
    }
}
