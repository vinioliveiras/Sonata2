//! Sound, Displays (with Wallpaper and Arrange: crate::arrange), Battery.

use crate::kit::{combo_row, group, install_row, nearest, run_async, show_quietly_combo, slider_row, switch_row, Install, WALL_PREVIEW_PX};
use crate::window::Ctx;
use adw::prelude::*;
use gtk::{gdk, gio, glib};
use serde_json::{json, Value};
use sonata_backend::system::{self, AudioDevice, Display};
use sonata_i18n::{fmt, gettext, tr};
use sonata_tweaks::{displaysleep, fpslimit, gamemode, gpu, powerprofile, wallpapers};
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Duration;

/// Python's bool(value) for a setting read back from JSON.
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn bool_of(name: &str, key: &str, default: bool) -> bool {
    let cfg = sonata_config::load(name, &json!({ key: default }));
    truthy(cfg.get(key))
}

/// `work` in a thread, nothing to do after (system.run_async(fn, None, ...)).
fn detach(work: impl FnOnce() + Send + 'static) {
    run_async(work, |_| {});
}

// -- Sound ------------------------------------------------------------------------------------
pub fn page_sound(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let out = group(&tr!("Output"), "");
    let vol = group(&tr!("Volume"), "");
    let eq = group(&tr!("Equalizer"), &tr!("Each output keeps its own settings"));
    let (w, o, v, e) = (Rc::downgrade(ctx), out.clone(), vol.clone(), eq.clone());
    run_async(
        || (system::volume(), system::audio_outputs(), system::audio_inputs(), system::input_volume(), equalizer_available()),
        move |(level, sinks, sources, mic, eq_ok)| {
            let Some(ctx) = w.upgrade() else { return };
            fill_sound(&ctx, &v, &o, level, &sinks, &sources, mic);
            if eq_ok && !sinks.is_empty() {
                equalizer(&e, &sinks);
            } else {
                e.set_visible(false);
            }
        },
    );
    let snd = sonata_config::load("sounds", &sonata_backend::sounds::defaults());
    let effects = group(&tr!("Sound Effects"), "");
    let w = Rc::downgrade(ctx);
    effects.add(&switch_row(&tr!("Play user interface sound effects"), truthy(snd.get("effects")), move |on| {
        if let Some(c) = w.upgrade() {
            c.save("sounds", "effects", json!(on));
        }
    }, &tr!("Moving to the Trash, emptying it, copies finished, screenshots")));
    let w = Rc::downgrade(ctx);
    effects.add(&switch_row(&tr!("Play feedback when volume is changed"), truthy(snd.get("volume_feedback")), move |on| {
        if let Some(c) = w.upgrade() {
            c.save("sounds", "volume_feedback", json!(on));
        }
    }, ""));
    vec![vol, out, eq, effects]
}

#[allow(clippy::too_many_arguments)]
fn fill_sound(ctx: &Ctx, vol: &adw::PreferencesGroup, out: &adw::PreferencesGroup, level: Option<(i64, bool)>, sinks: &[AudioDevice], sources: &[AudioDevice], mic: Option<(i64, bool)>) {
    let Some(level) = level else {
        vol.add(&adw::ActionRow::builder().title(tr!("Output volume")).subtitle(tr!("PipeWire (wpctl) not found")).build());
        out.set_visible(false);
        return;
    };
    let w = Rc::downgrade(ctx);
    let (out_row, out_scale) = slider_row(&tr!("Output volume"), level.0 as f64, 0.0, 100.0, move |x| {
        if let Some(c) = w.upgrade() {
            c.latest("volume", move || {
                system::set_volume(Some(x as i64), None);
            });
        }
    }, "", None, None);
    // feedback on release (once the new volume is in place: sounds.play_soon)
    on_release(&out_scale, || {
        glib::timeout_add_local_once(Duration::from_millis(120), || sonata_backend::sounds::play("volume"));
    });
    vol.add(&out_row);
    vol.add(&switch_row(&tr!("Mute"), level.1, |on| detach(move || {
        system::set_volume(None, Some(on));
    }), ""));
    let mut mic_scale = None;
    if let Some(m) = mic {
        let w = Rc::downgrade(ctx);
        let (row, scale) = slider_row(&tr!("Input volume"), m.0 as f64, 0.0, 100.0, move |x| {
            if let Some(c) = w.upgrade() {
                c.latest("mic", move || {
                    system::set_input_volume(Some(x as i64), None);
                });
            }
        }, "", None, None);
        vol.add(&row);
        mic_scale = Some(scale);
    }
    let snd = sonata_config::load("sounds", &sonata_backend::sounds::defaults());
    let w = Rc::downgrade(ctx);
    vol.add(&switch_row(&tr!("Don't let apps change the volume"), truthy(snd.get("lock_volumes")), move |on| {
        if let Some(c) = w.upgrade() {
            lock_volumes(&c, on);
        }
    }, &tr!("Each speaker and microphone stays where you set it in Sonata; an app turning it down (automatic gain in a call) is put back")));
    follow_levels(vol, &out_scale, mic_scale.as_ref());
    let options: Vec<(String, String)> = sinks.iter().map(|s| (s.key.clone(), s.name.clone())).collect();
    if let Some(first) = options.first() {
        let cur = sinks.iter().find(|s| s.default).map(|s| s.key.clone()).unwrap_or_else(|| first.0.clone());
        out.add(&combo_row(&tr!("Output device"), options.clone(), &cur, |k: &String| {
            let k = k.clone();
            detach(move || {
                system::select_output(&k);
            });
        }, ""));
    }
    let ins: Vec<(String, String)> = sources.iter().map(|s| (s.key.clone(), s.name.clone())).collect();
    if let Some(first) = ins.first() {
        let cur = sources.iter().find(|s| s.default).map(|s| s.key.clone()).unwrap_or_else(|| first.0.clone());
        out.add(&combo_row(&tr!("Input device"), ins.clone(), &cur, |k: &String| {
            let k = k.clone();
            detach(move || {
                system::select_input(&k);
            });
        }, ""));
    }
    let w = Rc::downgrade(ctx);
    out.add(&switch_row(&tr!("Use new headphones right away"), truthy(snd.get("follow_new_devices")), move |on| {
        if let Some(c) = w.upgrade() {
            c.save("sounds", "follow_new_devices", json!(on));
        }
    }, &tr!("USB and Bluetooth headphones and headsets, their microphone too; the device before comes back when they go")));
    out.set_visible(!options.is_empty() || !ins.is_empty());
}

fn lock_volumes(ctx: &Ctx, on: bool) {
    ctx.save("sounds", "lock_volumes", json!(on));
    if on {
        // the devices in use keep what they have now
        detach(|| {
            use sonata_backend::devicevolume as dv;
            let mut data = sonata_config::load(dv::NAME, &json!({"sink": {}, "source": {}}));
            lock_defaults(&mut data, &|kind| dv::pactl(&[&format!("get-default-{kind}")]).trim().to_string(), &|kind| {
                if kind == "sink" { system::volume() } else { system::input_volume() }
            });
            let _ = sonata_config::save(dv::NAME, &data);
        });
    }
}

/// devicevolume.lock_defaults (its Rust half only remembers): turning the
/// lock on, the devices in use keep the volume they have now -- one already
/// remembered keeps its own.
fn lock_defaults(data: &mut serde_json::Map<String, Value>, get: &dyn Fn(&str) -> String, volume: &dyn Fn(&str) -> Option<(i64, bool)>) {
    for kind in ["sink", "source"] {
        let mut devices = data.get(kind).and_then(Value::as_object).cloned().unwrap_or_default();
        let name = get(kind);
        if let (true, Some(v)) = (sonata_backend::devicevolume::usable(&name), volume(kind)) {
            if !devices.contains_key(&name) {
                devices.insert(name, json!(v.0));
            }
        }
        data.insert(kind.into(), Value::Object(devices));
    }
}

