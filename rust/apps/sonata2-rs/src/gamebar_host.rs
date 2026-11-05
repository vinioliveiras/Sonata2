//! The Game Bar's side of the menu bar (sonata_gamebar::gamebar::Host):
//! what the Python GameBar(app, bar) reaches through the bar.

use std::rc::{Rc, Weak};

pub fn host(_app: &gtk::Application, _capture: Weak<sonata_capture::capture::Capture>) -> Rc<dyn sonata_gamebar::gamebar::Host> {
    Rc::new(sonata_gamebar::gamebar::NoHost)
}
