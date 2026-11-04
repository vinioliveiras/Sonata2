//! Calculator (macOS Calculator, Basic), ported from
//! `sonata2/calculator/window.py`: a small glass window, the display on top
//! (under the compositor's glass title bar), 5 x 4 round-cornered keys:
//! function keys, digits, orange operators. The keyboard works too:
//! digits . + - * / x Enter = % Backspace Escape (AC) Delete (C), ⌘C /
//! Ctrl+C copies the result, Ctrl+V pastes a number. The operator waiting
//! for a number stays highlighted (white with orange text), as on a Mac.

use crate::engine::Engine;
use gtk::gdk::{self, Key, ModifierType};
use gtk::{glib, pango, prelude::*};
use sonata_i18n::tr;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::{Rc, Weak};
use std::time::Duration;

const CSS: &str = r#"
window.sonata-calculator { background: %(calc_bg)s; }
.calc-display { padding: 8px 16px 2px 16px; }
.calc-display label { color: %(calc_display)s; font-family: %(font_display)s; font-weight: 300; }
.calc-keys { padding: 6px 8px 10px 8px; }
button.calc-key { min-width: 48px; min-height: 42px; padding: 0; border-radius: 8px; border: none;
  font-size: 20px; font-weight: 400; box-shadow: none; background: %(calc_key)s; color: %(calc_key_text)s;
  transition: background-color 90ms ease-out, filter 90ms ease-out; }
button.calc-key:hover { filter: brightness(1.08); }
button.calc-key:active, button.calc-key.pressed { filter: brightness(1.35); transition: none; }
button.calc-key.fn { background: %(calc_key_fn)s; font-size: 17px; }
button.calc-key.op { background: %(calc_key_op)s; color: white; font-size: 24px; }
button.calc-key.op.armed { background: white; color: %(calc_key_op)s; }
"#;

/// (label, kind): "fn" grey, "op" orange, "zero" two columns wide.
const KEYS: [&[(&str, &str)]; 5] = [
    &[("AC", "fn"), ("±", "fn"), ("%", "fn"), ("÷", "op")],
    &[("7", ""), ("8", ""), ("9", ""), ("×", "op")],
    &[("4", ""), ("5", ""), ("6", ""), ("−", "op")],
    &[("1", ""), ("2", ""), ("3", ""), ("+", "op")],
    &[("0", "zero"), (".", ""), ("=", "op")],
];

fn key_for(keyval: Key) -> Option<&'static str> {
    Some(match keyval {
        Key::plus | Key::KP_Add => "+",
        Key::minus | Key::KP_Subtract => "−",
        Key::asterisk | Key::KP_Multiply | Key::x | Key::X => "×",
        Key::slash | Key::KP_Divide => "÷",
        Key::equal | Key::Return | Key::KP_Enter => "=",
        Key::percent => "%",
        Key::period | Key::comma | Key::KP_Decimal | Key::KP_Separator => ".",
        Key::BackSpace => "⌫",
        Key::Escape => "AC",
        Key::Delete => "C",
        _ => return None,
    })
}

/// A plain decimal, window.py's `-?(\d+\.?\d*|\.\d+)` ("-12.5", "3.",
/// ".5"): "1e5", "inf" etc. would be typed wrong (the e dropped).
fn plain_number(text: &str) -> bool {
    let body = text.strip_prefix('-').unwrap_or(text);
    let digits = |s: &str| s.bytes().all(|b| b.is_ascii_digit());
    match body.split_once('.') {
        None => !body.is_empty() && digits(body),
        Some(("", frac)) => !frac.is_empty() && digits(frac),
        Some((whole, frac)) => digits(whole) && digits(frac),
    }
}

pub struct Calculator {
    pub window: gtk::ApplicationWindow,
    engine: RefCell<Engine>,
    display: gtk::Label,
    buttons: HashMap<&'static str, gtk::Button>,
}

