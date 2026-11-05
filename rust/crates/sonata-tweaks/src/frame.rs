//! The window frame as the user set it: sonata-ui's (ui/tokens.py's
//! frame(), button_layout, user_button_size...), where Sonata's own windows
//! read it too, and wayfire_color for the title bars and traffic lights
//! Wayfire draws.

pub use sonata_ui::frame::*;

/// 'rgba(r, g, b, a)' or '#rrggbb' -> Wayfire's '#rrggbbaa' (premultiplied for pixdecor).
pub fn wayfire_color(css: &str, premultiplied: bool) -> String {
    let css = css.trim();
    if css.starts_with('#') {
        return if css.len() == 7 { format!("{css}ff") } else { css.to_string() };
    }
    let inner = css.split_once('(').and_then(|(_, r)| r.split_once(')')).map(|(i, _)| i).unwrap_or("");
    let n: Vec<f64> = inner.split(',').map(|x| x.trim().parse().unwrap_or(0.0)).collect();
    let g = |i: usize| n.get(i).copied().unwrap_or(0.0);
    let a = if n.len() > 3 { g(3) } else { 1.0 };
    let k = if premultiplied { a } else { 1.0 };
    let b = |v: f64| v.round_ties_even() as i64;
    format!("#{:02x}{:02x}{:02x}{:02x}", b(g(0) * k), b(g(1) * k), b(g(2) * k), b(255.0 * a))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wayfire_colors() {
        assert_eq!(wayfire_color("#ff5f57", false), "#ff5f57ff");
        assert_eq!(wayfire_color("rgba(255, 255, 255, 0.14)", false), "#ffffff24");
        assert_eq!(wayfire_color("rgba(200, 100, 0, 0.5)", true), "#64320080");
        assert_eq!(wayfire_color("rgb(9, 9, 11)", false), "#09090bff");
    }
}