/// A `pactl subscribe` line about a sink or a source changing (not "on
/// sink-input": every stream that plays sends those, many a second).
#[cfg_attr(not(feature = "system"), allow(dead_code))]
fn audio_event(text: &str) -> bool {
    text.contains("on sink #") || text.contains("on source #") || text.contains("on server #") // (pactl: "on server #-1", no quotes)
}

/// The sliders follow volume changes made elsewhere (Control Center, keys,
/// other apps) while the page is open.
#[cfg(feature = "system")]
fn follow_levels(anchor: &adw::PreferencesGroup, out: &gtk::Scale, mic: Option<&gtk::Scale>) {
    let handle: Rc<RefCell<Option<sonata_system::pactl_watch::Handle>>> = Rc::default();
    let (a, o, m, h) = (anchor.downgrade(), out.downgrade(), mic.map(|m| m.downgrade()), handle.clone());
    let changed = Rc::new(move || {
        if a.upgrade().is_none_or(|a| a.root().is_none()) {
            // the page is gone
            if let Some(h) = h.borrow_mut().take() {
                h.stop();
            }
            return;
        }
        let (o, m) = (o.clone(), m.clone());
        run_async(|| (system::volume(), system::input_volume()), move |(v, mic)| {
            for (scale, val) in [(o.upgrade(), v), (m.and_then(|m| m.upgrade()), mic)] {
                if let (Some(s), Some(val)) = (scale, val) {
                    if (s.value() - val.0 as f64).abs() >= 1.0 {
                        s.set_value(val.0 as f64);
                    }
                }
            }
        });
    });
    let busy = Rc::new(Cell::new(false));
    *handle.borrow_mut() = sonata_system::pactl_watch::watch(move |text| {
        if audio_event(text) && !busy.get() {
            busy.set(true); // a burst of events: one read
            let (busy, changed) = (busy.clone(), changed.clone());
            glib::timeout_add_local_once(Duration::from_millis(120), move || {
                busy.set(false);
                changed();
            });
        }
    });
    anchor.connect_destroy(move |_| {
        if let Some(h) = handle.borrow_mut().take() {
            h.stop();
        }
    });
}

#[cfg(not(feature = "system"))]
fn follow_levels(_anchor: &adw::PreferencesGroup, _out: &gtk::Scale, _mic: Option<&gtk::Scale>) {}

/// ui.controls.on_release: callback() when a slider is let go (mouse,
/// touch) or an arrow key on it comes up -- not while dragging, and never
/// for set_value() from code (macOS plays the volume feedback once, on release).
fn on_release(widget: &impl IsA<gtk::Widget>, callback: impl Fn() + 'static) {
    let legacy = gtk::EventControllerLegacy::new();
    legacy.set_propagation_phase(gtk::PropagationPhase::Capture);
    legacy.connect_event(move |_, ev| {
        let released = match ev.event_type() {
            gdk::EventType::ButtonRelease => ev.downcast_ref::<gdk::ButtonEvent>().is_some_and(|b| b.button() == 1),
            gdk::EventType::TouchEnd => true,
            gdk::EventType::KeyRelease => ev.downcast_ref::<gdk::KeyEvent>().is_some_and(|k| {
                use gdk::Key;
                [Key::Left, Key::Right, Key::Up, Key::Down, Key::Page_Up, Key::Page_Down, Key::Home, Key::End].contains(&k.keyval())
            }),
            _ => false,
        };
        if released {
            callback();
        }
        glib::Propagation::Proceed // the slider still gets every event
    });
    widget.add_controller(legacy);
}

#[cfg(feature = "system")]
fn equalizer_available() -> bool {
    sonata_system::equalizer::available()
}

#[cfg(not(feature = "system"))]
fn equalizer_available() -> bool {
    false
}

/// The equalizer's preset names, "Custom" last (English: translated where shown).
#[cfg(feature = "system")]
fn eq_names() -> Vec<&'static str> {
    sonata_system::equalizer::presets().map(|p| p.0).chain(["Custom"]).collect()
}

/// The pop-up's row for a curve's preset (an unknown one: Custom).
#[cfg(feature = "system")]
fn eq_preset_index(names: &[&str], preset: &str) -> u32 {
    names.iter().position(|n| *n == preset).unwrap_or(names.len() - 1) as u32
}

/// The equalizer's widgets (weak: the handlers hold this, the page holds
/// the widgets) and what it shows.
#[cfg(feature = "system")]
struct Eq {
    key: RefCell<String>,
    busy: Cell<bool>,
    pending: RefCell<Option<glib::SourceId>>,
    names: Vec<&'static str>,
    on: glib::WeakRef<adw::SwitchRow>,
    preset: glib::WeakRef<adw::ComboRow>,
    row: glib::WeakRef<gtk::ListBoxRow>,
    scales: Vec<glib::WeakRef<gtk::Scale>>,
}

#[cfg(feature = "system")]
impl Eq {
    fn show(&self) {
        let (Some(on), Some(preset), Some(row)) = (self.on.upgrade(), self.preset.upgrade(), self.row.upgrade()) else { return };
        let c = sonata_system::equalizer::curve(&self.key.borrow());
        self.busy.set(true);
        on.set_active(c.on);
        preset.set_selected(eq_preset_index(&self.names, &c.preset));
        for (sc, g) in self.scales.iter().zip(&c.gains) {
            if let Some(sc) = sc.upgrade() {
                sc.set_value(*g);
            }
        }
        preset.set_sensitive(c.on);
        row.set_sensitive(c.on);
        self.busy.set(false);
    }
}

