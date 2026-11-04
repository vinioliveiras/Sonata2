//! Screen sharing picker (sonata2/shell/sharepicker.py; macOS: "Share
//! Screen" in a meeting app), and the list Capture picks a display or a
//! window from.
//!
//! Apps share the screen through the ScreenCast portal (Chrome, Firefox,
//! Discord, OBS, Zoom...). xdg-desktop-portal-wlr does the capture and asks
//! this picker which screen (or window) to share: its choices come on
//! stdin, one per line ("HDMI-A-1", or "Monitor: HDMI-A-1" / "Window: ...:
//! title" on newer versions); the chosen line goes back on stdout, nothing
//! when cancelled. install.sh points
//! ~/.config/xdg-desktop-portal-wlr/Sonata (chooser_type=dmenu) at the
//! Python `sonata2 share-picker`; `sonata2-rs share-picker` reads the same
//! lines.
//!
//! A glass sheet in the middle of the screen: a live-looking thumbnail and
//! the name of every display, the first one selected; Share / Cancel,
//! Return / Escape, double-click shares.

use gtk::{gdk, gdk_pixbuf::Pixbuf, gio, glib, pango, prelude::*};
use gtk4_layer_shell::{KeyboardMode, Layer, LayerShell};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::io::BufRead;
use std::rc::Rc;

pub const THUMB_W: i32 = 240;
pub const THUMB_H: i32 = 135;
/// the panel around the choices: its margin and padding, the title, the buttons
pub const CHROME_H: i32 = 200;

/// (kind, name to show, output name / toplevel identifier for the thumbnail).
/// xdg-desktop-portal-wlr: "Monitor: HDMI-A-1 Acer KG241Y ...", "Window: <title> (<identifier>)".
pub fn parse(line: &str) -> (&'static str, String, Option<String>) {
    if let Some(rest) = line.strip_prefix("Monitor: ") {
        let name = rest.trim().split(' ').next().unwrap_or("").to_string();
        return ("screen", name.clone(), Some(name));
    }
    if let Some(rest) = line.strip_prefix("Window: ") {
        let rest = rest.trim();
        let or_window = |t: &str| if t.is_empty() { tr!("Window") } else { t.to_string() };
        if rest.ends_with(')') && rest.contains(" (") {
            let (title, ident) = rest[..rest.len() - 1].rsplit_once(" (").unwrap_or((rest, ""));
            return ("window", or_window(title.trim()), Some(ident.trim().to_string()));
        }
        // older versions: "Window: <id>: <title>"
        if let Some((_, title)) = rest.split_once(": ") {
            return ("window", or_window(title.trim()), None);
        }
        // only the identifier
        return ("window", tr!("Window"), (!rest.is_empty()).then(|| rest.to_string()));
    }
    let l = line.trim().to_string();
    ("screen", l.clone(), Some(l))
}

/// "Acer KG241Y" for HDMI-A-1 (the built-in panel: "Built-in Display").
pub fn display_name(connector: &str) -> String {
    if ["eDP", "LVDS", "DSI"].iter().any(|p| connector.starts_with(p)) {
        return tr!("Built-in Display");
    }
    for m in sonata_shell::layer::monitors() {
        if sonata_shell::layer::connector(&m) == connector {
            let d = m.description().map(|s| s.to_string()).filter(|s| !s.is_empty()).or_else(|| m.model().map(|s| s.to_string())).unwrap_or_else(|| connector.to_string());
            return d.split(" (").next().unwrap_or(&d).to_string();
        }
    }
    connector.to_string()
}

/// The tallest the choices may get before they scroll: many windows to
/// share on a 720 px laptop pushed Cancel / Share off the screen.
pub fn grid_max_height(screen_h: i32) -> i32 {
    (THUMB_H + 40).max(screen_h - CHROME_H)
}

/// A picture's size scaled to fit a picker item (never stretched).
pub fn fit_size(w: i32, h: i32) -> Option<(i32, i32)> {
    if w <= 0 || h <= 0 {
        return None;
    }
    let scale = (THUMB_W as f64 / w as f64).min(THUMB_H as f64 / h as f64);
    // Python's round(): halves to even
    Some((((w as f64 * scale).round_ties_even() as i32).max(1), ((h as f64 * scale).round_ties_even() as i32).max(1)))
}

