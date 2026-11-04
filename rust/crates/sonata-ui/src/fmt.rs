//! Numbers as people read them (sonata2/ui/fmt.py).

use sonata_i18n::{fmt, language, tr};

/// A decimal comma where the language writes one ("1,4 MB").
fn decimal(text: &str) -> String {
    if matches!(language().as_str(), "pt_BR" | "pt_PT") { text.replace('.', ",") } else { text.to_string() }
}

/// Decimal units like Finder: "Zero bytes", "653 bytes", "12 KB", "1.4 MB".
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