/// Output picker (the one in use first), on/off, preset, ten bands.
#[cfg(feature = "system")]
fn equalizer(grp: &adw::PreferencesGroup, outputs: &[AudioDevice]) {
    use sonata_system::equalizer as E;
    let keys: Vec<(String, String)> = outputs.iter().filter(|d| d.key.contains('|')).map(|d| (d.key.clone(), d.name.clone())).collect();
    let Some(first) = keys.first() else {
        grp.set_visible(false);
        return;
    };
    let key = outputs.iter().find(|d| d.default && d.key.contains('|')).map(|d| d.key.clone()).unwrap_or_else(|| first.0.clone());
    let names = eq_names();
    let on = adw::SwitchRow::builder().title(tr!("Equalizer")).build();
    let labels: Vec<String> = names.iter().map(|n| gettext(n)).collect();
    let labels: Vec<&str> = labels.iter().map(String::as_str).collect();
    let preset = adw::ComboRow::builder().title(tr!("Preset")).model(&gtk::StringList::new(&labels)).build();
    let bands = gtk::Box::builder().homogeneous(true).spacing(6).margin_end(12).build();
    let mut scales = Vec::new();
    // dB scale, like Music's
    let axis = gtk::Box::new(gtk::Orientation::Vertical, 0);
    for (t, align) in [("+12 dB", gtk::Align::Start), ("0 dB", gtk::Align::Center), ("-12 dB", gtk::Align::End)] {
        axis.append(&gtk::Label::builder().label(t).css_classes(["st-caption"]).xalign(1.0).vexpand(true).valign(align).build());
    }
    axis.set_size_request(-1, 150);
    let axis_col = gtk::Box::new(gtk::Orientation::Vertical, 4);
    axis_col.append(&axis);
    axis_col.append(&gtk::Label::builder().label(" ").css_classes(["st-caption"]).build());
    for label in E::LABELS {
        let col = gtk::Box::new(gtk::Orientation::Vertical, 4);
        let sc = gtk::Scale::with_range(gtk::Orientation::Vertical, -E::MAX_DB, E::MAX_DB, 0.5);
        sc.set_inverted(true);
        sc.set_draw_value(false);
        sc.set_size_request(-1, 150);
        sc.set_halign(gtk::Align::Center);
        col.append(&sc);
        col.append(&gtk::Label::builder().label(label).css_classes(["st-caption"]).build());
        bands.append(&col);
        sonata_ui::controls::reset_on_double_click(&sc, 0.0);
        scales.push(sc);
    }
    let wrap = gtk::Box::builder().spacing(8).margin_start(12).margin_top(12).margin_bottom(12).build();
    wrap.append(&axis_col);
    bands.set_hexpand(true);
    bands.set_margin_start(0);
    wrap.append(&bands);
    wrap.add_css_class("st-eq");
    let row = gtk::ListBoxRow::builder().activatable(false).selectable(false).child(&wrap).build();
    let eq = Rc::new(Eq {
        key: RefCell::new(key.clone()),
        busy: Cell::new(false),
        pending: RefCell::new(None),
        names,
        on: on.downgrade(),
        preset: preset.downgrade(),
        row: row.downgrade(),
        scales: scales.iter().map(|s| s.downgrade()).collect(),
    });
    let e = eq.clone();
    on.connect_active_notify(move |r| {
        if !e.busy.get() {
            E::set_curve(&e.key.borrow(), Some(r.is_active()), None, None);
            e.show();
        }
    });
    let e = eq.clone();
    preset.connect_selected_notify(move |r| {
        let name = e.names.get(r.selected() as usize).copied().unwrap_or("Custom");
        if !e.busy.get() && name != "Custom" {
            E::set_curve(&e.key.borrow(), None, Some(name), None);
            e.show();
        }
    });
    for sc in &scales {
        let e = eq.clone();
        sc.connect_value_changed(move |_| {
            if e.busy.get() {
                return;
            }
            if let Some(src) = e.pending.borrow_mut().take() {
                src.remove();
            }
            let e2 = e.clone();
            // dragging: save (and apply) ~8x/s
            let src = glib::timeout_add_local_once(Duration::from_millis(120), move || {
                e2.pending.borrow_mut().take();
                let gains: Vec<f64> = e2.scales.iter().filter_map(|s| s.upgrade()).map(|s| s.value()).collect();
                E::set_curve(&e2.key.borrow(), None, None, Some(&gains));
                let c = E::curve(&e2.key.borrow());
                if let Some(p) = e2.preset.upgrade() {
                    e2.busy.set(true);
                    p.set_selected(eq_preset_index(&e2.names, &c.preset));
                    e2.busy.set(false);
                }
            });
            *e.pending.borrow_mut() = Some(src);
        });
    }
    if keys.len() > 1 {
        let e = eq.clone();
        grp.add(&combo_row(&tr!("Output"), keys, &key, move |k: &String| {
            *e.key.borrow_mut() = k.clone();
            e.show();
        }, ""));
    }
    grp.add(&on);
    grp.add(&preset);
    grp.add(&row);
    eq.show();
}

#[cfg(not(feature = "system"))]
fn equalizer(grp: &adw::PreferencesGroup, _outputs: &[AudioDevice]) {
    grp.set_visible(false);
}

// -- Displays ---------------------------------------------------------------------------------
/// The displays with a brightness slider: the laptop panel and each
/// external monitor (None: a panel wlr-randr didn't list).
fn brightness_names(ds: &[String]) -> Vec<Option<String>> {
    let mut names: Vec<Option<String>> = ds.iter().cloned().map(Some).collect();
    if names.is_empty() {
        names.push(None);
    }
    if !names.iter().any(|n| system::is_builtin(n.as_deref())) {
        names.insert(0, None);
    }
    names
}

/// The main display shown: the one chosen, else what monitors.main()
/// picks -- an external monitor first.
fn main_display(ds: &[Display], chosen: &str) -> String {
    if ds.iter().any(|d| d.name == chosen) {
        return chosen.to_string();
    }
    let external = ds.iter().find(|d| !["eDP", "LVDS", "DSI"].iter().any(|b| d.name.starts_with(b)));
    external.or(ds.first()).map(|d| d.name.clone()).unwrap_or_default()
}

/// "2560x1440@143.912" -> "2560x1440 @ 143 Hz".
fn mode_label(mode: &str) -> String {
    let m = mode.replace('@', " @ ");
    format!("{} Hz", m.split('.').next().unwrap_or(""))
}

/// Every scale monitors.auto_scale picks; never under 100 %.
const SCALES: [(f64, &str); 9] = [(1.0, "100 %"), (1.25, "125 %"), (1.5, "150 %"), (1.75, "175 %"), (2.0, "200 %"), (2.25, "225 %"), (2.5, "250 %"), (2.75, "275 %"), (3.0, "300 %")];

/// A decoded picture as plain data (a Pixbuf can't leave the worker
/// thread): (pixels, has alpha, width, height, rowstride).
type Pixels = (glib::Bytes, bool, i32, i32, i32);

fn pixels_of(pb: &gtk::gdk_pixbuf::Pixbuf) -> Pixels {
    (pb.read_pixel_bytes(), pb.has_alpha(), pb.width(), pb.height(), pb.rowstride())
}

fn pixbuf_of((bytes, alpha, w, h, stride): Pixels) -> gtk::gdk_pixbuf::Pixbuf {
    gtk::gdk_pixbuf::Pixbuf::from_bytes(&bytes, gtk::gdk_pixbuf::Colorspace::Rgb, alpha, 8, w, h, stride)
}

type DisplayRead = (Vec<(Option<String>, Option<i64>)>, Vec<Display>, Option<Pixels>);

fn read_displays() -> DisplayRead {
    let ds = system::displays();
    let panel = system::brightness(None);
    let mut levels = vec![(None, panel)];
    for d in &ds {
        let b = if system::is_builtin(Some(&d.name)) { panel } else { system::brightness(Some(&d.name)) };
        levels.push((Some(d.name.clone()), b));
    }
    let thumb = if ds.len() > 1 { crate::arrange::wallpaper_thumb(320).as_ref().map(pixels_of) } else { None };
    (levels, ds, thumb)
}

pub fn page_displays(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let bright = group(&tr!("Brightness"), "");
    let arranged = group(&tr!("Arrangement"), ""); // only with two displays or more
    arranged.set_visible(false);
    let screens = group(&tr!("Displays"), "");
    let (w, b, a, s) = (Rc::downgrade(ctx), bright.clone(), arranged.clone(), screens.clone());
    run_async(read_displays, move |res| {
        if let Some(ctx) = w.upgrade() {
            fill_displays(&ctx, &b, &a, &s, res);
        }
    });
    let look = group(&tr!("Appearance"), "");
    let w = Rc::downgrade(ctx);
    look.add(&switch_row(&tr!("Rounded screen corners"), bool_of("appearance", "screen_corners", true), move |on| {
        if let Some(c) = w.upgrade() {
            c.save("appearance", "screen_corners", json!(on));
        }
    }, &tr!("The corners of every display rounded (the lock screen too)")));
    let mut pages = vec![bright, arranged, screens, look, night_shift_group(ctx)];
    let nvidia = gpu::card_drivers().iter().any(|d| d == "nvidia");
    let dual = gpu::has_dual_gpu();
    if dual || nvidia {
        let graphics = group(&tr!("Graphics"), "");
        if dual {
            let w = Rc::downgrade(ctx);
            graphics.add(&switch_row(&tr!("Draw with the Displays' Graphics Card"), gpu::compositor_on_display_gpu(), move |on| {
                gpu::set_compositor_on_display_gpu(on);
                if let Some(c) = w.upgrade() {
                    c.ask_restart("session", &tr!("The graphics card change"));
                }
            }, &gpu_subtitle(gpu::crashed_at(), gpu::compositor_on_display_gpu())));
        }
        if nvidia {
            graphics.add(&switch_row(&tr!("Lighter Effects While Gaming"), gamemode::light_effects_wanted(), |on| {
                let _ = sonata_config::update("gpu", &json!({"light_effects": on}));
            }, &tr!("When a full-screen game fills the graphics card, blur and window animations pause until you leave the game.")));
        }
        if dual {
            graphics.add(&switch_row(&tr!("Smart Graphics Switching"), gpu::smart(), gpu::set_smart, &tr!("Games and creative apps use the high-performance graphics; everyday apps use the graphics that draw the screens. An app can have its own choice: right-click it in the Dock or Launchpad. For apps opened after the change.")));
            gpu_card_rows(&graphics, gpu::cards(), gpu::card_name);
        }
        pages.push(graphics);
    }
    pages.push(fps_group());
    pages
}

