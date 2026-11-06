//! The Control Center pieces the Sound menu, Control Center and the
//! per-app mixer share: the capsule slider is sonata-ui's (module_slider,
//! the Game Bar uses it too); the round button and the volume feedback.

use gtk::prelude::*;

pub use sonata_ui::module_slider::{slider_with_icon, speaker_icon, value_at, ModuleSlider, SLIDER_H};

/// The volume feedback sound when the slider is let go (Settings > Sound):
/// Vini, in the Rust session -- Control Center's and the Sound menu's
/// volume made no sound (topbar.py's _volume_feedback).
pub fn volume_feedback(slider: &impl IsA<gtk::Widget>) {
    sonata_ui::controls::on_release(slider, || sonata_backend::sounds::play_soon("volume"));
}

/// Control Center's small round glyph button (the Sound module's Output).
pub fn round_button(icon: &str, tooltip: &str, on_click: impl Fn(&gtk::Button) + 'static) -> gtk::Button {
    let b = gtk::Button::builder().css_classes(["cc-round"]).can_focus(false).valign(gtk::Align::Center).tooltip_text(tooltip).build();
    b.set_child(Some(&gtk::Image::builder().icon_name(icon).pixel_size(14).build()));
    b.connect_clicked(on_click);
    b
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn speaker_levels() {
        assert_eq!(speaker_icon(0.0), "audio-volume-muted-symbolic");
        assert_eq!(speaker_icon(10.0), "audio-volume-low-symbolic");
        assert_eq!(speaker_icon(50.0), "audio-volume-medium-symbolic");
        assert_eq!(speaker_icon(67.0), "audio-volume-high-symbolic");
    }

    #[test]
    fn the_knob_follows_the_pointer() {
        assert_eq!(value_at(0.0, 222.0, 0.0, 100.0), 0.0);
        assert_eq!(value_at(11.0 + 100.0, 222.0, 0.0, 100.0), 50.0);
        assert_eq!(value_at(500.0, 222.0, 0.0, 100.0), 100.0);
        assert_eq!(value_at(50.0, 10.0, 0.0, 100.0), 100.0); // narrower than the knob: no division by zero
    }
}

#[cfg(test)]
mod feedback_tests {
    use gtk::prelude::*;
    /// Vini, in the Rust session: moving the volume in Control Center (and
    /// the Sound menu) made no sound -- the release hook was never put on
    /// those sliders. Both carry it, as topbar.py's _volume_feedback does.
    #[test]
    fn test_control_center_volume_made_no_sound() {
        for (file, src) in [("cc.rs", include_str!("cc.rs")), ("bar.rs", include_str!("bar.rs"))] {
            let at = src.find("slider_with_icon(|v| crate::controls::speaker_icon(v)").or_else(|| src.find("slider_with_icon(|v| speaker_icon(v)")).unwrap_or_else(|| panic!("{file}: the volume slider"));
            let next = &src[at..(at + 600).min(src.len())];
            assert!(next.contains("volume_feedback(&"), "{file}: no feedback on the volume slider");
        }
    }

    /// The hook itself: a release on the slider plays (here: is called).
    /// (GTK in a process of its own: started on a test thread it took the
    /// main context the other tests use -- the run aborted now and then.)
    #[test]
    fn release_calls_back() {
        crate::gtk_tests::in_own_process("controls::feedback_tests::release_calls_back_gtk");
    }

    #[test]
    #[ignore = "GTK: run by release_calls_back"]
    fn release_calls_back_gtk() {
        if gtk::init().is_err() {
            return; // (no display)
        }
        let s = super::ModuleSlider::new(50.0, 0.0, 100.0);
        super::volume_feedback(&s);
        let w: &gtk::Widget = s.upcast_ref();
        let ctl = w.observe_controllers();
        let has = (0..ctl.n_items()).any(|i| ctl.item(i).is_some_and(|c| c.is::<gtk::EventControllerLegacy>()));
        assert!(has);
    }
}
