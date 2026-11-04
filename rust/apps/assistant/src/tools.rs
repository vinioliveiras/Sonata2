//! File access for the Assistant (assistant/tools.py): Claude may list,
//! read and create files, only inside the folders you chose, and only
//! after you allow each action (the window asks). Paths are resolved
//! (symlinks, "..") before the folder check, so nothing outside the chosen
//! folders can be reached.

use serde_json::{json, Value};
use sonata_i18n::tr;
use std::path::{Path, PathBuf};

/// bytes read from one file
pub const MAX_READ: usize = 256 * 1024;
/// entries listed from one folder
pub const MAX_LIST: usize = 500;
/// bytes written to one file
pub const MAX_WRITE: usize = 2 * 1024 * 1024;
pub const NAMES: [&str; 3] = ["list_folder", "read_file", "create_file"];

/// The tool definitions sent to the API.
pub fn definitions() -> Value {
    json!([
        {"name": "list_folder",
         "description": "List the files and folders inside a folder the user allowed. Returns one entry per line: name, then '/' for folders or the size in bytes for files.",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute path of the folder."}}, "required": ["path"]}},
        {"name": "read_file",
         "description": "Read a text file inside a folder the user allowed (up to 256 KB).",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string", "description": "Absolute path of the file."}}, "required": ["path"]}},
        {"name": "create_file",
         "description": "Create a text file inside a folder the user allowed (missing parent folders are created). Fails if the file exists, unless overwrite is true.",
         "input_schema": {"type": "object", "properties": {
             "path": {"type": "string", "description": "Absolute path of the new file."},
             "content": {"type": "string", "description": "The file's text."},
             "overwrite": {"type": "boolean", "description": "Replace an existing file."}},
             "required": ["path", "content"]}}
    ])
}

/// What will happen (after the user allows it).
#[derive(Clone, Debug, PartialEq)]
pub struct Action {
    pub name: String,
    pub path: PathBuf,
    pub args: Value,
    pub exists: bool,
}

impl Action {
    /// Past-tense line for the chat ("Read notes.txt").
    pub fn summary(&self) -> String {
        let base = self.path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| self.path.display().to_string());
        let t = match self.name.as_str() {
            "list_folder" => tr!("Looked in {name}"),
            "read_file" => tr!("Read {name}"),
            _ if self.exists => tr!("Replaced {name}"),
            _ => tr!("Created {name}"),
        };
        sonata_i18n::fmt(&t, &[("name", &base)])
    }

    /// An action as the chat shows it (from the API's tool_use block).
    pub fn of_block(block: &Value) -> Self {
        let input = block.get("input").cloned().unwrap_or(json!({}));
        let path = input.get("path").and_then(Value::as_str).unwrap_or("").to_string();
        let exists = input.get("overwrite").and_then(Value::as_bool).unwrap_or(false);
        Self { name: block.get("name").and_then(Value::as_str).unwrap_or("").to_string(), path: PathBuf::from(path), args: input, exists }
    }
}

pub fn system_prompt(folders: &[String]) -> String {
    if folders.is_empty() {
        return String::new();
    }
    let lines = folders.iter().map(|f| format!("- {f}")).collect::<Vec<_>>().join("\n");
    format!("You can list, read and create files with your tools, only inside these folders the user chose:\n{lines}\nAlways use absolute paths. The user is asked to allow every action, and may refuse.")
}

fn expand(path: &str) -> PathBuf {
    match path.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with('/') => glib::home_dir().join(rest.trim_start_matches('/')),
        _ => PathBuf::from(path),
    }
}

