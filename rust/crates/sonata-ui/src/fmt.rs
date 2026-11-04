//! Numbers as people read them (sonata2/ui/fmt.py).

use sonata_i18n::{fmt, language, tr};

/// A decimal comma where the language writes one ("1,4 MB").
fn decimal(text: &str) -> String {
    if matches!(language().as_str(), "pt_BR" | "pt_PT") { text.replace('.', ",") } else { text.to_string() }
}

/// Decimal units like Finder: "Zero bytes", "653 bytes", "12 KB", "1.4 MB".
/// Short counts: 950, 24K, 1.2M (tokens, items).
pub fn count(n: i64) -> String {
    if n < 1000 {
        return n.to_string();
    }
    let f = n as f64;
    if n < 1_000_000 {
        return if n >= 10_000 { format!("{:.0}K", f / 1000.0) } else { format!("{:.1}K", f / 1000.0).replace(".0K", "K") };
    }
    format!("{:.1}M", f / 1_000_000.0).replace(".0M", "M")
}

pub fn size(n: u64) -> String {
    if n == 0 {
        return tr!("Zero bytes");
    }
    if n < 1000 {
        return fmt(&tr!("{n} bytes"), &[("n", &n.to_string())]);
    }
    let kb = (n as f64 / 1000.0).round();
    if kb < 1000.0 {
        return format!("{kb} KB");
    }
    for (unit, p) in [("MB", 2), ("GB", 3), ("TB", 4)] {
        let v = n as f64 / 1000f64.powi(p);
        if ((v * 10.0).round() / 10.0) < 1000.0 || unit == "TB" {
            return format!("{} {unit}", decimal(&format!("{v:.1}")));
        }
    }
    unreachable!()
}

fn local_date(t: f64) -> Option<gtk::glib::DateTime> {
    gtk::glib::DateTime::from_unix_local(t as i64).ok()
}

fn day_number(d: &gtk::glib::DateTime) -> i64 {
    // whole days since the epoch, in local time
    let (y, m, dd) = (d.year(), d.month(), d.day_of_month());
    let (y, m) = (if m <= 2 { y - 1 } else { y } as i64, m as i64);
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + dd as i64 - 1;
    era * 146097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719468
}

/// (rank, sub-rank, title) of the list section a date falls in (macOS:
/// Pinned, Today, Yesterday, Previous 7 Days, Previous 30 Days, months, years).
pub fn date_section(t: f64, pinned: bool, now: f64) -> (i32, i32, String) {
    if pinned {
        return (0, 0, tr!("Pinned"));
    }
    let (Some(today), Some(d)) = (local_date(now), local_date(t)) else { return (6, 0, String::new()) };
    let days = day_number(&today) - day_number(&d);
    if days <= 0 {
        return (1, 0, tr!("Today"));
    }
    if days == 1 {
        return (2, 0, tr!("Yesterday"));
    }
    if days <= 7 {
        return (3, 0, tr!("Previous 7 Days"));
    }
    if days <= 30 {
        return (4, 0, tr!("Previous 30 Days"));
    }
    if d.year() == today.year() {
        return (5, -d.month(), d.format("%B").map(|s| s.to_string()).unwrap_or_default());
    }
    (6, -d.year(), d.year().to_string())
}

/// 14:05 today, Yesterday, a weekday this week, else the date.
pub fn short_date(t: f64, now: f64) -> String {
    let (Some(today), Some(d)) = (local_date(now), local_date(t)) else { return String::new() };
    let days = day_number(&today) - day_number(&d);
    let f = |p: &str| d.format(p).map(|s| s.to_string()).unwrap_or_default();
    if days <= 0 {
        f("%H:%M")
    } else if days == 1 {
        tr!("Yesterday")
    } else if days < 7 {
        f("%A")
    } else {
        f("%x")
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn sizes_like_finder() {
        sonata_i18n::use_language("en");
        assert_eq!(super::size(0), "Zero bytes");
        assert_eq!(super::size(653), "653 bytes");
        assert_eq!(super::size(12_400), "12 KB");
        assert_eq!(super::size(999_500), "1.0 MB");
        assert_eq!(super::size(1_400_000), "1.4 MB");
    }
}