impl Calculator {
    pub fn new(app: &impl IsA<gtk::Application>) -> Rc<Self> {
        sonata_ui::register("calculator", CSS);
        let window = gtk::ApplicationWindow::builder().application(app).title(tr!("Calculator")).resizable(false).build();
        window.add_css_class("sonata-calculator");
        window.add_css_class("sonata-glass");
        sonata_ui::window::standard(&window);

        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let display = gtk::Label::builder().xalign(1.0).ellipsize(pango::EllipsizeMode::None).selectable(false).build();
        display.set_hexpand(true);
        let disp = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        disp.add_css_class("calc-display");
        disp.append(&display);
        col.append(&gtk::WindowHandle::builder().child(&disp).build());

        let grid = gtk::Grid::builder().row_spacing(6).column_spacing(6).row_homogeneous(true).column_homogeneous(true).build();
        grid.add_css_class("calc-keys");
        let mut buttons = HashMap::new();
        for (r, row) in KEYS.iter().enumerate() {
            let mut c = 0;
            for &(key, kind) in row.iter() {
                let b = gtk::Button::with_label(key);
                b.add_css_class("calc-key");
                if kind == "fn" || kind == "op" {
                    b.add_css_class(kind);
                }
                b.set_can_focus(false);
                let width = if kind == "zero" { 2 } else { 1 };
                grid.attach(&b, c, r as i32, width, 1);
                buttons.insert(key, b);
                c += width;
            }
        }
        col.append(&grid);
        // the title bar is the glass one the compositor draws (pixdecor)
        let over = gtk::Overlay::new();
        over.set_child(Some(&col));
        window.set_child(Some(&over));

        let calc = Rc::new(Calculator { window, engine: RefCell::new(Engine::new()), display, buttons });
        for (key, b) in &calc.buttons {
            let (weak, key) = (Rc::downgrade(&calc), *key);
            b.connect_clicked(move |_| {
                if let Some(c) = weak.upgrade() {
                    c.press(key);
                }
            });
        }
        let keys = gtk::EventControllerKey::new();
        let weak = Rc::downgrade(&calc);
        keys.connect_key_pressed(move |_, keyval, _code, state| match weak.upgrade() {
            Some(c) if c.key(keyval, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        calc.window.add_controller(keys);
        let weak = Rc::downgrade(&calc);
        sonata_ui::on_change(move || {
            if let Some(c) = weak.upgrade() {
                c.refresh();
            }
        });
        calc.refresh();
        calc
    }

    // -- input --------------------------------------------------------------------------
    pub fn press(&self, key: &str) {
        let key = if key == "AC" { self.engine.borrow().clear_label() } else { key }; // the key reads C or AC
        self.engine.borrow_mut().press(key);
        self.refresh();
    }

    fn key(self: &Rc<Self>, keyval: Key, state: ModifierType) -> bool {
        let ctrl = state.intersects(ModifierType::CONTROL_MASK | ModifierType::SUPER_MASK);
        if ctrl && matches!(keyval, Key::c | Key::C) {
            let text = self.engine.borrow().display().replace(',', "");
            self.window.clipboard().set_text(&text);
            return true;
        }
        if ctrl && matches!(keyval, Key::v | Key::V) {
            let weak: Weak<Self> = Rc::downgrade(self);
            self.window.clipboard().read_text_async(gtk::gio::Cancellable::NONE, move |res| {
                if let (Some(c), Ok(Some(text))) = (weak.upgrade(), res) {
                    c.pasted(&text);
                }
            });
            return true;
        }
        if ctrl {
            return false;
        }
        let digit = keyval.to_unicode().filter(char::is_ascii_digit).map(|d| d.to_string());
        let Some(key) = digit.or_else(|| key_for(keyval).map(str::to_string)) else { return false };
        self.press(&key);
        let shown = match key.as_str() {
            "C" => Some("AC"),
            "⌫" => None,
            k => Some(k),
        };
        if let Some(b) = shown.and_then(|k| self.buttons.get(k)) {
            // the key lights up, like a click
            b.add_css_class("pressed");
            let b = b.clone();
            glib::timeout_add_local_once(Duration::from_millis(110), move || b.remove_css_class("pressed"));
        }
        true
    }

    fn pasted(&self, text: &str) {
        let text = text.trim().replace(',', "");
        if !plain_number(&text) {
            return;
        }
        let neg = text.starts_with('-');
        {
            let mut e = self.engine.borrow_mut();
            for ch in text.trim_start_matches('-').chars() {
                e.press(&ch.to_string());
            }
            if neg {
                e.press("±"); // ± after the digits negates the new entry
            }
        }
        self.refresh();
    }

    // -- output -------------------------------------------------------------------------
    fn refresh(&self) {
        let e = self.engine.borrow();
        let mut text = e.display();
        if text == "Error" {
            text = tr!("Error"); // (the engine's word for ÷0 and overflow)
        }
        self.display.set_label(&text);
        // the number shrinks to fit (macOS), from 44 px down to 22 px
        let len = text.chars().count() as i32;
        let size = if len <= 9 { 44 } else { (44 * 9 / len).max(22) };
        let attrs = pango::AttrList::new();
        attrs.insert(pango::AttrSize::new_size_absolute(size * pango::SCALE));
        self.display.set_attributes(Some(&attrs));
        if let Some(b) = self.buttons.get("AC") {
            b.set_label(e.clear_label());
        }
        let waiting = e.waiting_op();
        for (op, ch) in [("÷", '÷'), ("×", '×'), ("−", '−'), ("+", '+')] {
            if let Some(b) = self.buttons.get(op) {
                if waiting == Some(ch) {
                    b.add_css_class("armed");
                } else {
                    b.remove_css_class("armed");
                }
            }
        }
    }
}

/// Run the Calculator: one window (opening it again brings it forward).
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = adw::Application::builder().application_id(crate::app_id()).build();
    let state: Rc<RefCell<Option<Rc<Calculator>>>> = Rc::default();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    app.connect_activate(move |app| {
        sonata_ui::setup();
        if let Some(f) = force {
            sonata_ui::force_appearance(f);
        }
        let mut s = state.borrow_mut();
        let calc = s.get_or_insert_with(|| Calculator::new(app)).clone();
        drop(s);
        sonata_ui::window::show_again(&calc.window);
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::plain_number;

    #[test]
    fn paste_takes_plain_decimals_only() {
        for ok in ["12", "-12", "1.5", "-0.5", ".5", "3."] {
            assert!(plain_number(ok), "{ok}");
        }
        for bad in ["", "-", ".", "1e5", "inf", "1.2.3", "--1", "12a", " 1"] {
            assert!(!plain_number(bad), "{bad}");
        }
    }
}