fn fill_displays(ctx: &Ctx, bright: &adw::PreferencesGroup, arranged: &adw::PreferencesGroup, screens: &adw::PreferencesGroup, (levels, ds, thumb): DisplayRead) {
    // one slider per display: the laptop panel and each external monitor
    // that answers over DDC/CI
    let ds_names: Vec<String> = ds.iter().map(|d| d.name.clone()).collect();
    for n in brightness_names(&ds_names) {
        let d = ds.iter().find(|x| Some(&x.name) == n.as_ref());
        let builtin = system::is_builtin(n.as_deref());
        let title = if builtin {
            tr!("Built-in Display")
        } else {
            d.map(|d| d.description.clone()).filter(|t| !t.is_empty()).or_else(|| n.clone()).unwrap_or_default()
        };
        let level = levels.iter().find(|(k, _)| *k == n).and_then(|(_, b)| *b);
        if let Some(b) = level {
            let (w, n) = (Rc::downgrade(ctx), n.clone());
            bright.add(&slider_row(&title, b as f64, 0.0, 100.0, move |x| {
                let n = n.clone();
                if let Some(c) = w.upgrade() {
                    c.latest(&format!("brightness/{}", n.as_deref().unwrap_or("")), move || {
                        system::set_brightness(x as i64, n.as_deref());
                    });
                }
            }, "", None, None).0);
        } else if builtin {
            bright.add(&adw::ActionRow::builder().title(&title).use_markup(false).subtitle(tr!("No backlight control (brightnessctl)")).build());
        } else {
            bright.add(&adw::ActionRow::builder().title(&title).use_markup(false).subtitle(tr!("Turn on DDC/CI in the monitor's own menu to change its brightness here (needs ddcutil)")).build());
        }
    }
    if ds.len() > 1 {
        let chosen = sonata_config::load("displays", &json!({"main": ""})).get("main").and_then(Value::as_str).unwrap_or("").to_string();
        let main = main_display(&ds, &chosen);
        let set_main = |n: &str| {
            let mut m = serde_json::Map::new();
            m.insert("main".into(), json!(n));
            let _ = sonata_config::save("displays", &m);
            sonata_sessionui::monitors::share_with_login_screen(); // the login screen too (it can't read ~/.config)
        };
        let options: Vec<(String, String)> = ds.iter().map(|d| (d.name.clone(), crate::arrange::display_title(d))).collect();
        let main_row = combo_row(&tr!("Main display"), options, &main, move |n: &String| set_main(n), &tr!("Dock, desktop icons and notifications; every display gets a menu bar"));
        // the menu bar dragged to another display
        let mr = main_row.downgrade();
        let new_main = move |n: &str| {
            set_main(n);
            if let Some(r) = mr.upgrade() {
                show_quietly_combo(&r, &n.to_string());
            }
        };
        // where the displays sit, macOS' Arrange: drag a display, drag the menu bar
        let art = crate::arrange::Arrangement::new(&ds, &main, |pos| {
            let pos: Vec<(String, i64, i64)> = pos.into_iter().map(|(n, (x, y))| (n, x, y)).collect();
            detach(move || {
                system::set_display_positions(&pos);
            });
        }, new_main, thumb.map(pixbuf_of));
        let hint = gtk::Label::builder().label(tr!("Drag the displays to arrange them. Drag the white menu bar to choose the main display.")).wrap(true).xalign(0.0).css_classes(["dim-label"]).margin_top(6).margin_bottom(10).build();
        let frame = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["display-arrange-box"]).build();
        frame.append(&art.widget);
        frame.append(&hint);
        arranged.add(&frame);
        arranged.set_visible(true);
        screens.add(&main_row);
    }
    for d in &ds {
        let saved = system::display_mode_setting(&d.name);
        let mut opts = vec![("highrr".to_string(), tr!("Highest refresh rate"))];
        opts.extend(d.modes.iter().map(|m| (m.clone(), mode_label(m))));
        let selected = if opts.iter().any(|o| o.0 == saved) { saved } else { d.current.clone() };
        let name = d.name.clone();
        screens.add(&combo_row(&d.name, opts, &selected, move |m: &String| {
            let (name, m) = (name.clone(), m.clone());
            detach(move || {
                system::set_display_mode(&name, &m);
            });
        }, &d.description));
        let values: Vec<f64> = SCALES.iter().map(|s| s.0).collect();
        let name = d.name.clone();
        screens.add(&combo_row(&tr!("Scale"), SCALES.iter().map(|(v, l)| (*v, l.to_string())).collect(), &nearest(&values, d.scale), move |s: &f64| {
            let (name, s) = (name.clone(), *s);
            detach(move || {
                system::set_display_scale(&name, s);
            });
        }, ""));
    }
    if ds.is_empty() {
        screens.add(&adw::ActionRow::builder().title(tr!("Displays")).subtitle(tr!("wlr-randr not found or no outputs")).build());
    }
}

/// The fastest display's refresh rate (the FPS limit's Max follows it;
/// shell/fpsmodule's, which lives in the Game Bar here).
fn refresh_hz() -> f64 {
    sonata_shell::layer::monitors().iter().map(|m| m.refresh_rate() as f64 / 1000.0).fold(0.0, f64::max)
}