/// A pixbuf scaled to fit a picker item; None stays None.
pub fn fit_texture(pb: Option<&Pixbuf>) -> Option<gdk::Texture> {
    let pb = pb?;
    let (w, h) = fit_size(pb.width(), pb.height())?;
    pb.scale_simple(w, h, gtk::gdk_pixbuf::InterpType::Bilinear).map(|p| gdk::Texture::for_pixbuf(&p))
}

/// grim's arguments for a choice's thumbnail: a display's picture, or a
/// window's own (grim -T: the toplevel capture xdg-desktop-portal-wlr
/// shares), even when covered.
pub fn thumb_args(kind: &str, reference: Option<&str>) -> Option<Vec<String>> {
    let r = reference.filter(|r| !r.is_empty())?;
    Some([if kind == "screen" { "-o" } else { "-T" }, r, "-s", "0.25"].iter().map(|s| s.to_string()).collect())
}

/// grim's PNG on stdout (off the main loop: no GTK here).
fn grim_png(args: &[String]) -> Option<Vec<u8>> {
    sonata_backend::run::which("grim")?;
    let mut child = std::process::Command::new("grim").args(args).arg("-").stdin(std::process::Stdio::null()).stdout(std::process::Stdio::piped()).stderr(std::process::Stdio::null()).spawn().ok()?;
    let mut out = Vec::new();
    use std::io::Read;
    let mut so = child.stdout.take()?;
    let reader = std::thread::spawn(move || {
        let _ = so.read_to_end(&mut out);
        out
    });
    let end = std::time::Instant::now() + std::time::Duration::from_secs(4);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if std::time::Instant::now() < end => std::thread::sleep(std::time::Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break;
            }
        }
    }
    reader.join().ok().filter(|b| !b.is_empty())
}

fn png_texture(bytes: &[u8]) -> Option<gdk::Texture> {
    let loader = gtk::gdk_pixbuf::PixbufLoader::with_type("png").ok()?;
    loader.write(bytes).ok()?;
    loader.close().ok()?;
    fit_texture(loader.pixbuf().as_ref())
}

/// jobs run at once, results in their order (one grim per window: six
/// windows waited 1 s one by one); at most six at a time.
pub fn parallel<T: Send, F: FnOnce() -> T + Send>(jobs: Vec<F>) -> Vec<T> {
    let mut out = Vec::new();
    let mut jobs = jobs.into_iter().peekable();
    while jobs.peek().is_some() {
        let chunk: Vec<F> = jobs.by_ref().take(6).collect();
        std::thread::scope(|s| {
            let handles: Vec<_> = chunk.into_iter().map(|j| s.spawn(j)).collect();
            out.extend(handles.into_iter().map(|h| h.join().expect("a thumbnail job")));
        });
    }
    out
}

/// {title: app id} of the open windows (the app's icon on a window's thumbnail).
fn views_by_title() -> HashMap<String, String> {
    crate::capture::views()
        .iter()
        .filter_map(|v| {
            let t = v.get("title")?.as_str().filter(|t| !t.is_empty())?;
            Some((t.to_string(), v.get("app-id").and_then(|a| a.as_str()).unwrap_or("").to_string()))
        })
        .collect()
}

/// The smallest display's height (a window before present(): the
/// compositor picks the display; whatever is sized from it fits any).
fn screen_height() -> Option<i32> {
    sonata_shell::layer::monitors().iter().map(|m| m.geometry().height()).min()
}

/// Keep a glass surface's "solid" class in step with the setting (ui/theme.py's glass_class).
fn glass_class(widget: &gtk::Widget) {
    let sync = |w: &gtk::Widget| {
        if sonata_ui::theme::glass() {
            w.remove_css_class("solid");
        } else {
            w.add_css_class("solid");
        }
    };
    sync(widget);
    let weak = widget.downgrade();
    sonata_ui::theme::on_change(move || {
        if let Some(w) = weak.upgrade() {
            sync(&w);
        }
    });
}

/// One choice: a thumbnail (else an icon), the app's icon over its corner, a name.
/// what a picker does with the choice (None: cancelled)
pub type OnDone = Box<dyn FnOnce(Option<String>)>;

pub struct PickItem {
    pub name: String,
    pub texture: Option<gdk::Texture>,
    pub icon: String,
    pub badge: Option<gio::Icon>,
    pub value: String,
}

