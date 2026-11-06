//! Your files by name (spotlight.py's Index): the home folder, INDEX_DEPTH
//! folders deep, MAX_FILES at most, hidden folders and build folders left
//! out. Built on another thread; one build is never changed once made (the
//! search reads it while the next is built). Compact, as the Python one: each
//! folder's path once, the names as one string per case with their offsets.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const MAX_FILES: usize = 60000;
pub const INDEX_DEPTH: usize = 5;
pub const INDEX_TTL: Duration = Duration::from_secs(300);
/// a name starting with the search ends the scan once this many are found
const PREFIX_LIMIT: usize = 200;
const SKIPPED: [&str; 2] = ["node_modules", "__pycache__"];

#[derive(Default)]
pub struct Names {
    roots: Vec<PathBuf>,
    parent: Vec<u32>,
    dirs: Vec<bool>,
    /// "\nname\nname...": entry i's name begins at starts[i] (after its "\n")
    names: String,
    starts: Vec<usize>,
    lower: String,
    lstarts: Vec<usize>,
}

fn join(names: &[String]) -> (String, Vec<usize>) {
    let mut s = String::with_capacity(names.iter().map(|n| n.len() + 1).sum());
    let mut starts = Vec::with_capacity(names.len());
    for n in names {
        s.push('\n');
        starts.push(s.len());
        s.push_str(n);
    }
    (s, starts)
}

impl Names {
    pub fn new(roots: Vec<PathBuf>, parent: Vec<u32>, names: Vec<String>, dirs: Vec<bool>) -> Self {
        let lowers: Vec<String> = names.iter().map(|n| n.to_lowercase()).collect();
        let (names, starts) = join(&names);
        let (lower, lstarts) = join(&lowers);
        Self { roots, parent, dirs, names, starts, lower, lstarts }
    }

    pub fn len(&self) -> usize {
        self.dirs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.dirs.is_empty()
    }

    fn end(starts: &[usize], text: &str, i: usize) -> usize {
        starts.get(i + 1).map(|s| s - 1).unwrap_or(text.len())
    }

    pub fn entry(&self, i: usize) -> (PathBuf, bool) {
        let name = &self.names[self.starts[i]..Self::end(&self.starts, &self.names, i)];
        (self.roots[self.parent[i] as usize].join(name), self.dirs[i])
    }

    /// The entry whose (lower-case) name holds byte `pos`.
    fn line(&self, pos: usize) -> usize {
        self.lstarts.partition_point(|s| *s <= pos).saturating_sub(1)
    }

    fn name_chars(&self, i: usize) -> usize {
        self.lower[self.lstarts[i]..Self::end(&self.lstarts, &self.lower, i)].chars().count()
    }

    /// Names starting with q first, then names containing it, each shortest
    /// first (then by path) -- the 201st name starting with it ends the
    /// scan, in index order.
    pub fn search(&self, q: &str) -> Vec<(PathBuf, bool)> {
        let q = q.to_lowercase();
        let n = self.len();
        if n == 0 || q.contains('\n') {
            return Vec::new();
        }
        let (mut pre, mut last) = (Vec::new(), n - 1);
        let needle = format!("\n{q}");
        let mut at = 0;
        while let Some(found) = self.lower[at..].find(&needle).map(|f| f + at) {
            let i = self.line(found + 1);
            pre.push(i);
            if pre.len() > PREFIX_LIMIT {
                last = i;
                break;
            }
            at = found + 1;
        }
        let stop = Self::end(&self.lstarts, &self.lower, last);
        let mut sub = Vec::new();
        let mut at = 1;
        while !q.is_empty() && at < stop {
            let Some(found) = self.lower[at..stop].find(&q).map(|f| f + at) else { break };
            let i = self.line(found);
            if !pre.contains(&i) {
                sub.push(i);
            }
            if i + 1 >= n {
                break;
            }
            at = self.lstarts[i + 1]; // one hit per name is enough
        }
        let ranked = |idx: Vec<usize>| {
            // (the path as a string, as Python's tuples compare it)
            let mut rows: Vec<(usize, String, PathBuf, bool)> = idx.into_iter().map(|i| {
                let (p, d) = self.entry(i);
                (self.name_chars(i), p.to_string_lossy().into_owned(), p, d)
            }).collect();
            rows.sort_by(|a, b| (a.0, &a.1, a.3).cmp(&(b.0, &b.1, b.3)));
            rows.into_iter().map(|(_, _, p, d)| (p, d))
        };
        ranked(pre).chain(ranked(sub)).collect()
    }
}