/// os.path.realpath: symlinks and ".." resolved, as far as the path exists
/// (a file still to be created: its existing folder resolved, the rest added).
pub fn real(path: &str) -> PathBuf {
    let p = expand(path);
    let mut probe = p.clone();
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    loop {
        if let Ok(r) = std::fs::canonicalize(&probe) {
            let mut out = r;
            for t in tail.iter().rev() {
                if t == ".." {
                    out.pop();
                } else if t != "." {
                    out.push(t);
                }
            }
            return out;
        }
        match (probe.file_name().map(|n| n.to_os_string()), probe.parent().map(Path::to_path_buf)) {
            (Some(n), Some(parent)) => {
                tail.push(n);
                probe = parent;
            }
            _ => {
                // nothing of it exists: ".." and "." taken out by hand
                let mut out = PathBuf::new();
                for c in p.components() {
                    match c {
                        std::path::Component::ParentDir => {
                            out.pop();
                        }
                        std::path::Component::CurDir => {}
                        other => out.push(other),
                    }
                }
                return out;
            }
        }
    }
}

pub fn inside(path: &Path, folders: &[String]) -> bool {
    folders.iter().any(|f| path.starts_with(real(f)))
}

/// Can `name` happen with `args`? The action, or why not.
pub fn check(name: &str, args: &Value, folders: &[String]) -> Result<Action, String> {
    if !NAMES.contains(&name) {
        return Err(format!("Unknown tool: {name}"));
    }
    let Some(raw) = args.get("path").and_then(Value::as_str).map(str::trim).filter(|p| !p.is_empty()) else {
        return Err("A path is needed.".into());
    };
    if !expand(raw).is_absolute() {
        return Err("The path must be absolute.".into());
    }
    let path = real(raw);
    if !inside(&path, folders) {
        return Err("That path is outside the folders the user allowed.".into());
    }
    if name == "list_folder" && !path.is_dir() {
        return Err("No such folder.".into());
    }
    if name == "read_file" && !path.is_file() {
        return Err("No such file.".into());
    }
    let mut exists = false;
    if name == "create_file" {
        let Some(content) = args.get("content").and_then(Value::as_str) else { return Err("The file's content is needed.".into()) };
        if content.len() > MAX_WRITE {
            return Err("The content is too large.".into());
        }
        if path.is_dir() {
            return Err("A folder has that name.".into());
        }
        exists = path.exists();
        if exists && !args.get("overwrite").and_then(Value::as_bool).unwrap_or(false) {
            return Err("The file already exists (set overwrite to replace it).".into());
        }
    }
    Ok(Action { name: name.into(), path, args: args.clone(), exists })
}

