//! Settings > App Icons (appicons_page.py): every app's icon -- Sonata's
//! artwork, the package's own, a picture of your choice or a theme icon --
//! and the frame's shape, for all apps or one (icons.json, icons.rs). The
//! Dock and Launchpad follow live. Built from Sonata's kit: Settings rows,
//! ui panels, ui controls.

use crate::applist::{AppList, AppRow};
use crate::kit::{combo_row, group};
use crate::window::{Ctx, Settings};
use adw::prelude::*;
use gtk::{gio, glib};
use serde_json::{json, Map, Value};
use sonata_i18n::{fmt, gettext, n_, tr};
use sonata_ui::icons::{self, SHAPES, SHAPE_TITLES, SOURCES};
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};
use std::time::Duration;

pub const SOURCE_TITLES: [(&str, &str); 4] = [("auto", n_!("Sonata (default)")), ("package", n_!("The app's own")), ("file", n_!("Custom picture")), ("theme", n_!("Theme icon"))];
pub const PANEL_W: i32 = 300;
pub const LABEL_W: i32 = 70;

fn title_of(table: &[(&str, &str)], key: &str) -> String {
    table.iter().find(|(k, _)| *k == key).map(|(_, t)| gettext(t)).unwrap_or_default()
}

/// What a row says of an app's icon: its source, and its shape when not the default one.
pub fn describe(pref: &icons::AppPref, default_shape: &str) -> String {
    let mut text = title_of(&SOURCE_TITLES, &pref.source);
    if pref.shape != default_shape {
        text.push_str(" · ");
        text.push_str(&title_of(&SHAPE_TITLES, &pref.shape));
    }
    text
}

/// icons.set_app_pref (not in sonata-ui's icons yet): change one app's
/// choices (None removes a key; nothing left: back to the choice for all).
pub fn set_app_pref(did: &str, values: &[(&str, Option<Value>)]) {
    let data = icons::prefs();
    let mut apps = data.get("apps").and_then(Value::as_object).cloned().unwrap_or_default();
    let mut cur = apps.get(did).and_then(Value::as_object).cloned().unwrap_or_default();
    for (k, v) in values {
        match v {
            None => {
                cur.remove(*k);
            }
            Some(v) => {
                cur.insert(k.to_string(), v.clone());
            }
        }
    }
    if cur.get("source") == data.get("source") {
        cur.remove("source"); // the same as all apps': nothing of its own
    }
    if cur.is_empty() {
        apps.remove(did);
    } else {
        apps.insert(did.to_string(), Value::Object(cur));
    }
    let _ = sonata_config::update("icons", &json!({ "apps": apps }));
}

/// Every app from Sonata's artwork or from its package (Vini): an app set
/// the same way loses its own setting, custom pictures stay.
pub fn set_source_all(source: &str) {
    let data = icons::prefs();
    let mut apps = Map::new();
    for (did, p) in data.get("apps").and_then(Value::as_object).cloned().unwrap_or_default() {
        let mut p = p.as_object().cloned().unwrap_or_default();
        if p.get("source").and_then(Value::as_str).is_some_and(|s| s == "auto" || s == "package") {
            p.remove("source");
        }
        if !p.is_empty() {
            apps.insert(did, Value::Object(p));
        }
    }
    let _ = sonata_config::update("icons", &json!({ "source": source, "apps": apps }));
}

/// The pieces of one app's open panel (the tests look at them).
pub struct Panel {
    pub popover: gtk::Popover,
    pub source: gtk::DropDown,
    pub shape: gtk::DropDown,
    pub choose: gtk::Button,
    pub name: gtk::Entry,
    pub preview: gtk::Image,
    pub size: gtk::Scale,
    pub file_rv: gtk::Revealer,
    pub name_rv: gtk::Revealer,
    pub size_rv: gtk::Revealer,
}

pub struct AppIconsPage {
    ctx: Weak<Settings>,
    me: Weak<AppIconsPage>,
    pub list: RefCell<Option<Rc<AppList>>>,
    pub shape_row: RefCell<Option<adw::ComboRow>>,
    pub panel: RefCell<Option<Rc<Panel>>>,
    size_src: RefCell<Option<glib::SourceId>>,
}

