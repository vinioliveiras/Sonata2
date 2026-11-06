//! Lines of a child's pipe on the GTK main loop, as text
//! (backend/linereader.py).
//!
//! Read in chunks (read_bytes_async), split here: a non-UTF-8 byte is
//! replaced instead of ending the read, and the end of the pipe is a
//! 0-byte read (never mistaken for an empty line).

use gio::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;

pub const CHUNK: usize = 64 * 1024;

/// The lines `data` completes after what was left over (`rest`), which
/// keeps the unfinished end. A line that never ends (a progress bar redrawn
/// with \r only) is given in parts past 4 chunks, not held in memory until
/// the child exits.
pub fn split(rest: &mut Vec<u8>, data: &[u8], chunk: usize) -> Vec<String> {
    rest.extend_from_slice(data);
    let mut lines = Vec::new();
    while let Some(i) = rest.iter().position(|&b| b == b'\n') {
        let line: Vec<u8> = rest.drain(..=i).collect();
        lines.push(String::from_utf8_lossy(&line[..line.len() - 1]).into_owned());
    }
    if rest.len() > 4 * chunk {
        lines.push(String::from_utf8_lossy(rest).into_owned());
        rest.clear();
    }
    lines
}

struct Reader {
    cancel: Option<gio::Cancellable>,
    chunk: usize,
    rest: RefCell<Vec<u8>>,
    on_line: Box<dyn Fn(&str)>,
    on_end: RefCell<Option<Box<dyn FnOnce()>>>,
}

/// on_line(text) per line (without the newline), then on_end() once at the
/// end of the stream or on a read error; nothing after `cancel`.
pub fn read_lines(stream: &impl IsA<gio::InputStream>, cancel: Option<&gio::Cancellable>, on_line: impl Fn(&str) + 'static, on_end: impl FnOnce() + 'static) {
    read_lines_chunked(stream, cancel, CHUNK, on_line, on_end)
}

pub fn read_lines_chunked(stream: &impl IsA<gio::InputStream>, cancel: Option<&gio::Cancellable>, chunk: usize, on_line: impl Fn(&str) + 'static, on_end: impl FnOnce() + 'static) {
    let r = Rc::new(Reader { cancel: cancel.cloned(), chunk, rest: RefCell::default(), on_line: Box::new(on_line), on_end: RefCell::new(Some(Box::new(on_end))) });
    next(stream.clone().upcast(), r);
}

fn next(stream: gio::InputStream, r: Rc<Reader>) {
    let (s, chunk, cancel) = (stream.clone(), r.chunk, r.cancel.clone());
    stream.read_bytes_async(chunk, glib::Priority::DEFAULT, cancel.as_ref(), move |res| {
        if r.cancel.as_ref().is_some_and(|c| c.is_cancelled()) {
            return;
        }
        let data = res.ok().filter(|b| !b.is_empty());
        let Some(data) = data else {
            // the end of the pipe (or an error)
            let last = std::mem::take(&mut *r.rest.borrow_mut());
            if !last.is_empty() {
                (r.on_line)(&String::from_utf8_lossy(&last));
            }
            if let Some(end) = r.on_end.borrow_mut().take() {
                end();
            }
            return;
        };
        let lines = split(&mut r.rest.borrow_mut(), &data, r.chunk);
        for ln in &lines {
            (r.on_line)(ln);
            if r.cancel.as_ref().is_some_and(|c| c.is_cancelled()) {
                return;
            }
        }
        next(s, r);
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    /// Feed `data` through read_lines on a context of the test's own:
    /// (lines, end calls).
    fn read_all(data: &[u8], chunk: usize, cancel: Option<gio::Cancellable>, stop_after: Option<usize>) -> (Vec<String>, usize) {
        let data = data.to_vec();
        crate::on_main(move || {
            let ctx = glib::MainContext::default();
            let stream = gio::MemoryInputStream::from_bytes(&glib::Bytes::from(&data[..]));
            let lines: Rc<RefCell<Vec<String>>> = Rc::default();
            let ends = Rc::new(Cell::new(0));
            let lp = glib::MainLoop::new(Some(&ctx), false);
            let (l, c, lp2) = (lines.clone(), cancel.clone(), lp.clone());
            let e = ends.clone();
            let lp3 = lp.clone();
            read_lines_chunked(
                &stream,
                cancel.as_ref(),
                chunk,
                move |t| {
                    l.borrow_mut().push(t.to_string());
                    if Some(l.borrow().len()) == stop_after {
                        c.as_ref().unwrap().cancel();
                        let lp = lp2.clone();
                        glib::timeout_add_local_once(std::time::Duration::from_millis(100), move || lp.quit());
                    }
                },
                move || {
                    e.set(e.get() + 1);
                    lp3.quit();
                },
            );
            let lp4 = lp.clone();
            // never hang the suite (the context, and the source, end with the test)
            glib::timeout_add_local_once(std::time::Duration::from_secs(3), move || lp4.quit());
            lp.run();
            let out = lines.borrow().clone();
            (out, ends.get())
        })
    }

    #[test]
    fn lines_and_the_end() {
        assert_eq!(read_all(b"one\ntwo\n\nfour\n", CHUNK, None, None), (vec!["one".into(), "two".into(), "".into(), "four".into()], 1));
        assert_eq!(read_all(b"a\nlast", CHUNK, None, None).0, ["a", "last"]);
        assert_eq!(read_all(b"", CHUNK, None, None), (vec![], 1));
    }

    #[test]
    fn lines_and_characters_split_across_reads() {
        assert_eq!(read_all(b"Downloading package 1/3\nInstalling\n100%\n", 8, None, None).0, ["Downloading package 1/3", "Installing", "100%"]);
        for chunk in [7, 9, 10, 11, 13] {
            assert_eq!(read_all("Atualização concluída\n".as_bytes(), chunk, None, None).0, ["Atualização concluída"], "{chunk}");
        }
    }

    #[test]
    fn bad_bytes_dont_end_the_read() {
        let (lines, ends) = read_all(b"ok\nbad \xff\xfe here\nafter\n", CHUNK, None, None);
        assert_eq!(lines, ["ok", "bad \u{fffd}\u{fffd} here", "after"]);
        assert_eq!(ends, 1);
    }

    #[test]
    fn a_line_that_never_ends_comes_in_parts() {
        let mut data = vec![b'x'; 50];
        data.push(b'\n');
        let lines = read_all(&data, 4, None, None).0;
        assert_eq!(lines.concat(), "x".repeat(50));
        assert!(lines.len() > 1 && lines.iter().all(|l| l.len() <= 4 * 4 + 4));
    }

    #[test]
    fn nothing_after_cancel() {
        let cancel = gio::Cancellable::new();
        assert_eq!(read_all(b"a\nb\nc\nd\n", 2, Some(cancel), Some(1)), (vec!["a".into()], 0));
    }
}
