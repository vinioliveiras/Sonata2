//! `sonata2-rs session-try <what>`: the session helpers that can be looked
//! at next to the running Python Sonata without fighting it. Nothing here
//! writes a setting, the gamma, the displays' config or the idle state.
//!
//!   session-try corners      a window with the rounded screen corners drawn over it
//!   session-try blackout     the idle blackout for 3 s (the pointer comes back after)
//!   session-try alarm        an alarm card ringing (Stop / Snooze close it)
//!   session-try timer        the timer's card
//!   session-try idle         the swayidle command and idle policy the settings give
//!   session-try nightshift   the wlsunset command, and whether it is on now
//!   session-try displays     each display's automatic scale and the main one
//!
//! The Setup Assistant has its own: `sonata2-rs setup --preview`.

use adw::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;

const USAGE: &str = "usage: sonata2-rs session-try <corners|blackout|alarm|timer|idle|nightshift|displays>";

pub fn run_try(args: &[String]) -> glib::ExitCode {
    let what = args.first().map(String::as_str).unwrap_or("");
    match what {
        "idle" => {
            let cfg = sonata_config::load(crate::idlelock::NAME, &crate::idlelock::defaults());
            let dpms = sonata_shell::lockdisplay::display_seconds();
            println!("display off after: {dpms} s; lock_after: {}", cfg.get("lock_after").cloned().unwrap_or_default());
            println!("swayidle (no idle watch): {:?}", crate::idlelock::command(&cfg, dpms, crate::idlelock::keyboard_light(), &crate::idlelock::lock_command()));
            println!("locked now: {}", crate::idlelock::is_locked());
            glib::ExitCode::SUCCESS
        }
        "nightshift" => {
            let cfg = sonata_config::load(crate::nightshift::NAME, &crate::nightshift::defaults());
            println!("wlsunset: {:?}", crate::nightshift::command(&cfg));
            println!("time zone location: {:?}", crate::nightshift::tz_location());
            println!("on now: {}", crate::nightshift::is_on());
            glib::ExitCode::SUCCESS
        }
        "corners" | "blackout" | "alarm" | "timer" | "displays" => gui(what),
        _ => {
            eprintln!("{USAGE}");
            glib::ExitCode::from(2)
        }
    }
}

fn gui(what: &str) -> glib::ExitCode {
    sonata_shell::layer::ensure_preload();
    let app = adw::Application::builder().application_id("io.github.vinioliveiras.sonata2rs.sessiontry").flags(gio::ApplicationFlags::NON_UNIQUE).build();
    let what = what.to_string();
    let keep: Rc<RefCell<Vec<Box<dyn std::any::Any>>>> = Rc::default();
    app.connect_activate(move |app| {
        sonata_ui::setup();
        match what.as_str() {
            "corners" => {
                let win = gtk::ApplicationWindow::builder().application(app).title("Screen corners").default_width(640).default_height(400).build();
                let over = gtk::Overlay::new();
                let bg = gtk::DrawingArea::builder().hexpand(true).vexpand(true).build();
                bg.set_draw_func(|_, cr, _, _| {
                    cr.set_source_rgb(1.0, 1.0, 1.0);
                    let _ = cr.paint();
                });
                over.set_child(Some(&bg));
                over.add_overlay(&crate::screencorners::corners_overlay(crate::screencorners::RADIUS * 3));
                win.set_child(Some(&over));
                win.present();
            }
            "blackout" => {
                let hold = app.hold();
                crate::blackout::show();
                glib::timeout_add_seconds_local_once(3, move || {
                    crate::blackout::hide();
                    glib::timeout_add_local_once(std::time::Duration::from_millis(crate::blackout::FADE_OUT_MS + 100), move || drop(hold));
                });
            }
            "alarm" | "timer" => {
                let timer = what == "timer";
                let r = crate::alarmservice::Ringing {
                    head: if timer { crate::alarmservice::HEAD_TIMER } else { crate::alarmservice::HEAD_ALARM },
                    label: if timer { sonata_i18n::tr!("Timer") } else { sonata_i18n::tr!("Alarm") },
                    time_text: if timer { sonata_clock::timers::duration_text(300.0) } else { "07:00".into() },
                    snooze: !timer,
                    sound: if timer { sonata_clock::timers::SOUND.into() } else { sonata_clock::alarms::SOUND.into() },
                    alarm: None,
                };
                let ringer = Rc::new(RefCell::new(crate::alarmservice::Ringer::default()));
                ringer.borrow_mut().start(&r.sound);
                let card: Rc<RefCell<Option<gtk::Window>>> = Rc::default();
                let close = {
                    let (ringer, card) = (ringer.clone(), card.clone());
                    move || {
                        ringer.borrow_mut().stop();
                        if let Some(c) = card.borrow_mut().take() {
                            c.destroy();
                        }
                    }
                };
                let c2 = close.clone();
                let w = crate::alarmservice::alarm_card(app.upcast_ref(), &r, close, c2);
                w.present();
                *card.borrow_mut() = Some(w);
                keep.borrow_mut().push(Box::new(ringer));
            }
            "displays" => {
                let main = crate::monitors::main().map(|m| crate::monitors::connector(&m)).unwrap_or_default();
                for m in crate::monitors::list() {
                    let g = m.geometry();
                    let k = m.scale_factor();
                    let c = crate::monitors::connector(&m);
                    let s = crate::monitors::auto_scale(g.width() * k, g.height() * k, m.width_mm(), m.height_mm(), sonata_backend::system::is_builtin(Some(&c)) && !c.is_empty());
                    println!("{c}: {}x{} px, {}x{} mm -> auto scale {}{}", g.width() * k, g.height() * k, m.width_mm(), m.height_mm(), s, if c == main { " (main)" } else { "" });
                }
            }
            _ => {}
        }
    });
    app.run_with_args(&[] as &[&str])
}