impl AppIconsPage {
    pub fn new(ctx: &Ctx) -> Rc<Self> {
        sonata_ui::css::register_module("settings/appicons_page.py");
        Rc::new_cyclic(|me| AppIconsPage { ctx: Rc::downgrade(ctx), me: me.clone(), list: RefCell::new(None), shape_row: RefCell::new(None), panel: RefCell::new(None), size_src: RefCell::new(None) })
    }

    fn call(&self, f: impl Fn(&Rc<AppIconsPage>) + 'static) -> impl Fn() + 'static {
        let w = self.me.clone();
        move || {
            if let Some(p) = w.upgrade() {
                f(&p);
            }
        }
    }

    pub fn groups(self: &Rc<Self>) -> Vec<adw::PreferencesGroup> {
        let p = icons::prefs();
        let shape = group(&tr!("All Apps"), &tr!("The frame every app icon sits on, and where the icons come from. One app can have its own (below)."));
        let current = p.get("shape").and_then(Value::as_str).unwrap_or("squircle").to_string();
        let w = self.me.clone();
        let row = combo_row(&tr!("Icon shape"), SHAPES.iter().map(|s| (s.to_string(), title_of(&SHAPE_TITLES, s))).collect(), &current, move |s: &String| {
            if let Some(p) = w.upgrade() {
                p.set_shape(s);
            }
        }, "");
        shape.add(&row);
        *self.shape_row.borrow_mut() = Some(row);
        let every = adw::ActionRow::builder().title(tr!("All apps' icons")).use_markup(false).subtitle(tr!("Pictures and theme icons you chose for an app stay")).build();
        let btns = gtk::Box::builder().spacing(8).valign(gtk::Align::Center).build();
        btns.append(&sonata_ui::controls::push_button(&tr!("Sonata's"), self.call(|p| p.set_source_all("auto")), ""));
        btns.append(&sonata_ui::controls::push_button(&tr!("From Packages"), self.call(|p| p.set_source_all("package")), ""));
        every.add_suffix(&btns);
        shape.add(&every);
        let reset = adw::ActionRow::builder().title(tr!("Reset app icons")).use_markup(false).subtitle(tr!("Forget what you set for single apps; the shape and icons above stay")).build();
        let rb = sonata_ui::controls::push_button(&tr!("Reset…"), self.call(|p| {
            p.ask_reset_all();
        }), "destructive");
        rb.set_valign(gtk::Align::Center);
        reset.add_suffix(&rb);
        shape.add(&reset);
        let w = self.me.clone();
        let list = AppList::new(
            &tr!("Apps"),
            &tr!("Click an app to change its icon."),
            Rc::new(move |row: &AppRow| {
                if let Some(p) = w.upgrade() {
                    p.edit(row);
                }
            }),
            Some(Rc::new(|row: &AppRow| Some(describe(&icons::app_pref(&row.info), &icons::frame_shape())))),
        );
        let g = list.group.clone();
        *self.list.borrow_mut() = Some(list);
        vec![shape, g]
    }

    /// Open one app's form (Launchpad's "Change Icon…").
    pub fn focus(&self, did: &str) {
        if let Some(l) = self.list.borrow().clone() {
            l.focus(did);
        }
    }

    pub fn refresh(&self) {
        if let Some(l) = self.list.borrow().clone() {
            l.refresh();
        }
    }

    // -- changes -------------------------------------------------------------------------------------
    pub fn set_shape(&self, shape: &str) {
        let _ = sonata_config::update("icons", &json!({ "shape": shape }));
        self.refresh();
    }

    pub fn set_source_all(&self, source: &str) {
        set_source_all(source);
        self.refresh();
    }

    /// Asked first: custom pictures and per-app shapes are forgotten.
    pub fn ask_reset_all(&self) -> Option<adw::AlertDialog> {
        let ctx = self.ctx.upgrade()?;
        let w = self.me.clone();
        Some(sonata_ui::dialog::alert(
            &tr!("Reset app icons?"),
            &tr!("The pictures, theme icons, sizes and shapes you set for single apps are forgotten. The shape and icons chosen for all apps stay."),
            &[("cancel", tr!("Cancel"), ""), ("reset", tr!("Reset"), "destructive")],
            move |rid| {
                if let (true, Some(p)) = (rid == "reset", w.upgrade()) {
                    p.reset_all();
                }
            },
            ctx.parent().as_ref(),
        ))
    }

    /// Every app back to the choice for all apps (Vini: only what was set
    /// per app goes; the shape and the icons' source stay).
    pub fn reset_all(&self) {
        let _ = sonata_config::update("icons", &json!({ "apps": {} }));
        self.refresh();
    }

    pub fn set_app(&self, row: &AppRow, values: &[(&str, Option<Value>)]) {
        set_app_pref(&row.did, values);
        if let Some(l) = self.list.borrow().clone() {
            l.refresh_row(row);
        }
    }

    // -- one app's panel -----------------------------------------------------------------------------
    /// A form like macOS' (labels right-aligned, controls in one column of
    /// one width); rows that only apply to one kind of icon slide in/out.
    pub fn edit(&self, row: &AppRow) -> Rc<Panel> {
        let pref = icons::app_pref(&row.info);
        let default_shape = icons::frame_shape();
        let preview = gtk::Image::builder().pixel_size(72).css_classes(["ai-preview"]).halign(gtk::Align::Center).build();
        // no row spacing: a hidden row (collapsed Revealer) must leave no gap; rows pad themselves
        let grid = gtk::Grid::builder().column_spacing(10).row_spacing(0).css_classes(["ai-form"]).build();
        let at = Cell::new(0);
        let add = |label: &str, widget: &gtk::Widget, reveal: bool| -> Option<gtk::Revealer> {
            let lab = gtk::Label::builder().label(label).xalign(1.0).width_chars(1).css_classes(["ai-label"]).build();
            lab.set_size_request(LABEL_W, -1);
            widget.set_hexpand(true);
            for w in [lab.upcast_ref::<gtk::Widget>(), widget] {
                w.set_margin_top(4);
                w.set_margin_bottom(4);
            }
            let n = at.get();
            at.set(n + 1);
            if !reveal {
                grid.attach(&lab, 0, n, 1, 1);
                grid.attach(widget, 1, n, 1, 1);
                return None;
            }
            let bx = gtk::Box::builder().spacing(10).build();
            bx.append(&lab);
            bx.append(widget);
            let rv = gtk::Revealer::builder().child(&bx).transition_type(gtk::RevealerTransitionType::SlideDown).transition_duration(sonata_ui::tokens::ms(180.0)).build();
            grid.attach(&rv, 0, n, 2, 1);
            Some(rv)
        };
        // redraw() is set once every widget it shows exists
        let redraw: Rc<RefCell<Rc<dyn Fn()>>> = Rc::new(RefCell::new(Rc::new(|| {})));
        let again = {
            let r = redraw.clone();
            move || {
                let f = r.borrow().clone();
                f();
            }
        };
        let (me, r, a) = (self.me.clone(), row.clone(), again.clone());
        let source = sonata_ui::controls::popup_button(
            &SOURCES.iter().map(|s| title_of(&SOURCE_TITLES, s)).collect::<Vec<_>>(),
            SOURCES.iter().position(|s| *s == pref.source).unwrap_or(0) as u32,
            move |i| {
                if let Some(p) = me.upgrade() {
                    p.set_app(&r, &[("source", Some(json!(SOURCES[i as usize])))]);
                    a();
                }
            },
        );
        add(&tr!("Icon"), source.upcast_ref(), false);
        let pick_box = gtk::Box::builder().spacing(8).build();
        let (me, r, a) = (self.me.clone(), row.clone(), again.clone());
        let choose = sonata_ui::controls::push_button(&tr!("Choose…"), move || {
            if let Some(p) = me.upgrade() {
                p.pick_file(&r, a.clone());
            }
        }, "");
        let file_lbl = gtk::Label::builder().xalign(0.0).hexpand(true).ellipsize(gtk::pango::EllipsizeMode::Middle).css_classes(["ai-file"]).build();
        pick_box.append(&file_lbl);
        pick_box.append(&choose);
        let file_rv = add(&tr!("Picture"), pick_box.upcast_ref(), true).expect("revealer");
        let name = sonata_ui::controls::text_field(&pref.name, &tr!("e.g. firefox"));
        name.add_css_class("ai-name");
        let commit = {
            let (me, r, a, name) = (self.me.clone(), row.clone(), again.clone(), name.downgrade());
            move || {
                let (Some(p), Some(name)) = (me.upgrade(), name.upgrade()) else { return };
                let typed = name.text().trim().to_string();
                // Return, or leaving the field (closing the panel)
                if typed != icons::app_pref(&r.info).name {
                    p.set_app(&r, &[("name", (!typed.is_empty()).then(|| json!(typed))), ("source", Some(json!("theme")))]);
                    a();
                }
            }
        };
        let c = commit.clone();
        name.connect_activate(move |_| c());
        let leave = gtk::EventControllerFocus::new();
        leave.connect_leave(move |_| commit());
        name.add_controller(leave);
        let name_rv = add(&tr!("Name"), name.upcast_ref(), true).expect("revealer");
        let cur_scale = pref.scale.unwrap_or_else(icons::default_scale);
        let size = sonata_ui::controls::slider(cur_scale * 100.0, |_| {}, icons::SCALE_RANGE.0 * 100.0, icons::SCALE_RANGE.1 * 100.0, Some(icons::default_scale() * 100.0));
        let (me, r, a) = (self.me.clone(), row.clone(), again.clone());
        size.connect_value_changed(move |sl| {
            if let Some(p) = me.upgrade() {
                p.size_changed(&r, sl.value(), a.clone());
            }
        });
        let size_rv = add(&tr!("Size"), size.upcast_ref(), true).expect("revealer");
        let own_shape = icons::prefs().get("apps").and_then(|a| a.get(&row.did)).and_then(|p| p.get("shape")).and_then(Value::as_str).map(str::to_string);
        let mut shape_titles = vec![fmt(&tr!("Default ({shape})"), &[("shape", &title_of(&SHAPE_TITLES, &default_shape))])];
        shape_titles.extend(SHAPES.iter().map(|s| title_of(&SHAPE_TITLES, s)));
        let (me, r, a) = (self.me.clone(), row.clone(), again.clone());
        let shape = sonata_ui::controls::popup_button(&shape_titles, own_shape.as_deref().and_then(|s| SHAPES.iter().position(|x| *x == s)).map(|i| i + 1).unwrap_or(0) as u32, move |i| {
            if let Some(p) = me.upgrade() {
                let v = if i == 0 { None } else { Some(json!(SHAPES[i as usize - 1])) };
                p.set_app(&r, &[("shape", v)]);
                a();
            }
        });
        add(&tr!("Shape"), shape.upcast_ref(), false);
        {
            let (preview, info, file_lbl, file_rv, name_rv, size_rv) = (preview.clone(), row.info.clone(), file_lbl.clone(), file_rv.clone(), name_rv.clone(), size_rv.clone());
            *redraw.borrow_mut() = Rc::new(move || {
                icons::set_image(&preview, &icons::app_icon(&info));
                let p = icons::app_pref(&info);
                file_rv.set_reveal_child(p.source == "file");
                file_lbl.set_label(&if p.path.is_empty() { tr!("No picture") } else { p.path.rsplit('/').next().unwrap_or("").to_string() });
                name_rv.set_reveal_child(p.source == "theme");
                size_rv.set_reveal_child(icons::plated(&info));
            });
        }
        let btns = gtk::Box::builder().spacing(8).css_classes(["ai-buttons"]).homogeneous(true).build();
        let pop_cell: Rc<RefCell<Option<gtk::Popover>>> = Rc::new(RefCell::new(None));
        let (me, r, pc) = (self.me.clone(), row.clone(), pop_cell.clone());
        btns.append(&sonata_ui::controls::push_button(&tr!("Use Default"), move || {
            if let Some(p) = me.upgrade() {
                p.set_app(&r, &[("source", None), ("path", None), ("name", None), ("shape", None), ("scale", None)]);
            }
            if let Some(pop) = pc.borrow().as_ref() {
                pop.popdown();
            }
        }, ""));
        let pc = pop_cell.clone();
        btns.append(&sonata_ui::controls::push_button(&tr!("Done"), move || {
            if let Some(pop) = pc.borrow().as_ref() {
                pop.popdown();
            }
        }, "default"));
        let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["ai-panel"]).build();
        col.append(&sonata_ui::panel::header(&row.info.display_name(), None));
        col.append(&preview);
        col.append(&grid);
        col.append(&btns);
        col.set_size_request(PANEL_W, -1);
        let pop = sonata_ui::panel::popup(&row.row, &col, gtk::PositionType::Bottom);
        *pop_cell.borrow_mut() = Some(pop.clone());
        again();
        let panel = Rc::new(Panel { popover: pop, source, shape, choose, name, preview, size, file_rv, name_rv, size_rv });
        *self.panel.borrow_mut() = Some(panel.clone());
        panel
    }