/// A glass sheet in the middle of the screen: a thumbnail and a name per
/// choice, the first one selected; action / Cancel, Return / Escape,
/// arrows, double-click. on_done(value or None).
pub struct Picker {
    pub win: gtk::Window,
    values: Vec<String>,
    index: Cell<usize>,
    items: Vec<gtk::Box>,
    on_done: RefCell<Option<OnDone>>,
}

impl Picker {
    pub fn new(app: &gtk::Application, title: &str, text: Option<&str>, items: Vec<PickItem>, action: &str, on_done: OnDone) -> Rc<Self> {
        sonata_ui::css::register_module("shell/sharepicker.py");
        let win = gtk::Window::builder().application(app).title(title).decorated(false).resizable(false).build();
        win.add_css_class("sonata-share");
        let panel = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(12).css_classes(["share-panel"]).halign(gtk::Align::Center).valign(gtk::Align::Center).build();
        glass_class(panel.upcast_ref());
        let head = gtk::Box::new(gtk::Orientation::Vertical, 2);
        head.append(&gtk::Label::builder().label(title).xalign(0.0).css_classes(["share-title"]).build());
        if let Some(t) = text.filter(|t| !t.is_empty()) {
            head.append(&gtk::Label::builder().label(t).xalign(0.0).css_classes(["share-text"]).build());
        }
        panel.append(&head);
        let grid = gtk::FlowBox::builder()
            .selection_mode(gtk::SelectionMode::None)
            .max_children_per_line(items.len().clamp(1, 3) as u32)
            .min_children_per_line(1)
            .homogeneous(true)
            .column_spacing(8)
            .row_spacing(8)
            .build();
        let mut boxes = Vec::new();
        let mut values = Vec::new();
        for it in items {
            let item = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["share-item"]).build();
            let art = match &it.texture {
                Some(tex) => {
                    let pic = gtk::Picture::builder().paintable(tex).can_shrink(false).css_classes(["share-thumb"]).overflow(gtk::Overflow::Hidden).halign(gtk::Align::Center).valign(gtk::Align::Center).build();
                    let art = gtk::Overlay::builder().child(&gtk::Box::builder().width_request(THUMB_W).height_request(THUMB_H).build()).build();
                    art.add_overlay(&pic);
                    art
                }
                None => {
                    let icon = if it.icon.is_empty() { "window-symbolic" } else { it.icon.as_str() };
                    let img = gtk::Image::builder().icon_name(icon).pixel_size(64).width_request(THUMB_W).height_request(THUMB_H).build();
                    gtk::Overlay::builder().child(&img).build()
                }
            };
            // the app's icon, like macOS' window picker
            if let Some(badge) = &it.badge {
                art.add_overlay(&gtk::Image::builder().gicon(badge).pixel_size(40).halign(gtk::Align::Center).valign(gtk::Align::End).css_classes(["share-badge"]).build());
            }
            item.append(&art);
            item.append(&gtk::Label::builder().label(&it.name).css_classes(["share-name"]).ellipsize(pango::EllipsizeMode::End).max_width_chars(28).build());
            grid.append(&item);
            boxes.push(item);
            values.push(it.value);
        }
        let scroller = gtk::ScrolledWindow::builder()
            .child(&grid)
            .hscrollbar_policy(gtk::PolicyType::Never)
            .propagate_natural_width(true)
            .propagate_natural_height(true)
            .max_content_height(screen_height().map_or(520, grid_max_height))
            .build();
        panel.append(&scroller);
        let buttons = gtk::Box::builder().spacing(8).halign(gtk::Align::End).build();
        panel.append(&buttons);
        win.set_child(Some(&panel));
        let p = Rc::new(Picker { win, values, index: Cell::new(0), items: boxes, on_done: RefCell::new(Some(on_done)) });
        let w = Rc::downgrade(&p);
        let cancel = sonata_ui::controls::push_button(
            &tr!("Cancel"),
            move || {
                if let Some(p) = w.upgrade() {
                    p.finish(None);
                }
            },
            "",
        );
        let w = Rc::downgrade(&p);
        let go = sonata_ui::controls::push_button(
            action,
            move || {
                if let Some(p) = w.upgrade() {
                    p.act();
                }
            },
            "default",
        );
        buttons.append(&cancel);
        buttons.append(&go);
        for (i, item) in p.items.iter().enumerate() {
            let click = gtk::GestureClick::new();
            let w = Rc::downgrade(&p);
            click.connect_pressed(move |_, n, _, _| {
                if let Some(p) = w.upgrade() {
                    p.select(i);
                    if n == 2 {
                        p.act();
                    }
                }
            });
            item.add_controller(click);
        }
        let keys = gtk::EventControllerKey::new();
        let w = Rc::downgrade(&p);
        keys.connect_key_pressed(move |_, key, _, _| match w.upgrade() {
            Some(p) if p.key(key) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        p.win.add_controller(keys);
        if sonata_shell::layer::available() {
            p.win.init_layer_shell();
            p.win.set_namespace(Some("sonata2-share"));
            p.win.set_layer(Layer::Overlay);
            p.win.set_keyboard_mode(KeyboardMode::Exclusive);
        }
        let w = Rc::downgrade(&p);
        p.win.connect_close_request(move |_| {
            if let Some(p) = w.upgrade() {
                p.finish(None);
            }
            glib::Propagation::Stop
        });
        p.select(0);
        // kept alive by its window until it's done
        let keep = p.clone();
        p.win.connect_destroy(move |_| {
            let _ = &keep;
        });
        p
    }

    pub fn present(&self) {
        self.win.present();
    }

    fn select(&self, i: usize) {
        self.index.set(i);
        for (j, it) in self.items.iter().enumerate() {
            if j == i {
                it.add_css_class("selected");
            } else {
                it.remove_css_class("selected");
            }
        }
    }

    fn key(&self, key: gdk::Key) -> bool {
        match key {
            gdk::Key::Escape => self.finish(None),
            gdk::Key::Return | gdk::Key::KP_Enter => self.act(),
            gdk::Key::Left | gdk::Key::Right if !self.items.is_empty() => {
                let n = self.items.len();
                self.select(if key == gdk::Key::Right { (self.index.get() + 1) % n } else { (self.index.get() + n - 1) % n });
            }
            _ => return false,
        }
        true
    }

    fn act(&self) {
        self.finish(self.values.get(self.index.get()).cloned());
    }

    fn finish(&self, value: Option<String>) {
        let Some(done) = self.on_done.borrow_mut().take() else { return };
        self.win.set_visible(false);
        done(value);
        // one per use: thumbnails freed, no hidden windows piling up
        self.win.destroy();
    }
}

/// The ScreenCast portal's picker's choices.
fn share_items(lines: &[String]) -> Vec<PickItem> {
    let parsed: Vec<_> = lines.iter().map(|l| parse(l)).collect();
    let jobs: Vec<_> = parsed.iter().map(|(kind, _, r)| thumb_args(kind, r.as_deref())).map(|args| move || args.and_then(|a| grim_png(&a))).collect();
    let pngs = parallel(jobs);
    let apps = if parsed.iter().any(|p| p.0 == "window") { views_by_title() } else { HashMap::new() };
    lines
        .iter()
        .zip(parsed)
        .zip(pngs)
        .map(|((line, (kind, label, _)), png)| PickItem {
            name: if kind == "screen" { display_name(&label) } else { label.clone() },
            texture: png.and_then(|b| png_texture(&b)),
            badge: if kind == "window" { apps.get(&label).and_then(|a| crate::capture::app_gicon(a)) } else { None },
            icon: if kind == "screen" { "video-display-symbolic".into() } else { "window-symbolic".into() },
            value: line.clone(),
        })
        .collect()
}

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.share"
    } else {
        "io.github.vinioliveiras.sonata2.share"
    }
}