/// Displays > Games: limit games' frame rate (frame-pacer; off by default,
/// the same choice as Control Center > FPS Limit).
fn fps_group() -> adw::PreferencesGroup {
    let g = group(&tr!("Games"), "");
    let ok = fpslimit::installed();
    let cfg = fpslimit::settings();
    let choices: Vec<(String, String)> = fpslimit::CHOICES
        .iter()
        .map(|c| {
            let label = match *c {
                "off" => tr!("Off"),
                "max" => tr!("Display's rate"),
                n => fmt(&tr!("{n} FPS"), &[("n", n)]),
            };
            (c.to_string(), label)
        })
        .collect();
    let limit = combo_row(&tr!("Limit Frame Rate"), choices, &fpslimit::get(), |v: &String| fpslimit::set(v, Some(refresh_hz()), None), &tr!("Vulkan and Proton games opened from Steam, Faugus, Lutris, Heroic or Bottles (frame-pacer). Also in Control Center (Add Controls)."));
    let hud = switch_row(&tr!("Show frame-pacer's Overlay"), truthy(cfg.get("hud")), |on| fpslimit::set(&fpslimit::get(), Some(refresh_hz()), Some(on)), &tr!("Its frame rate and timings in the corner of the game"));
    limit.set_sensitive(ok);
    hud.set_sensitive(ok);
    g.add(&limit);
    g.add(&hud);
    if !ok {
        let row = adw::ActionRow::builder().title(tr!("frame-pacer isn't installed")).subtitle(tr!("Downloaded from its GitHub releases into your home folder (no password). Steam picks it up the next time it opens.")).build();
        let btn = gtk::Button::with_label(&tr!("Install"));
        btn.add_css_class("sonata-button");
        btn.set_valign(gtk::Align::Center);
        let (r, l, h) = (row.downgrade(), limit.downgrade(), hud.downgrade());
        btn.connect_clicked(move |b| {
            b.set_label(&tr!("Installing…"));
            b.set_sensitive(false);
            let (b, r, l, h) = (b.downgrade(), r.clone(), l.clone(), h.clone());
            fpslimit::install(move |err| {
                if let Some(b) = b.upgrade() {
                    b.set_label(&tr!("Install"));
                    b.set_sensitive(true);
                }
                let Some(row) = r.upgrade() else { return };
                if !err.is_empty() {
                    row.set_subtitle(&fmt(&tr!("Couldn't install: {error}"), &[("error", &err)]));
                } else {
                    row.set_visible(false);
                    for w in [l.upgrade().map(|w| w.upcast::<gtk::Widget>()), h.upgrade().map(|w| w.upcast::<gtk::Widget>())].into_iter().flatten() {
                        w.set_sensitive(true);
                    }
                }
            });
        });
        row.add_suffix(&btn);
        g.add(&row);
    }
    g
}

/// The two Graphics pickers' options (None with fewer than two cards):
/// Automatic (Sonata's pick, named) or one card forced by the user.
#[allow(clippy::type_complexity)]
fn gpu_card_options(cs: &[gpu::Card], name: impl Fn(&gpu::Card) -> String) -> Option<(Vec<(String, String)>, Vec<(String, String)>)> {
    if cs.len() < 2 {
        return None;
    }
    let mut names: Vec<(String, String)> = Vec::new();
    for c in cs {
        if !names.iter().any(|(t, _)| *t == c.tag) {
            names.push((c.tag.clone(), name(c)));
        }
    }
    let best = gpu::best_discrete(cs).and_then(|b| names.iter().find(|(t, _)| *t == b.tag));
    let auto = match best {
        Some((_, card)) => fmt(&tr!("Automatic ({card})"), &[("card", card)]),
        None => tr!("Automatic"),
    };
    let games = std::iter::once((String::new(), auto)).chain(names.iter().cloned()).collect();
    let apps = std::iter::once((String::new(), tr!("Automatic"))).chain(names).collect();
    Some((games, apps))
}

/// Which card games and everyday apps use: Automatic (Sonata's pick, named)
/// or one forced by the user (Vini: in case Sonata guesses wrong).
fn gpu_card_rows(graphics: &adw::PreferencesGroup, cs: &[gpu::Card], name: impl Fn(&gpu::Card) -> String) {
    let Some((games, apps)) = gpu_card_options(cs, name) else { return };
    let cfg = sonata_config::load(gpu::NAME, &gpu::defaults());
    let get = |k: &str| cfg.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    graphics.add(&combo_row(&tr!("Graphics for Games"), games, &get("games_gpu"), |tag: &String| gpu::set_card("games", tag), &tr!("Games, creative apps and apps set to use high-performance graphics.")));
    graphics.add(&combo_row(&tr!("Graphics for Apps"), apps, &get("apps_gpu"), |tag: &String| gpu::set_card("apps", tag), &tr!("Everyday apps, while Smart Graphics Switching is on. A card without displays of its own can keep some apps from opening.")));
}

/// Why the switch is off when a crash turned it off (it looked like it
/// turned itself off -- Vini). crashed: when (epoch seconds); on: the switch.
fn gpu_subtitle(crashed: Option<f64>, on: bool) -> String {
    let base = tr!("Smoother on displays wired to the discrete card, but some NVIDIA drivers can end the session. From the next login.");
    let Some(when) = crashed.filter(|_| !on) else { return base };
    let stamp = glib::DateTime::from_unix_local(when as i64).ok().and_then(|t| t.format("%-d %b, %H:%M").ok()).map(|s| s.to_string()).unwrap_or_default();
    fmt(&tr!("Turned off after the session ended on {when}: the card refused memory."), &[("when", &stamp)]) + " " + &base
}

/// Night Shift's From / To choices, every half hour.
fn night_times() -> Vec<(String, String)> {
    (0..24).flat_map(|h| [0, 30].map(|m| format!("{h:02}:{m:02}"))).map(|t| (t.clone(), t)).collect()
}

/// Displays > Night Shift (macOS sheet as a group): schedule, custom
/// times, Turn On Until Tomorrow, Colour Temperature.
fn night_shift_group(ctx: &Ctx) -> adw::PreferencesGroup {
    use sonata_sessionui::nightshift;
    let cfg = sonata_config::load(nightshift::NAME, &nightshift::defaults());
    let g = group(&tr!("Night Shift"), &tr!("Night Shift shifts the colours of your display to the warmer end of the spectrum after dark."));
    if sonata_backend::run::which("wlsunset").is_none() {
        let w = Rc::downgrade(ctx);
        let (row, _) = install_row(&tr!("Night Shift needs wlsunset"), &tr!("Not installed"), system::install_command("wlsunset", None).as_deref(), Install {
            ready: Some(Rc::new(|| sonata_backend::run::which("wlsunset").is_some())),
            on_ready: Some(Rc::new(move || {
                if let Some(c) = w.upgrade() {
                    c.reload_page("displays");
                }
            })),
            ..Default::default()
        });
        g.add(&row);
        return g;
    }
    let save = |k: &str, v: Value| {
        let mut m = serde_json::Map::new();
        m.insert(k.into(), v);
        let _ = sonata_config::update(nightshift::NAME, &Value::Object(m));
    };
    let get = |k: &str| cfg.get(k).and_then(Value::as_str).unwrap_or("").to_string();
    let frm = combo_row(&tr!("From"), night_times(), &get("from"), move |v: &String| save("from", json!(v)), "");
    let to = combo_row(&tr!("To"), night_times(), &get("to"), move |v: &String| save("to", json!(v)), "");
    let (f, t) = (frm.downgrade(), to.downgrade());
    let schedules = vec![("off".to_string(), tr!("Off")), ("custom".to_string(), tr!("Custom")), ("sunset".to_string(), tr!("Sunset to Sunrise"))];
    g.add(&combo_row(&tr!("Schedule"), schedules, &get("schedule"), move |v: &String| {
        save("schedule", json!(v));
        for r in [f.upgrade(), t.upgrade()].into_iter().flatten() {
            r.set_visible(v == "custom");
        }
    }, ""));
    g.add(&frm);
    g.add(&to);
    frm.set_visible(get("schedule") == "custom");
    to.set_visible(get("schedule") == "custom");
    g.add(&switch_row(&tr!("Turn On Until Tomorrow"), nightshift::manual_active(&cfg), nightshift::set_manual, &tr!("Manual")));
    let warmth = cfg.get("warmth").and_then(Value::as_f64).unwrap_or(50.0);
    let w = Rc::downgrade(ctx);
    let (less, more) = (tr!("Less Warm"), tr!("More Warm"));
    g.add(&slider_row(&tr!("Colour Temperature"), warmth, 0.0, 100.0, move |v| {
        if let Some(c) = w.upgrade() {
            c.save_live("nightshift", "warmth", json!(v as i64));
        }
    }, "", Some((&less, &more)), Some(50.0)).0);
    g
}

