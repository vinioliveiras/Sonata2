//! Preview's Open / Save panels: Files' own (sonata_files::chooser), as the
//! Python Preview opens files/chooser.py's ChooserWindow -- its sidebar,
//! "Save As:" field and Format pop-up, not GTK's file dialog.
//!
//! sonata-files uses Preview's picture loading (Quick Look, thumbnails), so
//! Preview can't name it. Files' chooser hands itself in here when the
//! program starts (chooser.rs, linked into sonata2-rs); where it isn't
//! linked in (Preview's own tests) GTK's file dialog stands in, with the
//! same filters and names.

use gtk::prelude::*;
use gtk::gio;
use std::sync::OnceLock;

/// kind of a filter rule (portal spec): 0 glob pattern, 1 MIME type
pub const GLOB: u32 = 0;
pub const MIME: u32 = 1;

/// (name, [(kind, pattern)]): the panel's file type pop-up
pub type Filter = (String, Vec<(u32, String)>);

/// What a panel is opened with.
#[derive(Debug, Clone, PartialEq)]
pub struct Request {
    pub save: bool,
    pub title: String,
    pub multiple: bool,
    pub filters: Vec<Filter>,
    /// the folder it starts in (a uri)
    pub folder: Option<String>,
    /// Save As: the name it suggests
    pub name: String,
}

/// The chosen uris, None when cancelled.
pub type Done = Box<dyn FnOnce(Option<Vec<String>>)>;

/// Opens a panel; with a parent it is that window's (modal) sheet.
pub type Panel = fn(&gtk::Application, Option<&gtk::Window>, Request, Done);

static PANEL: OnceLock<Panel> = OnceLock::new();

/// Files' chooser, handed in once.
pub fn install(panel: Panel) {
    let _ = PANEL.set(panel);
}

pub fn installed() -> bool {
    PANEL.get().is_some()
}

pub fn present(app: &gtk::Application, parent: Option<&gtk::Window>, req: Request, done: Done) {
    match PANEL.get() {
        Some(panel) => panel(app, parent, req, done),
        None => gtk_dialog(app, parent, req, done),
    }
}

/// GTK's file dialog, where Files' chooser isn't there.
fn gtk_dialog(app: &gtk::Application, parent: Option<&gtk::Window>, req: Request, done: Done) {
    let filters = gio::ListStore::new::<gtk::FileFilter>();
    for (name, rules) in &req.filters {
        let f = gtk::FileFilter::new();
        f.set_name(Some(name));
        for (kind, pattern) in rules {
            if *kind == MIME {
                f.add_mime_type(pattern);
            } else {
                f.add_pattern(pattern);
            }
        }
        filters.append(&f);
    }
    let dlg = gtk::FileDialog::builder().title(req.title.as_str()).modal(parent.is_some()).filters(&filters).build();
    if let Some(folder) = &req.folder {
        dlg.set_initial_folder(Some(&gio::File::for_uri(folder)));
    }
    // the app stays while the panel is up (Open with no window yet)
    let hold = app.hold();
    if req.save {
        dlg.set_initial_name(Some(req.name.as_str()));
        dlg.save(parent, gio::Cancellable::NONE, move |res| {
            let _hold = hold;
            done(res.ok().map(|f| vec![f.uri().to_string()]));
        });
    } else {
        dlg.open_multiple(parent, gio::Cancellable::NONE, move |res| {
            let _hold = hold;
            done(res.ok().map(|list| (0..list.n_items()).filter_map(|i| list.item(i).and_downcast::<gio::File>()).map(|f| f.uri().to_string()).collect()));
        });
    }
}