/// `sonata2-rs share-picker`: the portal's choices on stdin, the chosen
/// line on stdout; exit 1 when cancelled (or nothing to choose).
pub fn run_share_picker(_args: &[String]) -> glib::ExitCode {
    sonata_shell::layer::ensure_preload(); // may re-exec (stdin not read yet)
    let lines: Vec<String> = std::io::stdin().lock().lines().map_while(Result::ok).map(|l| l.trim_end_matches('\n').to_string()).filter(|l| !l.trim().is_empty()).collect();
    if lines.is_empty() {
        return glib::ExitCode::FAILURE;
    }
    // only the answer may reach the portal: anything else printed goes to stderr
    // SAFETY: plain descriptor juggling on this process' own stdout / stderr
    let saved = unsafe { libc::dup(1) };
    if saved >= 0 {
        unsafe { libc::dup2(2, 1) };
    }
    let result: Rc<RefCell<Option<String>>> = Rc::default();
    let app = gtk::Application::builder().application_id(app_id()).flags(gio::ApplicationFlags::NON_UNIQUE).build();
    let r = result.clone();
    app.connect_activate(move |app| {
        sonata_ui::setup();
        let (r, a) = (r.clone(), app.clone());
        let picker = Picker::new(
            app,
            &tr!("Choose what to share"),
            Some(&tr!("The app will see everything on the screen you pick.")),
            share_items(&lines),
            &tr!("Share"),
            Box::new(move |line| {
                *r.borrow_mut() = line;
                a.quit();
            }),
        );
        picker.present();
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()]);
    if saved >= 0 {
        unsafe {
            libc::dup2(saved, 1);
            libc::close(saved);
        }
    }
    let Some(line) = result.borrow_mut().take() else { return glib::ExitCode::FAILURE };
    use std::io::Write;
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{line}");
    let _ = out.flush();
    glib::ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::*;

    fn p(line: &str) -> (&'static str, String, Option<String>) {
        parse(line)
    }

    /// Vini: the screen-share picker showed a generic icon for every window
    /// and "WhatsApp (d4ff1290c45e...)" as its name. Windows get their own
    /// thumbnail (grim -T, the toplevel identifier) and the app's icon; the
    /// name is the title.
    #[test]
    fn test_lines() {
        let s = |a: &str| Some(a.to_string());
        assert_eq!(p("Window: WhatsApp (d4ff1290c45e)"), ("window", "WhatsApp".into(), s("d4ff1290c45e")));
        assert_eq!(p("Window: ~ — fish — 80×24 (9ac6b3)"), ("window", "~ — fish — 80×24".into(), s("9ac6b3")));
        assert_eq!(p("Window: Song (Live) (abc)"), ("window", "Song (Live)".into(), s("abc")));
        assert_eq!(p("Window: abc"), ("window", tr!("Window"), s("abc")));
        assert_eq!(p("Monitor: HDMI-A-1 Acer KG241Y"), ("screen", "HDMI-A-1".into(), s("HDMI-A-1")));
        assert_eq!(p("eDP-1"), ("screen", "eDP-1".into(), s("eDP-1")));
        // xdg-desktop-portal-wlr chooser lines: bare connector, "Monitor: ..." and "Window: ...: title"
        assert_eq!(p("HDMI-A-1"), ("screen", "HDMI-A-1".into(), s("HDMI-A-1")));
        assert_eq!(p("Monitor: DP-2"), ("screen", "DP-2".into(), s("DP-2")));
        assert_eq!(p("Window: 42: Firefox: Docs"), ("window", "Firefox: Docs".into(), None));
    }

    #[test]
    fn test_window_thumb_uses_toplevel_capture() {
        assert_eq!(thumb_args("window", Some("abc")).unwrap()[..2], ["-T", "abc"]);
        assert_eq!(thumb_args("screen", Some("DP-1")).unwrap()[..2], ["-o", "DP-1"]);
        assert_eq!(thumb_args("window", None), None);
    }

    #[test]
    fn test_textures_keep_order() {
        let jobs: Vec<_> = (0..9).map(|i| move || i * 2).collect();
        assert_eq!(parallel(jobs), [0, 2, 4, 6, 8, 10, 12, 14, 16]);
        assert!(parallel(Vec::<fn() -> i32>::new()).is_empty());
    }

    /// Many windows to share on a 720 px laptop pushed Cancel / Share off the screen.
    #[test]
    fn test_choices_scroll_on_short_screens() {
        assert_eq!(grid_max_height(720), 520);
        assert_eq!(grid_max_height(200), THUMB_H + 40);
    }

    #[test]
    fn test_thumbnails_fit_never_stretched() {
        assert_eq!(fit_size(1920, 1080), Some((240, 135)));
        assert_eq!(fit_size(1080, 1920), Some((76, 135)));
        assert_eq!(fit_size(10000, 1), Some((240, 1)));
        assert_eq!(fit_size(0, 10), None);
    }
}