// -- Battery ----------------------------------------------------------------------------------
/// Display sleep's choices, seconds (-1: never).
fn sleep_options() -> Vec<(i64, String)> {
    vec![(60, tr!("1 minute")), (120, tr!("2 minutes")), (300, tr!("5 minutes")), (600, tr!("10 minutes")), (1200, tr!("20 minutes")), (1800, tr!("30 minutes")), (-1, tr!("Never"))]
}

/// The option closest to the seconds read back (the first of two as close).
fn nearest_seconds(options: &[i64], cur: i64) -> i64 {
    options.iter().copied().min_by_key(|v| (v - cur).abs()).unwrap_or(cur)
}

/// The Energy mode shown: Automatic while it is only raised for a
/// full-screen game (it goes back by itself).
fn shown_profile(profile: &str, boosted: bool) -> &str {
    if profile == "performance" && boosted { "balanced" } else { profile }
}

/// The battery page follows power changes (plugging in, the level, Low
/// Power Mode) while it is shown; watched once per window.
#[cfg(feature = "system")]
fn follow_power(ctx: &Ctx) {
    if ctx.kept::<()>("battery-watch").is_some() {
        return;
    }
    ctx.keep("battery-watch", Rc::new(()));
    let w = Rc::downgrade(ctx);
    sonata_system::power::watch(move || {
        if let Some(c) = w.upgrade() {
            if c.current() == Some("battery") {
                c.reload_page("battery");
            }
        }
    });
}

#[cfg(feature = "system")]
fn battery_icon(pct: Option<i64>, status: &str, ac: bool, profile: Option<&str>) -> Option<String> {
    // the menu bar's icon, same rule
    Some(sonata_system::power::icon_name(pct, status, ac, profile))
}

#[cfg(not(feature = "system"))]
fn battery_icon(_pct: Option<i64>, _status: &str, _ac: bool, _profile: Option<&str>) -> Option<String> {
    None
}

pub fn page_battery(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    #[cfg(feature = "system")]
    follow_power(ctx);
    #[cfg(not(feature = "system"))]
    let _ = ctx;
    let info = group("", "");
    let mode = group(&tr!("Energy Mode"), &tr!("Automatic switches to High Performance while a game or another app is full screen, and back when it closes."));
    let (i, m) = (info.clone(), mode.clone());
    run_async(|| (system::battery(system::POWER_SUPPLY), system::on_ac(system::POWER_SUPPLY), system::power_profile()), move |((pct, status), ac, prof)| {
        let subtitle = match pct {
            Some(p) => format!("{p}% · {}", if status.is_empty() { String::new() } else { gettext(&status) }),
            None => tr!("No battery"),
        };
        let level = adw::ActionRow::builder().title(tr!("Battery level")).subtitle(subtitle).build();
        if pct.is_some() || !status.is_empty() {
            if let Some(icon) = battery_icon(pct, &status, ac, prof.as_deref()) {
                level.add_prefix(&gtk::Image::builder().icon_name(icon).pixel_size(24).build());
            }
        }
        i.add(&level);
        i.add(&adw::ActionRow::builder().title(tr!("Power source")).subtitle(if ac { tr!("Power Adapter") } else { tr!("Battery") }).build());
        match prof.filter(|p| !p.is_empty()) {
            Some(prof) => {
                let pick = |p: &String| {
                    if gamemode::boosted() {
                        // raised for a full-screen game
                        if p == "balanced" {
                            return;
                        }
                        gamemode::set_boosted(false);
                    }
                    powerprofile::remember(p); // back to it when plugged in again
                    let p = p.clone();
                    detach(move || {
                        system::set_power_profile(&p);
                    });
                };
                let shown = shown_profile(&prof, gamemode::boosted()).to_string();
                let options = system::POWER_PROFILES.iter().map(|(k, v)| (k.to_string(), gettext(v))).collect();
                m.add(&combo_row(&tr!("Energy mode"), options, &shown, pick, ""));
            }
            None => m.add(&adw::ActionRow::builder().title(tr!("Energy mode")).subtitle(tr!("power-profiles-daemon not available")).build()),
        }
    });
    let screen = group(&tr!("Display"), "");
    let cur = displaysleep::seconds(); // Sonata's own (Wayfire's display timeout stays off)
    let opts = sleep_options();
    let values: Vec<i64> = opts.iter().map(|o| o.0).collect();
    screen.add(&combo_row(&tr!("Turn display off after"), opts, &nearest_seconds(&values, cur), |v: &i64| {
        let v = *v;
        detach(move || displaysleep::set_seconds(v));
    }, ""));
    vec![info, mode, screen]
}

// -- Wallpaper --------------------------------------------------------------------------------
const BG: &str = "org.gnome.desktop.background";
/// shell/videowallpaper's defaults (the desktop's focus.rs keeps them; not
/// a dependency of Settings)
const VIDEO_VOLUME_DEFAULT: i64 = 25;
const ALWAYS_DEFAULT: bool = false;
const PER_DISPLAY_DEFAULT: bool = true;

/// (light uri, dark uri) the desktop uses now.
fn shown_wallpaper() -> (String, String) {
    let light = sonata_tweaks::prefs_write::get(BG, "picture-uri");
    let dark = Some(sonata_tweaks::prefs_write::get(BG, "picture-uri-dark")).filter(|d| !d.is_empty()).unwrap_or_else(|| light.clone());
    (light, dark)
}

/// The widgets refresh() updates, weak: the tiles' click handlers hold this
/// (Python's `alive`: refs to the page from them kept it from ever dying).
struct Wall {
    pic: glib::WeakRef<gtk::Picture>,
    row: glib::WeakRef<adw::ActionRow>,
    tiles: Vec<(&'static str, glib::WeakRef<gtk::Button>)>,
    load: Cell<u32>,
}

impl Wall {
    fn refresh(self: &Rc<Self>) {
        let (Some(pic), Some(row)) = (self.pic.upgrade(), self.row.upgrade()) else { return };
        let (light, dark) = shown_wallpaper();
        let uri = if adw::StyleManager::default().is_dark() { &dark } else { &light };
        let f = (!uri.is_empty()).then(|| gio::File::for_uri(uri));
        let path = f.as_ref().filter(|f| f.query_exists(gio::Cancellable::NONE)).and_then(|f| f.path());
        match path {
            Some(path) if sonata_tweaks::videowall::is_video(&path.to_string_lossy()) => {
                // a video wallpaper: its poster (made by ffmpeg the first time: off the UI thread)
                let p = self.pic.clone();
                run_async(move || sonata_tweaks::videowall::poster(&path), move |still| {
                    if let (Some(still), Some(pic)) = (still, p.upgrade()) {
                        pic.set_filename(Some(&still));
                    }
                });
            }
            Some(path) => {
                // decoded in a thread, at the preview's size: a 4K picture took
                // ~160 ms of the page's opening and kept ~40 MB for a 180 px view
                // (the one shown stays until the new one is ready: no blank flash)
                let load = self.load.get() + 1;
                self.load.set(load);
                let me = Rc::downgrade(self);
                run_async(move || preview_pixels(&path), move |px| {
                    let Some(me) = me.upgrade() else { return };
                    if me.load.get() != load {
                        return;
                    }
                    if let Some(pic) = me.pic.upgrade() {
                        let tex = px.map(|(bytes, alpha, w, h, stride)| {
                            let format = if alpha { gdk::MemoryFormat::R8g8b8a8 } else { gdk::MemoryFormat::R8g8b8 };
                            gdk::MemoryTexture::new(w, h, format, &bytes, stride as usize)
                        });
                        pic.set_paintable(tex.as_ref());
                    }
                });
            }
            None => {
                self.load.set(self.load.get() + 1);
                pic.set_paintable(None::<&gdk::Paintable>);
            }
        }
        let cur = wallpapers::current(&light, &dark);
        for (id, b) in &self.tiles {
            if let Some(b) = b.upgrade() {
                if *id == cur {
                    b.add_css_class("selected");
                } else {
                    b.remove_css_class("selected");
                }
            }
        }
        let name = wallpapers::catalog().into_iter().find(|w| w.id == cur).map(|w| gettext(w.name));
        let base = f.and_then(|f| f.basename()).map(|b| b.to_string_lossy().into_owned());
        row.set_subtitle(&name.or(base).unwrap_or_else(|| tr!("None")));
    }

