//! Finder's file actions that aren't plain copy / move (files/actions.py):
//! Compress (a .zip next to the items), Copy Path and the Quick Actions for
//! pictures (Rotate Left / Right, Convert to PNG / JPEG). The work runs in
//! a thread; what it makes can be undone.

use crate::ops;
use gio::prelude::*;
use gtk::gdk_pixbuf::{self, Pixbuf, PixbufRotation};
use std::io::Write;
use std::path::Path;
use std::rc::Rc;

pub const JPEG_QUALITY: &str = "95";

/// "png", "jpeg"...: a picture these actions can rewrite; "" otherwise.
pub fn picture_kind(content_type: &str) -> &'static str {
    match content_type {
        "image/png" => "png",
        "image/jpeg" => "jpeg",
        "image/bmp" => "bmp",
        "image/tiff" => "tiff",
        _ => "",
    }
}

// -- Compress ---------------------------------------------------------------------------------
/// Finder: "<name>.zip" for one item, "Archive.zip" for several.
pub fn archive_name(files: &[gio::File]) -> String {
    if files.len() == 1 { format!("{}.zip", ops::base(&files[0])) } else { "Archive.zip".into() }
}

fn add(zf: &mut zip::ZipWriter<std::fs::File>, path: &Path, arc: &str) -> std::io::Result<()> {
    let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated).large_file(true);
    let meta = std::fs::symlink_metadata(path)?;
    if meta.file_type().is_symlink() {
        // a symlink stays a symlink
        let target = std::fs::read_link(path)?;
        zf.add_symlink(arc, target.to_string_lossy(), opts.unix_permissions(0o777)).map_err(std::io::Error::other)?;
    } else if meta.is_dir() {
        zf.add_directory(format!("{arc}/"), opts).map_err(std::io::Error::other)?;
        let mut names: Vec<String> = std::fs::read_dir(path)?.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        for n in names {
            add(zf, &path.join(&n), &format!("{arc}/{n}"))?;
        }
    } else {
        use std::os::unix::fs::PermissionsExt;
        zf.start_file(arc, opts.unix_permissions(meta.permissions().mode())).map_err(std::io::Error::other)?;
        let mut f = std::fs::File::open(path)?;
        std::io::copy(&mut f, zf)?;
    }
    Ok(())
}

fn zip_files(files: &[std::path::PathBuf], target: &Path) -> std::io::Result<()> {
    let tmp = target.with_file_name(format!("{}.part", target.file_name().unwrap().to_string_lossy()));
    let res = (|| {
        let mut zf = zip::ZipWriter::new(std::fs::File::create(&tmp)?);
        for f in files {
            add(&mut zf, f, &f.file_name().unwrap().to_string_lossy())?;
        }
        zf.finish().map_err(std::io::Error::other)?.flush()?;
        std::fs::rename(&tmp, target)
    })();
    if res.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    res
}

/// A .zip of `files` in their folder (paths relative to it); on_done(zip).
pub fn compress(files: &[gio::File], on_done: Option<Box<dyn FnOnce(gio::File)>>, on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>) {
    let paths: Vec<std::path::PathBuf> = files.iter().filter_map(|f| f.path()).collect();
    if paths.is_empty() {
        return;
    }
    let Some(folder) = files[0].parent() else { return };
    let target = folder.child(ops::free_name(&folder, &archive_name(files), false, "number"));
    let made = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let (m2, t2) = (made.clone(), target.clone());
    ops::in_thread(
        move |report| match zip_files(&paths, &t2.path().unwrap()) {
            Ok(()) => m2.store(true, std::sync::atomic::Ordering::SeqCst),
            Err(e) => report(&t2, &glib::Error::new(gio::IOErrorEnum::Failed, &e.to_string())),
        },
        Some(Box::new(move || {
            if made.load(std::sync::atomic::Ordering::SeqCst) {
                crate::undo::copied(vec![target.clone()]);
                if let Some(d) = on_done {
                    d(target);
                }
            }
        })),
        on_error,
    );
}

// -- Copy Path --------------------------------------------------------------------------------
pub fn paths_text(files: &[gio::File]) -> String {
    files.iter().map(|f| f.path().map(|p| p.display().to_string()).unwrap_or_else(|| f.uri().to_string())).collect::<Vec<_>>().join("\n")
}

pub fn copy_paths(widget: &impl IsA<gtk::Widget>, files: &[gio::File]) {
    use gtk::prelude::*;
    if !files.is_empty() {
        widget.clipboard().set_text(&paths_text(files));
    }
}

// -- Quick Actions for pictures ---------------------------------------------------------------
fn save(pb: &Pixbuf, path: &Path, kind: &str) -> Result<(), glib::Error> {
    let tmp = path.with_file_name(format!("{}.sonata-new", path.file_name().unwrap().to_string_lossy()));
    let opts: Vec<(&str, &str)> = if kind == "jpeg" { vec![("quality", JPEG_QUALITY)] } else { Vec::new() };
    pb.savev(&tmp, kind, &opts)?;
    std::fs::rename(&tmp, path).map_err(|e| glib::Error::new(gio::IOErrorEnum::Failed, &e.to_string()))
}