/// os.walk(home): top-down, depth first, folders in the order the file
/// system lists them.
pub fn build(home: &Path) -> Names {
    let (mut roots, mut parent, mut names, mut dirs) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
    let base = home.components().count();
    let mut stack = vec![home.to_path_buf()];
    while let Some(root) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&root) else { continue };
        let depth = root.components().count() - base;
        let (mut subdirs, mut files) = (Vec::new(), Vec::new());
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            // (a link to a folder is listed as one, never walked into)
            let is_dir = e.file_type().is_ok_and(|t| t.is_dir() || (t.is_symlink() && e.path().is_dir()));
            let walk = e.file_type().is_ok_and(|t| t.is_dir());
            if is_dir {
                if !name.starts_with('.') && !SKIPPED.contains(&name.as_str()) {
                    subdirs.push((name, walk));
                }
            } else if !name.starts_with('.') {
                files.push(name);
            }
        }
        if depth >= INDEX_DEPTH {
            subdirs.clear(); // the deepest folders: their files only
        }
        roots.push(root.clone());
        let me = (roots.len() - 1) as u32;
        let mut add = |name: &String, d: bool| {
            if !name.contains('\n') {
                // (the separator: such a name isn't searchable)
                parent.push(me);
                names.push(name.clone());
                dirs.push(d);
            }
        };
        for (d, _) in &subdirs {
            add(d, true);
        }
        for f in &files {
            add(f, false);
        }
        if dirs.len() > MAX_FILES {
            break;
        }
        for (d, walk) in subdirs.iter().rev() {
            if *walk {
                stack.push(root.join(d));
            }
        }
    }
    Names::new(roots, parent, names, dirs)
}

/// The index, rebuilt in the background when older than INDEX_TTL.
#[derive(Clone, Default)]
pub struct Index {
    data: Arc<Mutex<Arc<Names>>>,
    state: Arc<Mutex<(Option<Instant>, bool)>>,
}

impl Index {
    pub fn refresh(&self) {
        let mut st = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if st.1 || st.0.is_some_and(|t| t.elapsed() < INDEX_TTL) {
            return;
        }
        st.1 = true;
        let (data, state) = (self.data.clone(), self.state.clone());
        std::thread::spawn(move || {
            let names = Arc::new(build(&glib::home_dir()));
            *data.lock().unwrap_or_else(|e| e.into_inner()) = names; // one swap: never half built
            *state.lock().unwrap_or_else(|e| e.into_inner()) = (Some(Instant::now()), false);
        });
    }

    pub fn search(&self, q: &str) -> Vec<(PathBuf, bool)> {
        let names = self.data.lock().unwrap_or_else(|e| e.into_inner()).clone();
        names.search(q)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(list: &[(&str, &str, bool)]) -> Names {
        let mut roots: Vec<PathBuf> = Vec::new();
        let (mut parent, mut ns, mut dirs) = (Vec::new(), Vec::new(), Vec::new());
        for (root, name, d) in list {
            let r = PathBuf::from(root);
            let i = roots.iter().position(|x| *x == r).unwrap_or_else(|| {
                roots.push(r);
                roots.len() - 1
            });
            parent.push(i as u32);
            ns.push(name.to_string());
            dirs.push(*d);
        }
        Names::new(roots, parent, ns, dirs)
    }

    #[test]
    fn prefixes_first_then_shortest() {
        let n = names(&[("/h", "Report final.pdf", false), ("/h", "Old report.txt", false), ("/h/docs", "report.md", false), ("/h", "Reports", true), ("/h", "Ünïcode Rëport", false)]);
        let got: Vec<String> = n.search("report").iter().map(|(p, _)| p.display().to_string()).collect();
        assert_eq!(got, ["/h/Reports", "/h/docs/report.md", "/h/Report final.pdf", "/h/Old report.txt"]);
        assert_eq!(n.search("rëport")[0].0, PathBuf::from("/h/Ünïcode Rëport"));
        assert!(n.search("nothing").is_empty());
        assert!(n.search("a\nb").is_empty());
    }

    #[test]
    fn the_walk_skips_hidden_and_build_folders() {
        let d = tempfile::tempdir().unwrap();
        let h = d.path();
        for p in ["Docs/a/b/c/d/e/f", ".hidden/x", "code/node_modules/pkg", "code/src"] {
            std::fs::create_dir_all(h.join(p)).unwrap();
        }
        for f in ["Docs/notes.txt", ".secret", ".hidden/x/in.txt", "code/node_modules/pkg/index.js", "code/src/main.rs", "Docs/a/b/c/d/five.txt", "Docs/a/b/c/d/e/deep.txt", "Docs/a/b/c/d/e/f/deeper.txt"] {
            std::fs::write(h.join(f), "").unwrap();
        }
        let n = build(h);
        let all: Vec<String> = (0..n.len()).map(|i| n.entry(i).0.strip_prefix(h).unwrap().display().to_string()).collect();
        // os.walk with INDEX_DEPTH 5: the fifth level's files, not its folders
        for want in ["Docs", "Docs/notes.txt", "code/src/main.rs", "Docs/a/b/c/d", "Docs/a/b/c/d/five.txt"] {
            assert!(all.contains(&want.to_string()), "{want}: {all:?}");
        }
        for not in [".secret", ".hidden", "code/node_modules", "Docs/a/b/c/d/e", "Docs/a/b/c/d/e/deep.txt"] {
            assert!(!all.iter().any(|a| a == not), "{not}");
        }
        assert_eq!(n.search("main.rs")[0], (h.join("code/src/main.rs"), false));
    }
}