/// Do it (after the user allowed it): (result text, is_error).
pub fn run(action: &Action) -> (String, bool) {
    let res: std::io::Result<(String, bool)> = (|| {
        match action.name.as_str() {
            "list_folder" => {
                let mut items: Vec<std::fs::DirEntry> = std::fs::read_dir(&action.path)?.flatten().collect();
                items.sort_by_key(|e| e.file_name().to_string_lossy().to_lowercase());
                let mut entries = Vec::new();
                for e in items {
                    if entries.len() >= MAX_LIST {
                        entries.push(format!("… (more than {MAX_LIST} entries)"));
                        break;
                    }
                    let name = e.file_name().to_string_lossy().into_owned();
                    entries.push(match e.metadata() {
                        Ok(m) if m.is_dir() => format!("{name}/"),
                        Ok(m) => format!("{name}\t{}", m.len()),
                        Err(_) => name,
                    });
                }
                Ok((if entries.is_empty() { "(empty folder)".into() } else { entries.join("\n") }, false))
            }
            "read_file" => {
                use std::io::Read;
                let mut data = Vec::new();
                std::fs::File::open(&action.path)?.take(MAX_READ as u64 + 1).read_to_end(&mut data)?;
                if data[..data.len().min(8192)].contains(&0) {
                    return Ok(("This is not a text file.".into(), true));
                }
                let mut text = String::from_utf8_lossy(&data[..data.len().min(MAX_READ)]).into_owned();
                if data.len() > MAX_READ {
                    text.push_str(&format!("\n… (cut at {} KB)", MAX_READ / 1024));
                }
                Ok((text, false))
            }
            _ => {
                let content = action.args.get("content").and_then(Value::as_str).unwrap_or("");
                if let Some(d) = action.path.parent() {
                    std::fs::create_dir_all(d)?;
                }
                sonata_config::atomic_write(&action.path, content.as_bytes(), true)?; // unique temp, removed on failure
                Ok((format!("{} {}", if action.exists { "Replaced" } else { "Created" }, action.path.display()), false))
            }
        }
    })();
    res.unwrap_or_else(|e| (e.to_string(), true))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn folders(d: &Path) -> Vec<String> {
        vec![d.join("allowed").display().to_string()]
    }

    #[test]
    fn nothing_outside_the_folders_is_reached() {
        let d = tempfile::tempdir().unwrap();
        let ok = d.path().join("allowed");
        std::fs::create_dir_all(ok.join("sub")).unwrap();
        std::fs::write(ok.join("notes.txt"), "hi").unwrap();
        std::fs::write(d.path().join("secret.txt"), "no").unwrap();
        std::os::unix::fs::symlink(d.path().join("secret.txt"), ok.join("link.txt")).unwrap();
        std::os::unix::fs::symlink(d.path(), ok.join("up")).unwrap();
        let f = folders(d.path());
        let p = |s: &str| json!({"path": s});
        assert!(check("read_file", &p(&ok.join("notes.txt").display().to_string()), &f).is_ok());
        // "..", a link to a file outside, a link to the folder above: refused
        for bad in [ok.join("../secret.txt"), ok.join("link.txt"), ok.join("up/secret.txt"), ok.join("sub/../../secret.txt")] {
            assert_eq!(check("read_file", &p(&bad.display().to_string()), &f).unwrap_err(), "That path is outside the folders the user allowed.", "{}", bad.display());
        }
        // a new file through a link out: refused too
        assert!(check("create_file", &json!({"path": ok.join("up/new.txt").display().to_string(), "content": "x"}), &f).is_err());
        // a folder whose name starts like the allowed one isn't inside it
        std::fs::create_dir_all(d.path().join("allowed-not")).unwrap();
        std::fs::write(d.path().join("allowed-not/x.txt"), "x").unwrap();
        assert!(check("read_file", &p(&d.path().join("allowed-not/x.txt").display().to_string()), &f).is_err());
        assert_eq!(check("read_file", &p("relative.txt"), &f).unwrap_err(), "The path must be absolute.");
        assert_eq!(check("rm_rf", &p("/"), &f).unwrap_err(), "Unknown tool: rm_rf");
        assert_eq!(check("read_file", &json!({}), &f).unwrap_err(), "A path is needed.");
    }

    #[test]
    fn creating_lists_and_reads() {
        let d = tempfile::tempdir().unwrap();
        let ok = d.path().join("allowed");
        std::fs::create_dir_all(&ok).unwrap();
        let f = folders(d.path());
        let path = ok.join("new/dir/file.txt").display().to_string();
        let act = check("create_file", &json!({"path": path, "content": "hello"}), &f).unwrap();
        assert!(!act.exists);
        assert_eq!(run(&act).1, false);
        assert_eq!(check("create_file", &json!({"path": path, "content": "x"}), &f).unwrap_err(), "The file already exists (set overwrite to replace it).");
        let again = check("create_file", &json!({"path": path, "content": "bye", "overwrite": true}), &f).unwrap();
        assert!(again.exists && run(&again).0.starts_with("Replaced"));
        let read = check("read_file", &json!({"path": path}), &f).unwrap();
        assert_eq!(run(&read), ("bye".into(), false));
        let list = check("list_folder", &json!({"path": ok.join("new").display().to_string()}), &f).unwrap();
        assert_eq!(run(&list).0, "dir/");
        std::fs::write(ok.join("bin"), [0u8, 1, 2]).unwrap();
        assert_eq!(run(&check("read_file", &json!({"path": ok.join("bin").display().to_string()}), &f).unwrap()), ("This is not a text file.".into(), true));
    }

    #[test]
    fn summaries() {
        sonata_i18n::use_language("en");
        let a = Action::of_block(&json!({"name": "read_file", "input": {"path": "/x/notes.txt"}}));
        assert_eq!(a.summary(), "Read notes.txt");
        let c = Action::of_block(&json!({"name": "create_file", "input": {"path": "/x/a.md", "overwrite": true}}));
        assert_eq!(c.summary(), "Replaced a.md");
    }
}
