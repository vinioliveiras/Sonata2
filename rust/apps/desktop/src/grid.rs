//! The desktop's grid and its spots (desktop.py's layout half, no GTK): a
//! grid that starts at the top right and fills columns downwards, spots
//! remembered in desktop.json -- {"positions": {name: [col, row] |
//! [col, row, connector]}, "sort": "none"|"name"|"kind"|"date"} -- and
//! which display each icon is on.

use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};

/// Big Sur's default desktop grid (64 px icons)
pub const CELL_W: i32 = 96;
pub const CELL_H: i32 = 104;
pub const ICON: i32 = 64;
/// off the screen edges
pub const EDGE: i32 = 14;

pub type Cell = (i32, i32);

pub fn defaults() -> Value {
    json!({"positions": {}, "sort": "none"})
}

/// Sort By (and Settings): (key, English label).
pub const SORTS: [(&str, &str); 4] = [("none", sonata_i18n::n_!("None")), ("name", sonata_i18n::n_!("Name")), ("kind", sonata_i18n::n_!("Kind")), ("date", sonata_i18n::n_!("Date Modified"))];

/// Below the menu bar.
pub fn top(menubar_height: i32) -> i32 {
    menubar_height + 10
}

/// The menu bar's height (shell/menubar_size.py; the menu bar's crate has
/// its own copy, which this one can't link: 32 px unless "Taller menu
/// bar" is off).
pub fn menubar_height() -> i32 {
    let cfg = sonata_config::load("topbar", &json!({"tall": true}));
    if cfg.get("tall").is_none_or(truthy) { 32 } else { 24 }
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

// the Dock's plate (dock.py's plate_height; the Dock's crate isn't a
// library this one links)
const PAD_TOP: i32 = 5;
const DOT: i32 = 4;
const DOT_GAP: i32 = 4;
const ART_INSET: f64 = 1.0 / 12.0;

fn int(v: Option<&Value>, default: i64) -> i64 {
    v.and_then(|v| v.as_i64().or_else(|| v.as_f64().map(|f| f as i64))).unwrap_or(default)
}

pub fn plate_height(icon_size: i32) -> i32 {
    let inner = (DOT_GAP - (icon_size as f64 * ART_INSET).round_ties_even() as i32).max(0);
    PAD_TOP + icon_size + inner + DOT + DOT_GAP
}

/// (left, right, bottom) kept free: the Dock's edge gets its height (it can
/// sit left, right or bottom; auto-hide leaves only the edge gap).
pub fn dock_margins(dock: &Map<String, Value>) -> (i32, i32, i32) {
    let thick = if dock.get("autohide").is_some_and(truthy) {
        4
    } else {
        plate_height(int(dock.get("icon_size"), 48) as i32) + int(dock.get("edge_gap"), 4) as i32 + 8
    };
    let side = dock.get("position").and_then(Value::as_str).unwrap_or("bottom");
    let on = |s: &str| if side == s { thick } else { 0 };
    (EDGE + on("left"), EDGE + on("right"), EDGE + on("bottom"))
}

/// The grid of one display: its size, the Dock's margins, the menu bar.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Grid {
    pub size: (i32, i32),
    pub margins: (i32, i32, i32),
    pub top: i32,
}

impl Grid {
    pub fn rows(&self) -> i32 {
        ((self.size.1 - self.top - self.margins.2).div_euclid(CELL_H)).max(1)
    }

    pub fn cols(&self) -> i32 {
        ((self.size.0 - self.margins.0 - self.margins.1).div_euclid(CELL_W)).max(1)
    }

    /// (width, height) of a grid cell: the free area shared out evenly, so
    /// the grid reaches both edges (Vini: the left side had a strip no icon
    /// could go to) -- never smaller than Big Sur's grid.
    pub fn cell_size(&self) -> (f64, f64) {
        let w = (self.size.0 - self.margins.0 - self.margins.1) as f64;
        let h = (self.size.1 - self.top - self.margins.2) as f64;
        ((CELL_W as f64).max(w / self.cols() as f64), (CELL_H as f64).max(h / self.rows() as f64))
    }

    /// Where an icon (CELL_W wide) sits: centred in its cell; columns
    /// counted from the right (macOS).
    pub fn cell_xy(&self, col: i32, row: i32) -> (i32, i32) {
        let (cw, ch) = self.cell_size();
        let x = (self.size.0 - self.margins.1) as f64 - (col + 1) as f64 * cw + (cw - CELL_W as f64) / 2.0;
        (x.round_ties_even() as i32, (self.top as f64 + row as f64 * ch).round_ties_even() as i32)
    }

