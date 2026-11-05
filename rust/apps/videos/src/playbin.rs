//! Songs in Videos (videos/gststream.py): a gtk::MediaStream over
//! GStreamer's classic playbin. Not GTK's own media backend for audio: it
//! plays through playbin3, whose decodebin3 aborts the whole app on some
//! MP3s -- what the removed Music app learnt. As a MediaStream, the window
//! uses it exactly like a movie. Also the song's tags (title, artist,
//! cover), read with GStreamer's discoverer: audiotags.py's own MP3 / FLAC /
//! Ogg / MP4 / WAV parsers aren't ported -- the discoverer reads every
//! format GStreamer plays, and Videos only shows those three.

use gst::prelude::*;
use gstreamer as gst;
use gstreamer_pbutils as pbutils;
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use std::cell::RefCell;

const TICK_MS: u64 = 250;

/// GStreamer started, with a playbin.
pub fn available() -> bool {
    gst::init().is_ok() && gst::ElementFactory::find("playbin").is_some()
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct PlaybinStream {
        pub bin: RefCell<Option<gst::Element>>,
        pub tick: RefCell<Option<glib::SourceId>>,
        pub watch: RefCell<Option<gst::bus::BusWatchGuard>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for PlaybinStream {
        const NAME: &'static str = "SonataRsPlaybinStream";
        type Type = super::PlaybinStream;
        type ParentType = gtk::MediaStream;
    }

    impl ObjectImpl for PlaybinStream {}

    impl gtk::gdk::subclass::paintable::PaintableImpl for PlaybinStream {
        fn snapshot(&self, _s: &gtk::gdk::Snapshot, _w: f64, _h: f64) {}
    }

    impl MediaStreamImpl for PlaybinStream {
        fn play(&self) -> bool {
            if let Some(b) = self.bin.borrow().as_ref() {
                let _ = b.set_state(gst::State::Playing);
            }
            if self.tick.borrow().is_none() {
                let w = self.obj().downgrade();
                let id = glib::timeout_add_local(std::time::Duration::from_millis(TICK_MS), move || {
                    let Some(s) = w.upgrade() else { return glib::ControlFlow::Break };
                    s.tick();
                    glib::ControlFlow::Continue
                });
                self.tick.replace(Some(id));
            }
            true
        }

        fn pause(&self) {
            if let Some(b) = self.bin.borrow().as_ref() {
                let _ = b.set_state(gst::State::Paused);
            }
            self.obj().stop_tick();
        }

        fn seek(&self, timestamp: i64) {
            let obj = self.obj();
            let ok = self.bin.borrow().as_ref().is_some_and(|b| b.seek_simple(gst::SeekFlags::FLUSH | gst::SeekFlags::ACCURATE, gst::ClockTime::from_useconds(timestamp.max(0) as u64)).is_ok());
            if ok {
                obj.seek_success();
                obj.update(timestamp);
            } else {
                obj.seek_failed();
            }
        }

        fn update_audio(&self, muted: bool, volume: f64) {
            if let Some(b) = self.bin.borrow().as_ref() {
                b.set_property("mute", muted);
                b.set_property("volume", volume);
            }
        }
    }
}

glib::wrapper! {
    pub struct PlaybinStream(ObjectSubclass<imp::PlaybinStream>) @extends gtk::MediaStream, @implements gtk::gdk::Paintable;
}

impl PlaybinStream {
    pub fn new(path: &std::path::Path) -> Result<Self, String> {
        if !available() {
            return Err("GStreamer's playbin is missing (gstreamer and its base plugins).".into());
        }
        let s: Self = glib::Object::new();
        let bin = gst::ElementFactory::make("playbin").property("uri", gio::File::for_path(path).uri().as_str()).build().map_err(|e| e.to_string())?;
        if let Ok(sink) = gst::ElementFactory::make("fakesink").build() {
            bin.set_property("video-sink", &sink); // a song's cover "video": dropped
        }
        let w = s.downgrade();
        let watch = bin.bus().and_then(|bus| {
            bus.add_watch_local(move |_, msg| {
                if let Some(s) = w.upgrade() {
                    s.message(msg);
                }
                glib::ControlFlow::Continue
            })
            .ok()
        });
        s.imp().watch.replace(watch);
        let _ = bin.set_state(gst::State::Paused); // prerolls: duration, prepared
        s.imp().bin.replace(Some(bin));
        Ok(s)
    }

    /// Let go of the pipeline (the window does, on close or another file).
    pub fn close(&self) {
        self.stop_tick();
        self.imp().watch.take();
        if let Some(b) = self.imp().bin.take() {
            let _ = b.set_state(gst::State::Null);
        }
    }

    fn stop_tick(&self) {
        if let Some(id) = self.imp().tick.take() {
            sonata_ui::cancel(id);
        }
    }

    fn tick(&self) {
        let pos = self.imp().bin.borrow().as_ref().and_then(|b| b.query_position::<gst::ClockTime>());
        if let Some(p) = pos {
            self.update(p.useconds() as i64);
        }
    }

    fn duration_us(&self) -> i64 {
        self.imp().bin.borrow().as_ref().and_then(|b| b.query_duration::<gst::ClockTime>()).map(|d| d.useconds() as i64).unwrap_or(0)
    }

    fn message(&self, msg: &gst::Message) {
        match msg.view() {
            gst::MessageView::AsyncDone(_) if !self.is_prepared() => {
                let dur = self.duration_us();
                self.stream_prepared(true, false, dur > 0, dur);
            }
            gst::MessageView::Eos(_) => {
                if self.is_loop() {
                    MediaStreamExt::seek(self, 0);
                    return;
                }
                self.stop_tick();
                self.update(self.duration());
                self.stream_ended();
            }
            gst::MessageView::Error(e) => {
                self.stop_tick();
                self.set_error(e.error());
            }
            _ => {}
        }
    }
}

/// A song's tags: (title, artist, cover picture bytes).
#[derive(Default, Debug, PartialEq)]
pub struct Tags {
    pub title: String,
    pub artist: String,
    pub cover: Option<Vec<u8>>,
}

/// Title and track number from "01 - Title.mp3" / "01 Title.mp3" (audiotags.from_filename).
pub fn title_from_filename(path: &std::path::Path) -> String {
    let base = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
    let re = regex_lite(&base);
    re.unwrap_or(base)
}

/// "^\s*(?:(\d{1,2})-)?(\d{1,3})[\s._-]+(.+)$" by hand: the title after a track number.
fn regex_lite(base: &str) -> Option<String> {
    let s = base.trim_start();
    let digits = |t: &str| t.chars().take_while(|c| c.is_ascii_digit()).count();
    let mut rest = s;
    let d1 = digits(rest);
    if (1..=2).contains(&d1) && rest[d1..].starts_with('-') && digits(&rest[d1 + 1..]) >= 1 {
        rest = &rest[d1 + 1..]; // disc-track
    }
    let d = digits(rest);
    if !(1..=3).contains(&d) {
        return None;
    }
    let after = &rest[d..];
    let sep = after.chars().take_while(|c| c.is_whitespace() || "._-".contains(*c)).map(char::len_utf8).sum::<usize>();
    if sep == 0 || after.len() == sep {
        return None;
    }
    let title = after[sep..].trim_matches(|c: char| " -_.".contains(c));
    Some(if title.is_empty() { base.to_string() } else { title.to_string() })
}

/// The song's tags (slow: call it off the main loop).
pub fn read_tags(path: &std::path::Path) -> Tags {
    let mut t = Tags::default();
    if gst::init().is_ok() {
        if let Ok(d) = pbutils::Discoverer::new(gst::ClockTime::from_seconds(5)) {
            if let Ok(info) = d.discover_uri(gio::File::for_path(path).uri().as_str()) {
                if let Some(tags) = info.tags() {
                    t.title = tags.get::<gst::tags::Title>().map(|v| v.get().to_string()).unwrap_or_default();
                    t.artist = tags.get::<gst::tags::Artist>().map(|v| v.get().to_string()).unwrap_or_default();
                    let sample = tags.get::<gst::tags::Image>().map(|v| v.get().clone()).or_else(|| tags.get::<gst::tags::PreviewImage>().map(|v| v.get().clone()));
                    t.cover = sample.and_then(|s| s.buffer().and_then(|b| b.map_readable().ok().map(|m| m.as_slice().to_vec())));
                }
            }
        }
    }
    if t.title.is_empty() {
        t.title = title_from_filename(path);
    }
    t
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn titles_from_file_names() {
        assert_eq!(title_from_filename(Path::new("/m/01 - Hey Jude.mp3")), "Hey Jude");
        assert_eq!(title_from_filename(Path::new("/m/1-07 Song.flac")), "Song");
        assert_eq!(title_from_filename(Path::new("/m/07_Song.ogg")), "Song");
        assert_eq!(title_from_filename(Path::new("/m/Song.mp3")), "Song");
        assert_eq!(title_from_filename(Path::new("/m/2024.mp3")), "2024"); // a year alone isn't a track number
        // (the same as audiotags.from_filename's regex on these)
        assert_eq!(title_from_filename(Path::new("/m/01-Song.mp3")), "Song");
        assert_eq!(title_from_filename(Path::new("/m/1234 Song.mp3")), "1234 Song");
        assert_eq!(title_from_filename(Path::new("/m/12 .mp3")), "12 ");
        assert_eq!(title_from_filename(Path::new("/m/3 - -.mp3")), "3 - -");
    }
}