    fn set(self: &Rc<Self>, light: &str, dark: &str) {
        sonata_tweaks::prefs_write::set_wallpaper(light, Some(dark)); // (the desktop's wallpaper starts again, fresh)
        self.refresh();
    }
}

/// The picture at most WALL_PREVIEW_PX a side (orientation applied).
fn preview_pixels(path: &std::path::Path) -> Option<Pixels> {
    let pb = gtk::gdk_pixbuf::Pixbuf::from_file_at_scale(path, WALL_PREVIEW_PX, WALL_PREVIEW_PX, true).ok()?;
    Some(pixels_of(&pb.apply_embedded_orientation().unwrap_or(pb)))
}

pub fn page_wallpaper(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let g = group(&tr!("Wallpaper"), &tr!("Sonata draws it; apps that show the desktop picture get it too."));
    let pic = gtk::Picture::builder().content_fit(gtk::ContentFit::Cover).css_classes(["st-wall"]).height_request(180).can_shrink(true).overflow(gtk::Overflow::Hidden).build();
    g.add(&pic);
    // Sonata's own pictures (Vini): a click sets them for Light and Dark
    let gallery = group(&tr!("Sonata Wallpapers"), "");
    let grid = gtk::FlowBox::builder().max_children_per_line(5).min_children_per_line(4).selection_mode(gtk::SelectionMode::None).row_spacing(12).column_spacing(12).homogeneous(true).build();
    let chooser = group("", ""); // its own group: the page's standard gap below the picture
    let row = adw::ActionRow::builder().title(tr!("Picture or Video")).build();
    let mut tiles = Vec::new();
    let mut buttons = Vec::new();
    for w in wallpapers::catalog() {
        let thumb = gtk::Picture::builder().content_fit(gtk::ContentFit::Cover).can_shrink(true).width_request(88).height_request(58).overflow(gtk::Overflow::Hidden).build();
        thumb.set_filename(Some(&wallpapers::thumb(&w)));
        let name = gettext(w.name);
        let tip = if w.light != w.dark { fmt(&tr!("{name} (changes with Light and Dark)"), &[("name", &name)]) } else { name.clone() };
        let b = gtk::Button::builder().child(&thumb).css_classes(["st-wall-tile"]).tooltip_text(tip).build();
        let col = gtk::Box::new(gtk::Orientation::Vertical, 5);
        col.append(&b);
        col.append(&gtk::Label::builder().label(&name).css_classes(["st-wall-name"]).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(12).build());
        tiles.push((w.id, b.downgrade()));
        buttons.push((b, w));
        grid.append(&col);
    }
    gallery.add(&grid);
    let wall = Rc::new(Wall { pic: pic.downgrade(), row: row.downgrade(), tiles, load: Cell::new(0) });
    for (b, w) in buttons {
        let wall = wall.clone();
        b.connect_clicked(move |_| wall.set(&wallpapers::uri(&w.light), &wallpapers::uri(&w.dark)));
    }
    let choose = gtk::Button::with_label(&tr!("Choose…"));
    choose.add_css_class("sonata-button");
    choose.set_valign(gtk::Align::Center);
    let (wl, win) = (wall.clone(), ctx.window.downgrade());
    choose.connect_clicked(move |_| {
        if let Some(win) = win.upgrade() {
            let wl = wl.clone();
            pick_wallpaper(&win, move |uri| wl.set(&uri, &uri));
        }
    });
    row.add_suffix(&choose);
    chooser.add(&row);
    wall.refresh();
    let sm = adw::StyleManager::default();
    let me = Rc::downgrade(&wall);
    let hid = sm.connect_dark_notify(move |_| {
        if let Some(w) = me.upgrade() {
            w.refresh();
        }
    });
    // the page gone (rebuilt, or the window closed): let the handler go
    let hid = RefCell::new(Some(hid));
    pic.connect_destroy(move |_| {
        if let Some(h) = hid.borrow_mut().take() {
            adw::StyleManager::default().disconnect(h);
        }
    });
    // the tiles' handlers hold `wall`; it lives as long as they do
    vec![g, gallery, chooser, video_sound_group(ctx)]
}

/// Sonata's own Open panel (Files' chooser), pictures and videos: GTK's
/// file dialog went through the portal and did nothing here (Vini), and it
/// isn't Sonata's look anyway.
#[cfg(feature = "system")]
fn pick_wallpaper(win: &adw::ApplicationWindow, use_it: impl Fn(String) + 'static) {
    use sonata_files::chooser::{Chooser, Mode, Options, MIME};
    let Some(app) = win.application() else { return };
    let filters = vec![(tr!("Pictures and Videos"), vec![(MIME, "image/*".to_string()), (MIME, "video/*".to_string())])];
    let o = Options { mode: Mode::Open, title: tr!("Choose a Picture or Video"), accept_label: String::new(), multiple: false, filters, current_filter: 0, folder: None, name: String::new() };
    let dlg = Chooser::new(&app, o, Box::new(move |uris, _| {
        if let Some(uri) = uris.and_then(|u| u.into_iter().next()) {
            use_it(uri);
        }
    }));
    dlg.window().set_transient_for(Some(win));
    dlg.window().set_modal(true);
    dlg.present();
}

/// Without Files (no `system` feature): GTK's file dialog.
#[cfg(not(feature = "system"))]
fn pick_wallpaper(win: &adw::ApplicationWindow, use_it: impl Fn(String) + 'static) {
    let flt = gtk::FileFilter::new();
    flt.set_name(Some(&tr!("Pictures and Videos")));
    flt.add_mime_type("image/*");
    flt.add_mime_type("video/*");
    let store = gio::ListStore::new::<gtk::FileFilter>();
    store.append(&flt);
    let dlg = gtk::FileDialog::builder().title(tr!("Choose a Picture or Video")).modal(true).filters(&store).build();
    dlg.open(Some(win), gio::Cancellable::NONE, move |res| {
        // (an error: cancelled)
        if let Ok(f) = res {
            use_it(f.uri().to_string());
        }
    });
}

/// A video wallpaper's sound (Vini): off by default, at 25 %.
fn video_sound_group(ctx: &Ctx) -> adw::PreferencesGroup {
    let cfg = sonata_config::load("wallpaper", &json!({"video_sound": false, "video_volume": VIDEO_VOLUME_DEFAULT}));
    let g = group(&tr!("Video Wallpaper"), &tr!("When the wallpaper is a video."));
    let w = Rc::downgrade(ctx);
    let (quiet, loud) = (tr!("Quiet"), tr!("Loud"));
    let (vol, _) = slider_row(&tr!("Volume"), cfg.get("video_volume").and_then(Value::as_f64).unwrap_or(VIDEO_VOLUME_DEFAULT as f64), 0.0, 100.0, move |v| {
        if let Some(c) = w.upgrade() {
            c.save_live("wallpaper", "video_volume", json!(v.round_ties_even() as i64));
        }
    }, "", Some((&quiet, &loud)), Some(VIDEO_VOLUME_DEFAULT as f64));
    let sound_on = truthy(cfg.get("video_sound"));
    vol.set_sensitive(sound_on);
    let (w, v) = (Rc::downgrade(ctx), vol.downgrade());
    g.add(&switch_row(&tr!("Play sound"), sound_on, move |on| {
        if let Some(c) = w.upgrade() {
            c.save("wallpaper", "video_sound", json!(on));
        }
        if let Some(v) = v.upgrade() {
            v.set_sensitive(on);
        }
    }, &tr!("The video's own sound, when the wallpaper is a video")));
    g.add(&vol);
    let always_on = bool_of("wallpaper", "video_always", ALWAYS_DEFAULT);
    let w = Rc::downgrade(ctx);
    let per = switch_row(&tr!("Pause each display on its own"), bool_of("wallpaper", "video_per_display", PER_DISPLAY_DEFAULT), move |on| {
        if let Some(c) = w.upgrade() {
            c.save("wallpaper", "video_per_display", json!(on));
        }
    }, &tr!("A display with no window keeps playing while another has windows"));
    per.set_sensitive(!always_on);
    let (w, p) = (Rc::downgrade(ctx), per.downgrade());
    g.add(&switch_row(&tr!("Keep playing with windows open"), always_on, move |on| {
        if let Some(c) = w.upgrade() {
            c.save("wallpaper", "video_always", json!(on));
        }
        if let Some(p) = p.upgrade() {
            p.set_sensitive(!on);
        }
    }, &tr!("Even with maximized windows; still paused in Overview, in full screen and when locked")));
    g.add(&per);
    g
}

#[cfg(test)]
mod tests {
    use super::*;