    pub fn cell_at(&self, x: f64, y: f64) -> Cell {
        let (cw, ch) = self.cell_size();
        let col = (((self.size.0 - self.margins.1) as f64 - x) / cw).floor() as i32;
        let row = ((y - self.top as f64) / ch).floor() as i32;
        (col.clamp(0, self.cols() - 1), row.clamp(0, self.rows() - 1))
    }

    /// (c, r), or the next free cell below it (then the next column); None
    /// when the desktop is full.
    pub fn next_free(&self, mut c: i32, mut r: i32, taken: &HashSet<Cell>) -> Option<Cell> {
        while 0 <= c && c < self.cols() {
            if r >= self.rows() {
                (r, c) = (0, c + 1);
                continue;
            }
            if !taken.contains(&(c, r)) {
                return Some((c, r));
            }
            r += 1;
        }
        None
    }

    fn fits(&self, c: Cell) -> bool {
        // (negative spots, hand-edited, go to a free cell instead)
        c.0 >= 0 && c.1 >= 0 && c.0 < self.cols() && c.1 < self.rows()
    }
}

pub fn sort_of(cfg: &Map<String, Value>) -> String {
    cfg.get("sort").and_then(Value::as_str).unwrap_or("none").to_string()
}

fn positions(cfg: &Map<String, Value>) -> Map<String, Value> {
    cfg.get("positions").and_then(Value::as_object).cloned().unwrap_or_default()
}

/// The spot saved for `name`: (col, row).
pub fn spot(cfg: &Map<String, Value>, name: &str) -> Option<Cell> {
    spot_in(cfg.get("positions")?.as_object()?, name)
}

fn spot_in(pos: &Map<String, Value>, name: &str) -> Option<Cell> {
    let p = pos.get(name)?.as_array()?;
    if !p.first()?.is_number() || !p.get(1)?.is_number() {
        return None;
    }
    Some((int(p.first(), i64::MIN).try_into().ok()?, int(p.get(1), i64::MIN).try_into().ok()?))
}

/// The display an icon was put on ("" = the main one).
pub fn display_of(cfg: &Map<String, Value>, name: &str) -> String {
    if sort_of(cfg) != "none" {
        return String::new(); // sorted: all on the main display
    }
    cfg.get("positions").and_then(|p| p.get(name)).and_then(Value::as_array).and_then(|p| p.get(2)).and_then(Value::as_str).unwrap_or("").to_string()
}

/// Is this icon shown on this display's desktop?
pub fn mine(cfg: &Map<String, Value>, name: &str, main: bool, screen: &str, connected: &HashSet<String>) -> bool {
    let at = display_of(cfg, name);
    if main {
        return at.is_empty() || at == screen || !connected.contains(&at);
    }
    !screen.is_empty() && at == screen
}

/// Where every icon goes: its remembered spot first, then where it already
/// was, then the first free cells (columns from the right, downwards).
/// `names` in the order the icons are taken. Also gives the icons that got
/// a spot without a saved one (unsaved).
pub fn layout(g: &Grid, names: &[String], cfg: &Map<String, Value>, prev: &HashMap<String, Cell>) -> (HashMap<String, Cell>, HashMap<String, Cell>) {
    let unsorted = sort_of(cfg) == "none";
    let (rows, cols) = (g.rows(), g.cols());
    let pos = if unsorted { positions(cfg) } else { Map::new() };
    let mut taken = HashSet::new();
    let mut placed = HashMap::new();
    for name in names {
        if let Some(p) = spot_in(&pos, name) {
            if !taken.contains(&p) && g.fits(p) {
                placed.insert(name.clone(), p);
                taken.insert(p);
            }
        }
    }
    if unsorted {
        for name in names {
            if let Some(&p) = prev.get(name) {
                if !placed.contains_key(name) && !taken.contains(&p) && g.fits(p) {
                    placed.insert(name.clone(), p);
                    taken.insert(p);
                }
            }
        }
    }
    let mut slots = (0..cols).flat_map(|c| (0..rows).map(move |r| (c, r))).filter(|c| !taken.contains(c)).collect::<Vec<_>>().into_iter();
    for name in names {
        if !placed.contains_key(name) {
            placed.insert(name.clone(), slots.next().unwrap_or((cols - 1, rows - 1)));
        }
    }
    // an icon with no spot of its own keeps the one it got: deleting a file
    // shuffled every icon after it (they took the first free cells in name
    // order again), and so did logging in again
    let unsaved = if unsorted { placed.iter().filter(|(n, _)| !pos.contains_key(*n)).map(|(n, c)| (n.clone(), *c)).collect() } else { HashMap::new() };
    (placed, unsaved)
}