/// Rotate Left / Right, in place (Finder's Quick Action). files: (file, kind).
pub fn rotate(files: Vec<(gio::File, String)>, clockwise: bool, on_done: Option<Box<dyn FnOnce()>>, on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>) {
    let angle = if clockwise { PixbufRotation::Clockwise } else { PixbufRotation::Counterclockwise };
    ops::in_thread(
        move |report| {
            for (f, kind) in files.iter().filter(|(f, k)| !k.is_empty() && f.path().is_some()) {
                let p = f.path().unwrap();
                let res = Pixbuf::from_file(&p).and_then(|pb| {
                    let pb = pb.apply_embedded_orientation().unwrap_or(pb);
                    let r = pb.rotate_simple(angle).ok_or_else(|| glib::Error::new(gio::IOErrorEnum::Failed, "rotate"))?;
                    save(&r, &p, kind)
                });
                if let Err(e) = res {
                    report(f, &e);
                }
            }
        },
        on_done,
        on_error,
    );
}

/// Convert to PNG / JPEG: a new file next to each picture.
pub fn convert(files: Vec<gio::File>, kind: &'static str, on_done: Option<Box<dyn FnOnce(Vec<gio::File>)>>, on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>) {
    let ext = if kind == "png" { ".png" } else { ".jpg" };
    let made = std::sync::Arc::new(std::sync::Mutex::new(Vec::<gio::File>::new()));
    let m2 = made.clone();
    ops::in_thread(
        move |report| {
            for f in &files {
                let (Some(p), Some(parent)) = (f.path(), f.parent()) else { continue };
                let stem = p.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
                let target = parent.child(ops::free_name(&parent, &format!("{stem}{ext}"), false, "number"));
                let res = Pixbuf::from_file(&p).and_then(|pb| {
                    let mut pb = pb.apply_embedded_orientation().unwrap_or(pb);
                    if kind == "jpeg" && pb.has_alpha() {
                        // JPEG has no transparency: on white
                        let flat = Pixbuf::new(gdk_pixbuf::Colorspace::Rgb, false, 8, pb.width(), pb.height()).unwrap();
                        flat.fill(0xffffffff);
                        pb.composite(&flat, 0, 0, pb.width(), pb.height(), 0.0, 0.0, 1.0, 1.0, gdk_pixbuf::InterpType::Bilinear, 255);
                        pb = flat;
                    }
                    save(&pb, &target.path().unwrap(), kind)
                });
                match res {
                    Ok(()) => m2.lock().unwrap().push(target),
                    Err(e) => report(f, &e),
                }
            }
        },
        Some(Box::new(move || {
            let made = made.lock().unwrap().clone();
            if !made.is_empty() {
                crate::undo::copied(made.clone());
            }
            if let Some(d) = on_done {
                d(made);
            }
        })),
        on_error,
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compress_keeps_folders_and_links() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("dir/sub")).unwrap();
        std::fs::write(d.path().join("dir/sub/a.txt"), "hello").unwrap();
        std::os::unix::fs::symlink("sub/a.txt", d.path().join("dir/link")).unwrap();
        let target = d.path().join("dir.zip");
        zip_files(&[d.path().join("dir")], &target).unwrap();
        let mut z = zip::ZipArchive::new(std::fs::File::open(&target).unwrap()).unwrap();
        let mut names: Vec<String> = (0..z.len()).map(|i| z.by_index(i).unwrap().name().to_string()).collect();
        names.sort();
        assert_eq!(names, ["dir/", "dir/link", "dir/sub/", "dir/sub/a.txt"]);
        assert!(!d.path().join("dir.zip.part").exists());
        let g = gio::File::for_path(d.path().join("dir"));
        assert_eq!(archive_name(&[g.clone()]), "dir.zip");
        assert_eq!(archive_name(&[g.clone(), g.clone()]), "Archive.zip");
        assert_eq!(paths_text(&[g]), d.path().join("dir").display().to_string());
    }

    #[test]
    fn rotate_and_convert_pixels() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("a.png");
        let pb = Pixbuf::new(gdk_pixbuf::Colorspace::Rgb, true, 8, 4, 2).unwrap();
        pb.fill(0x00000000);
        pb.savev(&p, "png", &[]).unwrap();
        let r = Pixbuf::from_file(&p).unwrap().rotate_simple(PixbufRotation::Clockwise).unwrap();
        save(&r, &p, "png").unwrap();
        let back = Pixbuf::from_file(&p).unwrap();
        assert_eq!((back.width(), back.height()), (2, 4));
        assert_eq!(picture_kind("image/jpeg"), "jpeg");
        assert_eq!(picture_kind("image/gif"), "");
    }
}