    fn display(name: &str) -> Display {
        Display { name: name.into(), description: String::new(), modes: Vec::new(), current: String::new(), scale: 1.0, x: 0, y: 0, enabled: true, transform: "normal".into() }
    }

    #[test]
    fn only_device_events_wake_the_sliders() {
        assert!(audio_event("Event 'change' on sink #56"));
        assert!(audio_event("Event 'new' on source #12"));
        assert!(audio_event("Event 'change' on server #4294967295")); // a new default device
        assert!(!audio_event("Event 'change' on sink-input #210"));
    }

    /// devicevolume's lock_defaults: the devices in use keep their volume;
    /// one already remembered, a monitor or the equalizer are left alone.
    #[test]
    fn lock_keeps_the_volume_of_the_devices_in_use() {
        let mut data = json!({"sink": {"hdmi": 40}, "source": {}}).as_object().unwrap().clone();
        lock_defaults(&mut data, &|k| if k == "sink" { "hdmi".into() } else { "usb-mic".into() }, &|k| Some((if k == "sink" { 70 } else { 55 }, false)));
        assert_eq!(data["sink"], json!({"hdmi": 40}));
        assert_eq!(data["source"], json!({"usb-mic": 55}));
        let mut data = json!({"sink": {}, "source": {}}).as_object().unwrap().clone();
        lock_defaults(&mut data, &|k| if k == "sink" { "sonata-eq-x".into() } else { "a.monitor".into() }, &|_| Some((10, false)));
        assert_eq!(data["sink"], json!({}));
        assert_eq!(data["source"], json!({}));
    }

    #[test]
    fn brightness_rows_include_the_panel() {
        assert_eq!(brightness_names(&[]), vec![None]);
        assert_eq!(brightness_names(&["eDP-1".into(), "HDMI-A-1".into()]), vec![Some("eDP-1".into()), Some("HDMI-A-1".into())]);
        // a desktop: a panel wlr-randr didn't list comes first
        assert_eq!(brightness_names(&["DP-1".into()]), vec![None, Some("DP-1".into())]);
    }

    #[test]
    fn main_display_chosen_else_external() {
        let ds = [display("eDP-1"), display("HDMI-A-1")];
        assert_eq!(main_display(&ds, ""), "HDMI-A-1");
        assert_eq!(main_display(&ds, "eDP-1"), "eDP-1");
        assert_eq!(main_display(&ds, "gone"), "HDMI-A-1");
        assert_eq!(main_display(&[display("eDP-1"), display("LVDS-1")], ""), "eDP-1");
    }

    #[test]
    fn mode_labels() {
        assert_eq!(mode_label("2560x1440@143.912"), "2560x1440 @ 143 Hz");
        assert_eq!(mode_label("1920x1080@60.000"), "1920x1080 @ 60 Hz");
    }

    /// Settings > Displays > Graphics: two pickers, Automatic named after Sonata's pick.
    #[test]
    fn settings_card_rows() {
        let card = |tag: &str, drv: &str, discrete: bool| gpu::Card { tag: tag.into(), addr: String::new(), driver: drv.into(), discrete, vram: 0 };
        let (nv, amd) = (card("pci-0000_01_00_0", "nvidia", true), card("pci-0000_36_00_0", "amdgpu", false));
        let name = |c: &gpu::Card| if c.driver == "nvidia" { "RTX".to_string() } else { "680M".to_string() };
        let (games, apps) = gpu_card_options(&[nv.clone(), amd.clone()], name).unwrap();
        assert_eq!(games[0], (String::new(), fmt(&tr!("Automatic ({card})"), &[("card", "RTX")])));
        assert_eq!(apps[0], (String::new(), tr!("Automatic")));
        assert_eq!(&games[1..], &[(nv.tag.clone(), "RTX".to_string()), (amd.tag.clone(), "680M".to_string())]);
        assert_eq!(games[1..], apps[1..]);
        assert!(gpu_card_options(&[nv], name).is_none());
    }

    /// The switch "turned itself off" with no word (Vini): after a crash
    /// the subtitle says why; on again, the old crash is history.
    #[test]
    fn gpu_subtitle_says_why_it_is_off() {
        let base = gpu_subtitle(None, false);
        let after = gpu_subtitle(Some(1000.0), false);
        assert_ne!(after, base);
        assert!(after.ends_with(&format!(" {base}")));
        assert_eq!(gpu_subtitle(Some(1000.0), true), base);
    }

    #[test]
    fn night_shift_times_every_half_hour() {
        let t = night_times();
        assert_eq!(t.len(), 48);
        assert_eq!(t[0].0, "00:00");
        assert_eq!(t[1].0, "00:30");
        assert_eq!(t[47].0, "23:30");
    }

    #[test]
    fn display_sleep_nearest_choice() {
        let v: Vec<i64> = sleep_options().iter().map(|o| o.0).collect();
        assert_eq!(nearest_seconds(&v, 600), 600);
        assert_eq!(nearest_seconds(&v, 400), 300);
        assert_eq!(nearest_seconds(&v, 90), 60); // as close to both: the first
        assert_eq!(nearest_seconds(&v, -1), -1);
    }

    #[test]
    fn energy_mode_shown_as_automatic_while_a_game_raised_it() {
        assert_eq!(shown_profile("performance", true), "balanced");
        assert_eq!(shown_profile("performance", false), "performance");
        assert_eq!(shown_profile("power-saver", true), "power-saver");
    }

    #[cfg(feature = "system")]
    #[test]
    fn equalizer_custom_for_an_unknown_preset() {
        let names = eq_names();
        assert_eq!(*names.last().unwrap(), "Custom");
        assert_eq!(eq_preset_index(&names, "Flat"), 0);
        assert_eq!(eq_preset_index(&names, "Mine"), names.len() as u32 - 1);
    }

    #[test]
    fn truthy_like_python() {
        assert!(!truthy(None));
        assert!(!truthy(Some(&json!(0))));
        assert!(truthy(Some(&json!(1))));
        assert!(!truthy(Some(&json!(""))));
        assert!(truthy(Some(&json!(true))));
    }
}