fn entry(c: Cell, tag: Option<&str>) -> Value {
    let mut v = vec![json!(c.0), json!(c.1)];
    if let Some(t) = tag {
        v.push(json!(t));
    }
    Value::Array(v)
}

/// The spots the layout gave, kept (unsorted only; not over a spot saved
/// meanwhile). True when `cfg` changed.
pub fn keep_spots(cfg: &mut Map<String, Value>, spots: &HashMap<String, Cell>, shown: &dyn Fn(&str) -> bool, placed: &HashMap<String, Cell>, tag: Option<&str>) -> bool {
    if sort_of(cfg) != "none" {
        return false;
    }
    let mut pos = positions(cfg);
    let mut changed = false;
    for (n, c) in spots {
        if !pos.contains_key(n) && shown(n) && placed.get(n) == Some(c) {
            pos.insert(n.clone(), entry(*c, tag));
            changed = true;
        }
    }
    if changed {
        cfg.insert("positions".into(), Value::Object(pos));
    }
    changed
}

/// Icons moved by hand to new spots (`tag`: the display, when not the
/// main one). Leaving a sort: the other icons stay where it put them
/// (they jumped back to their old hand-made spots; Finder keeps them).
/// Files gone from ~/Desktop lose their spot (`exists`), not a moved one
/// (a folder being made right now).
pub fn save_positions(cfg: &mut Map<String, Value>, moved: &HashMap<String, Cell>, placed: &HashMap<String, Cell>, tag: Option<&str>, exists: &dyn Fn(&str) -> bool) {
    let mut pos = positions(cfg);
    let sorted = sort_of(cfg) != "none";
    if sorted {
        for (k, v) in placed {
            if !moved.contains_key(k) {
                pos.insert(k.clone(), entry(*v, tag));
            }
        }
    }
    for (k, v) in moved {
        pos.insert(k.clone(), entry(*v, tag));
    }
    pos.retain(|k, _| moved.contains_key(k) || exists(k));
    cfg.insert("positions".into(), Value::Object(pos));
    if sorted {
        cfg.insert("sort".into(), json!("none")); // moving an icon by hand (Finder does the same)
    }
}

/// Finder's Clean Up By Name for one display: its icons (`order`: the
/// folder's name order, those shown here) into the first cells. Icons on
/// the other displays stay there: wiping every spot brought them all to
/// the main display.
pub fn clean_up(cfg: &mut Map<String, Value>, g: &Grid, order: &[String], shown: &dyn Fn(&str) -> bool, tag: Option<&str>) {
    let mut pos = positions(cfg);
    pos.retain(|k, _| !shown(k));
    let mut free = (0..g.cols()).flat_map(|c| (0..g.rows()).map(move |r| (c, r)));
    for name in order.iter().filter(|n| shown(n)) {
        let Some(cell) = free.next() else { break };
        pos.insert(name.clone(), entry(cell, tag));
    }
    cfg.insert("positions".into(), Value::Object(pos));
    cfg.insert("sort".into(), json!("none"));
}

/// Our own icons dropped at `at`: each keeps its offset from the one
/// dragged (`anchor`), moved to the next free cell when taken.
pub fn drop_own(g: &Grid, names: &[String], placed: &HashMap<String, Cell>, anchor: Option<Cell>, at: Cell) -> HashMap<String, Cell> {
    let anchor = anchor.unwrap_or(at);
    let mut taken: HashSet<Cell> = placed.iter().filter(|(k, _)| !names.contains(k)).map(|(_, v)| *v).collect();
    let mut moved = HashMap::new();
    for name in names {
        let p = placed.get(name).copied().unwrap_or(anchor);
        let c = (p.0 + at.0 - anchor.0).clamp(0, g.cols() - 1);
        let r = (p.1 + at.1 - anchor.1).clamp(0, g.rows() - 1);
        if let Some(cell) = g.next_free(c, r, &taken) {
            moved.insert(name.clone(), cell);
            taken.insert(cell);
        }
    }
    moved
}

/// Files dropped at `at`, every one where it was dropped, one under the
/// other (only the first got the spot; the rest went to the top right).
/// Skipped (`skip`): a name already on the Desktop -- the copy gets another
/// name, and the icon already there was sent here.
pub fn drop_spots(g: &Grid, names: &[String], placed: &HashMap<String, Cell>, at: Cell, skip: &dyn Fn(&str) -> bool) -> HashMap<String, Cell> {
    let mut taken: HashSet<Cell> = placed.values().copied().collect();
    let mut moved = HashMap::new();
    for name in names {
        if name.is_empty() || skip(name) {
            continue;
        }
        let Some(cell) = g.next_free(at.0, at.1, &taken) else { break };
        moved.insert(name.clone(), cell);
        taken.insert(cell);
    }
    moved
}