    /// Saved (and the icon drawn again) once the slider pauses.
    fn size_changed(&self, row: &AppRow, value: f64, then: impl Fn() + 'static) {
        if let Some(src) = self.size_src.borrow_mut().take() {
            src.remove();
        }
        let (me, r) = (self.me.clone(), row.clone());
        let src = glib::timeout_add_local_once(Duration::from_millis(150), move || {
            let Some(p) = me.upgrade() else { return };
            p.size_src.borrow_mut().take();
            let v = icons::clamp_scale(value / 100.0);
            let scale = if (v - icons::default_scale()).abs() < 0.005 { None } else { Some(json!(v)) };
            p.set_app(&r, &[("scale", scale)]);
            then();
        });
        *self.size_src.borrow_mut() = Some(src);
    }

    fn pick_file(&self, row: &AppRow, then: impl Fn() + 'static) {
        let Some(ctx) = self.ctx.upgrade() else { return };
        let (me, r) = (self.me.clone(), row.clone());
        pick_picture(&ctx.window, move |path| {
            if let Some(p) = me.upgrade() {
                p.set_app(&r, &[("source", Some(json!("file"))), ("path", Some(json!(path)))]);
                then();
            }
        });
    }
}

/// A picture through the Open panel (Sonata's own, via the portal):
/// on_path(path) when one was chosen. (App Icons; the New Web App form.)
pub fn pick_picture(parent: &impl IsA<gtk::Window>, on_path: impl FnOnce(String) + 'static) {
    let flt = gtk::FileFilter::new();
    flt.set_name(Some(&tr!("Images")));
    for pat in ["*.png", "*.svg", "*.jpg", "*.jpeg", "*.webp"] {
        flt.add_pattern(pat);
    }
    let store = gio::ListStore::new::<gtk::FileFilter>();
    store.append(&flt);
    let dlg = gtk::FileDialog::builder().title(tr!("Choose an Icon")).modal(true).filters(&store).build();
    dlg.open(Some(parent), gio::Cancellable::NONE, move |res| {
        // (an error: cancelled)
        if let Some(path) = res.ok().and_then(|f| f.path()) {
            on_path(path.to_string_lossy().into_owned());
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn with_config<T>(f: impl FnOnce() -> T) -> T {
        let _lock = crate::tests::CONFIG.lock().unwrap_or_else(|e| e.into_inner());
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("XDG_CONFIG_HOME", dir.path());
        f()
    }

    #[test]
    fn source_and_shape_described() {
        let pref = |source: &str, shape: &str| icons::AppPref { source: source.into(), path: String::new(), name: String::new(), scale: None, shape: shape.into() };
        // (in the tests' language)
        assert_eq!(describe(&pref("auto", "squircle"), "squircle"), gettext("Sonata (default)"));
        assert_eq!(describe(&pref("theme", "circle"), "squircle"), format!("{} · {}", gettext("Theme icon"), gettext("Circle")));
    }

    #[test]
    fn one_apps_choice_and_back() {
        with_config(|| {
            set_app_pref("org.test.App", &[("source", Some(json!("theme"))), ("name", Some(json!("firefox")))]);
            let p = icons::pref_of(&icons::prefs(), "org.test.App");
            assert_eq!((p.source.as_str(), p.name.as_str()), ("theme", "firefox"));
            // the same source as every app's: nothing of its own
            set_app_pref("org.test.App", &[("source", Some(json!("auto"))), ("name", None)]);
            assert!(icons::prefs()["apps"].get("org.test.App").is_none());
        });
    }

    #[test]
    fn all_from_packages_keeps_pictures() {
        with_config(|| {
            set_app_pref("a", &[("source", Some(json!("package")))]);
            set_app_pref("b", &[("source", Some(json!("file"))), ("path", Some(json!("/x.png")))]);
            set_source_all("package");
            let p = icons::prefs();
            assert_eq!(p["source"], "package");
            assert!(p["apps"].get("a").is_none());
            assert_eq!(p["apps"]["b"]["source"], "file");
        });
    }
}
