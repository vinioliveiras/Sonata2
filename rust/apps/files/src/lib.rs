//! Files (sonata2/files/): Sonata's file manager (macOS Finder look and behaviour).

pub mod actions;
pub mod appimage;
pub mod automount;
pub mod batchrename;
pub mod chooser;
pub mod folder;
pub mod folderprefs;
pub mod mountop;
pub mod ops;
pub mod packages;
pub mod pathbar;
pub mod peicon;
pub mod quicklook;
pub mod rodisk;
pub mod search;
pub mod server;
pub mod sidebar;
pub mod tabs;
pub mod tags;
pub mod thumbs;
pub mod undo;
pub mod views;
pub mod window;

use gtk::prelude::*;

/// GTK for a test, on its thread: none without a display, and none when
/// another test's thread has it already (GTK lives on one thread). No
/// display is checked before gtk::init: a second gtk::init after a failed
/// one crashes the whole test run (SIGSEGV), so a run without a display
/// never tries.
#[cfg(test)]
pub(crate) fn gtk_here() -> bool {
    let shown = ["WAYLAND_DISPLAY", "DISPLAY", "BROADWAY_DISPLAY"].iter().any(|v| std::env::var_os(v).is_some_and(|x| !x.is_empty()));
    if !shown {
        return false;
    }
    // one test thread at a time asks: two seeing GTK not started and both
    // starting it crashed the run (SIGSEGV in ColumnView::new)
    static INIT: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _one = INIT.lock().unwrap_or_else(|e| e.into_inner());
    if gtk::is_initialized() {
        return gtk::is_initialized_main_thread();
    }
    gtk::init().is_ok()
}

/// The app id (its D-Bus name and Wayland app id).
pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.files"
}

/// A new Files window per launch (like Finder's File > New Window); a file
/// opens its folder with it selected.
pub fn open_uris(app: &gtk::Application, uris: &[String]) {
    let none = [String::new()];
    for uri in if uris.is_empty() { &none[..] } else { uris } {
        let mut uri = if uri.is_empty() { None } else { Some(folder::canonical(uri)) };
        if uri.as_deref() == Some(folder::CONNECT) {
            // the menu bar's Go > Connect to Server
            window::present(app, None, true).connect_to_server();
            continue;
        }
        let mut reveal = None;
        if let Some(u) = uri.clone() {
            let f = gio::File::for_uri(&u);
            let t = f.query_file_type(gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE);
            if !matches!(t, gio::FileType::Directory | gio::FileType::Unknown) {
                if let Some(p) = f.parent() {
                    reveal = Some(ops::base(&f));
                    uri = Some(p.uri().to_string());
                }
            }
        }
        let w = window::present(app, uri.as_deref(), true);
        if let Some(n) = reveal {
            w.reveal(&n);
        }
    }
}

/// org.freedesktop.FileManager1 (files/filemanager1.py): what apps call
/// for "Show in Folder" / "Open containing folder" (Chrome and Firefox
/// downloads, VS Code, Steam, portals...); without it D-Bus starts GNOME
/// Files for them. The name is taken by the running Files process; when
/// none runs, D-Bus starts `files --service` through the service file the
/// installer writes. Served only by the real Files, never on trial (the
/// Python one keeps it).
fn serve_file_manager(app: &gtk::Application) {
    const XML: &str = r#"<node><interface name="org.freedesktop.FileManager1">
  <method name="ShowFolders"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
  <method name="ShowItems"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
  <method name="ShowItemProperties"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
</interface></node>"#;
    let Some(conn) = app.dbus_connection() else { return };
    let Ok(node) = gio::DBusNodeInfo::for_xml(XML) else { return };
    let Some(iface) = node.lookup_interface("org.freedesktop.FileManager1") else { return };
    let a = app.downgrade();
    let reg = conn
        .register_object("/org/freedesktop/FileManager1", &iface)
        .method_call(move |_, _, _, _, method, params, inv| {
            let uris: Vec<String> = params.child_value(0).get().unwrap_or_default();
            if let Some(app) = a.upgrade() {
                if method == "ShowItemProperties" {
                    for u in &uris {
                        let f = gio::File::for_uri(u);
                        if let Ok(info) = f.query_info(folder::ATTRS, gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE) {
                            info.set_attribute_object("sonata::file", &f);
                            // (a window only to hand the app over: gone at once, or
                            // the app would never quit with it in its list)
                            let w = gtk::Window::new();
                            w.set_application(Some(&app));
                            quicklook::get_info(&w, &info);
                            w.destroy();
                        }
                    }
                } else {
                    open_uris(&app, &uris);
                }
            }
            inv.return_value(None);
        })
        .build();
    if reg.is_ok() {
        std::mem::forget(gio::bus_own_name_on_connection(&conn, "org.freedesktop.FileManager1", gio::BusNameOwnerFlags::REPLACE, |_, _| {}, |_, _| {}));
    }
}

pub fn run(args: &[String]) -> glib::ExitCode {
    let app = gtk::Application::builder().application_id(app_id()).flags(gio::ApplicationFlags::HANDLES_OPEN).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let background = args.iter().any(|a| a == "--background");
    // started by D-Bus for FileManager1: no window of its own
    let service = args.iter().any(|a| a == "--service");
    let ready = std::rc::Rc::new(std::cell::Cell::new(false));
    let start = move |app: &gtk::Application| {
        if !ready.replace(true) {
            if glib::application_name().is_none() {
                glib::set_application_name("Files");
            }
            sonata_ui::setup();
            if let Some(f) = force {
                sonata_ui::force_appearance(f);
            }
            sonata_ui::css::register_module("files/window.py");
            serve_file_manager(app);
        }
    };
    let s = start.clone();
    app.connect_open(move |app, files, _| {
        s(app);
        open_uris(app, &files.iter().map(|f| f.uri().to_string()).collect::<Vec<_>>());
    });
    let resident = std::rc::Rc::new(std::cell::Cell::new(false));
    app.connect_activate(move |app| {
        start(app);
        if background && !resident.replace(true) {
            // at login: resident like Finder, so every window opens at once
            std::mem::forget(app.hold());
            automount::start(); // every disk mounted and in the sidebar
            // warm once: CSS, icons -- the first window then opens at once
            let a = app.downgrade();
            glib::timeout_add_local_once(std::time::Duration::from_millis(1500), move || {
                if let Some(app) = a.upgrade() {
                    // (closed, not destroyed: closing stops its folder listing too)
                    window::FilesWindow::new(&app, None).window.close();
                }
            });
            return;
        }
        if service && !resident.replace(true) {
            // the call that started it opens the windows; they keep it alive
            let hold = app.hold();
            glib::timeout_add_seconds_local_once(30, move || drop(hold));
            return;
        }
        open_uris(app, &[]);
    });
    let mut argv = vec![std::env::args().next().unwrap_or_default()];
    argv.extend(args.iter().filter(|a| !a.starts_with("--")).map(|a| gio::File::for_commandline_arg(a).uri().to_string()));
    app.run_with_args(&argv)
}