#[cfg(test)]
mod tests {
    use super::*;

    fn grid() -> Grid {
        Grid { size: (1280, 800), margins: (EDGE, EDGE, EDGE + 80), top: top(32) }
    }

    fn cfg(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    fn names(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn the_grid_starts_top_right_and_fills_columns() {
        let g = grid();
        let (placed, unsaved) = layout(&g, &names(&["a", "b"]), &cfg(defaults()), &HashMap::new());
        assert_eq!(placed["a"], (0, 0));
        assert_eq!(placed["b"], (0, 1));
        assert_eq!(unsaved.len(), 2);
        let (x0, _) = g.cell_xy(0, 0);
        let (x1, _) = g.cell_xy(1, 0);
        assert!(x0 > x1); // columns from the right
        assert_eq!(g.cell_at(x0 as f64 + 10.0, g.top as f64 + 5.0), (0, 0));
    }

    #[test]
    fn test_icons_split_between_displays() {
        let c = cfg(json!({"positions": {"b.txt": [0, 1, "HDMI-A-1"], "c.txt": [0, 0, "DP-9"]}, "sort": "none"}));
        let plugged: HashSet<String> = ["eDP-1", "HDMI-A-1"].iter().map(|s| s.to_string()).collect();
        let on = |main, screen| ["a.txt", "b.txt", "c.txt"].into_iter().filter(|n| mine(&c, n, main, screen, &plugged)).collect::<Vec<_>>();
        assert_eq!(on(false, "HDMI-A-1"), ["b.txt"]);
        assert_eq!(on(true, "eDP-1"), ["a.txt", "c.txt"]); // DP-9 isn't plugged in: on the main one
    }

    #[test]
    fn test_spots_on_another_display_name_it_and_survive() {
        let mut c = cfg(json!({"positions": {"c.txt": [0, 0, "DP-9"]}, "sort": "none"}));
        let moved = HashMap::from([("a.txt".to_string(), (2, 3))]);
        save_positions(&mut c, &moved, &HashMap::new(), Some("HDMI-A-1"), &|_| true);
        assert_eq!(c["positions"]["a.txt"], json!([2, 3, "HDMI-A-1"]));
        assert_eq!(c["positions"]["c.txt"], json!([0, 0, "DP-9"])); // other icons' spots kept
        save_positions(&mut c, &HashMap::from([("a.txt".to_string(), (0, 0))]), &HashMap::new(), None, &|_| true);
        assert_eq!(c["positions"]["a.txt"], json!([0, 0])); // dragged back: the main display
    }

    #[test]
    fn test_new_folder_spot_not_pruned() {
        let mut c = cfg(defaults());
        save_positions(&mut c, &HashMap::from([("New Folder".to_string(), (1, 1))]), &HashMap::new(), Some("HDMI-A-1"), &|_| false);
        assert_eq!(c["positions"]["New Folder"], json!([1, 1, "HDMI-A-1"])); // being made: not on disk yet
    }

    /// Dragging one icon with Sort By on put every other icon back where it
    /// was before sorting.
    #[test]
    fn test_dragging_while_sorted_keeps_the_other_icons() {
        let g = grid();
        let mut c = cfg(json!({"positions": {"zeta.png": [4, 4], "alpha.txt": [3, 3]}, "sort": "kind"}));
        let order = names(&["Projects", "alpha.txt", "beta.pdf", "zeta.png"]);
        let (placed, _) = layout(&g, &order, &c, &HashMap::new());
        let moved = drop_own(&g, &names(&["zeta.png"]), &placed, placed.get("zeta.png").copied(), (5, 3));
        save_positions(&mut c, &moved, &placed, None, &|_| true);
        assert_eq!(sort_of(&c), "none");
        let (after, _) = layout(&g, &order, &c, &placed);
        assert_eq!(after["zeta.png"], (5, 3));
        for (n, s) in &placed {
            if n != "zeta.png" {
                assert_eq!(after[n], *s, "{n}");
            }
        }
    }

    #[test]
    fn test_deleting_a_file_doesnt_shuffle_the_other_icons() {
        let g = grid();
        let mut c = cfg(defaults());
        let all = names(&["Projects", "alpha.txt", "beta.pdf", "gamma.txt"]);
        let (placed, unsaved) = layout(&g, &all, &c, &HashMap::new());
        keep_spots(&mut c, &unsaved, &|_| true, &placed, None);
        let rest = names(&["Projects", "alpha.txt", "gamma.txt"]);
        let (after, _) = layout(&g, &rest, &c, &HashMap::new()); // (logging in again too)
        for n in &rest {
            assert_eq!(after[n], placed[n], "{n}");
        }
    }

    /// Clean Up wiped every spot: the second display's icons all went to the
    /// main one.
    #[test]
    fn test_clean_up_keeps_icons_on_the_other_display() {
        let g = grid();
        let plugged: HashSet<String> = ["eDP-1", "HDMI-A-1"].iter().map(|s| s.to_string()).collect();
        let mut c = cfg(json!({"positions": {"gamma.txt": [2, 2, "HDMI-A-1"]}, "sort": "none"}));
        let order = names(&["alpha.txt", "gamma.txt"]);
        let before = c.clone();
        clean_up(&mut c, &g, &order, &|n| mine(&before, n, true, "eDP-1", &plugged), None);
        assert_eq!(c["positions"]["gamma.txt"], json!([2, 2, "HDMI-A-1"]));
        assert!(!mine(&c, "gamma.txt", true, "eDP-1", &plugged));
        let before = c.clone();
        clean_up(&mut c, &g, &order, &|n| mine(&before, n, false, "HDMI-A-1", &plugged), Some("HDMI-A-1"));
        assert_eq!(c["positions"]["gamma.txt"], json!([0, 0, "HDMI-A-1"])); // its own Clean Up keeps it there too
    }

    #[test]
    fn test_clean_up_sorts_by_name_from_the_first_cell() {
        let g = grid();
        let mut c = cfg(json!({"positions": {"alpha.txt": [4, 4]}, "sort": "none"}));
        clean_up(&mut c, &g, &names(&["Projects", "alpha.txt"]), &|_| true, None);
        let (placed, _) = layout(&g, &names(&["Projects", "alpha.txt"]), &c, &HashMap::new());
        assert_eq!(placed["Projects"], (0, 0));
        assert_eq!(placed["alpha.txt"], (0, 1));
    }

    /// Only the first dropped file went to the drop spot; the others went to
    /// the top right.
    #[test]
    fn test_dropping_several_files_puts_them_all_where_dropped() {
        let g = grid();
        let placed = HashMap::from([("alpha.txt".to_string(), (0, 0))]);
        let moved = drop_spots(&g, &names(&["one.txt", "two.txt", "alpha.txt"]), &placed, (6, 2), &|n| n == "alpha.txt");
        assert_eq!(moved["one.txt"], (6, 2));
        assert_eq!(moved["two.txt"], (6, 3));
        assert!(!moved.contains_key("alpha.txt")); // the icon already there stays put
    }

    #[test]
    fn test_drag_into_a_full_column_stays_on_the_grid() {
        let g = grid();
        let rows = g.rows();
        let taken: HashSet<Cell> = (0..rows).map(|r| (g.cols() - 1, r)).collect();
        assert_eq!(g.next_free(g.cols() - 1, 0, &taken), None);
        assert_eq!(g.next_free(g.cols() - 2, rows - 1, &taken), Some((g.cols() - 2, rows - 1)));
    }

    /// The grid started under the menu bar of when the session began:
    /// switching Taller menu bar left icons under it, or a gap.
    #[test]
    fn test_taller_menu_bar_moves_the_icons() {
        let short = Grid { top: top(24), ..grid() };
        let tall = Grid { top: top(32), ..grid() };
        assert_eq!(tall.cell_xy(0, 0).1 - short.cell_xy(0, 0).1, 8);
    }

    #[test]
    fn the_docks_edge_is_kept_free() {
        let dock = cfg(json!({"icon_size": 48, "edge_gap": 4, "position": "left"}));
        // dock.py: PAD_TOP 5 + 48 + (4 - round(48/12)=0) + 4 + 4 = 61; + gap 4 + 8
        assert_eq!(dock_margins(&dock), (EDGE + 73, EDGE, EDGE));
        assert_eq!(dock_margins(&cfg(json!({"autohide": true}))), (EDGE, EDGE, EDGE + 4));
        assert_eq!(dock_margins(&Map::new()), (EDGE, EDGE, EDGE + 73));
    }

    #[test]
    fn spots_too_far_or_hand_edited_go_to_a_free_cell() {
        let g = grid();
        let c = cfg(json!({"positions": {"a": [99, 0], "b": ["x", 1], "c": [-1, 0]}, "sort": "none"}));
        let (placed, _) = layout(&g, &names(&["a", "b", "c"]), &c, &HashMap::new());
        assert_eq!(placed["a"], (0, 0));
        assert_eq!(placed["b"], (0, 1));
        assert_eq!(placed["c"], (0, 2));
    }
}
