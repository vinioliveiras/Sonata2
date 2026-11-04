//! The Calculator's arithmetic (macOS Calculator, Basic), ported from
//! `sonata2/calculator/engine.py`: decimal numbers, × and ÷ before + and −,
//! repeated =, % and ±. No GTK: tested on its own.
//!
//! ```
//! use sonata_calculator::engine::Engine;
//! let mut c = Engine::new();
//! for k in ["1", "2", "+", "3", "×", "4", "="] { c.press(k); }
//! assert_eq!(c.display(), "24");
//! ```
//! Keys: digits, ".", "+", "−", "×", "÷", "=", "%", "±", "C" (clear entry,
//! then all), "AC", "⌫" (last digit).

use crate::decimal::{Dec, MathError};

/// Digits you can type.
pub const MAX_DIGITS: usize = 15;
/// Significant digits a result shows.
pub const SHOWN_DIGITS: u32 = 12;
/// The precision the four operations work at (engine.py's localcontext).
const WORK_PREC: u32 = 34;
/// Python's default context: % and ± run outside the engine's localcontext.
const DEFAULT_PREC: u32 = 28;

pub const OPS: [char; 4] = ['+', '−', '×', '÷'];

fn level(op: char) -> u8 {
    if op == '×' || op == '÷' { 2 } else { 1 }
}

#[derive(Clone, Debug)]
pub enum Tok {
    Num(Dec),
    Op(char),
}

#[derive(Debug)]
pub struct Engine {
    /// [n, op, n, op, ...] waiting for =
    pub tokens: Vec<Tok>,
    /// The number being typed (text); None: showing a result.
    pub entry: Option<String>,
    /// What is shown when not typing.
    pub value: Dec,
    /// (op, operand) for repeated =
    last: Option<(char, Dec)>,
    pub error: bool,
}

impl Default for Engine {
    fn default() -> Self {
        Self::new()
    }
}

fn apply(a: &Dec, op: char, b: &Dec) -> Result<Dec, MathError> {
    match op {
        '+' => a.add(b, WORK_PREC),
        '−' => a.sub(b, WORK_PREC),
        '×' => a.mul(b, WORK_PREC),
        _ => a.div(b, WORK_PREC),
    }
}

impl Engine {
    pub fn new() -> Self {
        Engine { tokens: Vec::new(), entry: None, value: Dec::zero(), last: None, error: false }
    }

    pub fn all_clear(&mut self) {
        *self = Engine::new();
    }

    /// macOS: "C" while there is an entry to clear, "AC" otherwise.
    pub fn clear_label(&self) -> &'static str {
        let typing = matches!(self.entry.as_deref(), Some(e) if e != "0");
        if typing || self.error { "C" } else { "AC" }
    }

    /// The operator waiting for a number (shown highlighted), if any.
    pub fn waiting_op(&self) -> Option<char> {
        match (self.entry.as_ref(), self.tokens.last()) {
            (None, Some(Tok::Op(op))) => Some(*op),
            _ => None,
        }
    }

    fn current(&self) -> Dec {
        match self.entry.as_deref() {
            Some(e) if !e.is_empty() && e != "-" => Dec::parse(e).unwrap_or_else(|| self.value.clone()),
            _ => self.value.clone(),
        }
    }

    pub fn press(&mut self, key: &str) {
        if self.error && key != "C" && key != "AC" {
            self.all_clear();
        }
        let mut chars = key.chars();
        let (first, single) = (chars.next(), chars.next().is_none());
        match (first, single) {
            (Some(d), true) if d.is_ascii_digit() => self.digit(d),
            (Some('.'), true) => self.point(),
            (Some(op), true) if OPS.contains(&op) => self.operator(op),
            (Some('='), true) => self.equals(),
            (Some('%'), true) => self.percent(),
            (Some('±'), true) => self.negate(),
            (Some('⌫'), true) => self.backspace(),
            (Some('C'), true) => {
                if self.clear_label() == "C" {
                    self.entry = Some("0".into());
                    self.error = false;
                } else {
                    self.all_clear();
                }
            }
            _ if key == "AC" => self.all_clear(),
            _ => {}
        }
    }

    fn digit(&mut self, d: char) {
        let entry = self.entry.clone();
        match entry.as_deref() {
            None | Some("0") => self.entry = Some(d.to_string()),
            Some("-0") => self.entry = Some(format!("-{d}")),
            Some(e) => {
                if e.chars().filter(char::is_ascii_digit).count() < MAX_DIGITS {
                    self.entry = Some(format!("{e}{d}"));
                }
            }
        }
    }

    fn point(&mut self) {
        let e = self.entry.get_or_insert_with(|| "0".into());
        if !e.contains('.') {
            e.push('.');
        }
    }

    fn backspace(&mut self) {
        let Some(e) = self.entry.as_mut() else { return };
        e.pop();
        if e.is_empty() || e.as_str() == "-" {
            *e = "0".into();
        }
    }

    fn negate(&mut self) {
        if let Some(e) = self.entry.take() {
            self.entry = Some(match e.strip_prefix('-') {
                Some(rest) => rest.to_string(),
                None => format!("-{e}"),
            });
            return;
        }
        match self.value.neg().round(DEFAULT_PREC) {
            Ok(v) => self.value = v,
            Err(_) => self.fail(),
        }
    }

    fn percent(&mut self) {
        let x = self.current();
        let hundred = Dec::from_i64(100);
        let n = self.tokens.len();
        let r = match (n >= 2).then(|| (&self.tokens[n - 2], &self.tokens[n - 1])) {
            // 200 + 10 % -> 200 + 20 (macOS)
            Some((Tok::Num(a), Tok::Op('+' | '−'))) => {
                a.mul(&x, DEFAULT_PREC).and_then(|ax| ax.div(&hundred, DEFAULT_PREC))
            }
            _ => x.div(&hundred, DEFAULT_PREC),
        };
        match r {
            Ok(v) => {
                self.entry = None;
                self.value = v;
            }
            Err(_) => self.fail(),
        }
    }

    fn operator(&mut self, op: char) {
        if self.entry.is_none() {
            if let Some(Tok::Op(last)) = self.tokens.last_mut() {
                *last = op; // changed my mind: the new operator
                return;
            }
        }
        let cur = self.current();
        self.tokens.push(Tok::Num(cur));
        self.tokens.push(Tok::Op(op));
        self.entry = None;
        self.last = None;
        // show what is known so far: everything for + and −, the product chain for × and ÷
        let known = self.tokens[..self.tokens.len() - 1].to_vec();
        match fold(&known, level(op)) {
            Ok(v) => self.value = v,
            Err(_) => self.fail(),
        }
    }

    fn equals(&mut self) {
        let r = if let Some(Tok::Op(op)) = self.tokens.last() {
            let operand = self.current();
            let op = *op;
            let mut expr = self.tokens.clone();
            expr.push(Tok::Num(operand.clone()));
            self.last = Some((op, operand));
            fold(&expr, 0)
        } else if let Some((op, operand)) = &self.last {
            apply(&self.current(), *op, operand) // = again: repeat the last operation
        } else {
            Ok(self.current())
        };
        match r {
            Ok(v) => {
                self.value = v;
                self.tokens.clear();
                self.entry = None;
            }
            Err(_) => self.fail(),
        }
    }

    fn fail(&mut self) {
        self.all_clear();
        self.error = true;
    }

    /// What the display shows ("Error" is translated by the window).
    pub fn display(&self) -> String {
        if self.error {
            return "Error".into();
        }
        match &self.entry {
            Some(e) => group(e),
            None => fmt(&self.value),
        }
    }
}

/// Evaluate [n, op, n, ...] with × ÷ first; level 2 only folds the trailing
/// × ÷ chain (what macOS shows after pressing × or ÷).
fn fold(expr: &[Tok], level: u8) -> Result<Dec, MathError> {
    let mut start = 0;
    if level == 2 {
        let mut i = expr.len() - 1;
        while i >= 2 && matches!(expr[i - 1], Tok::Op('×' | '÷')) {
            i -= 2;
        }
        start = i;
    }
    let expr = &expr[start..];
    let num = |t: &Tok| match t {
        Tok::Num(n) => n.clone(),
        Tok::Op(_) => Dec::zero(),
    };
    let mut terms = vec![num(&expr[0])];
    let mut ops = Vec::new();
    for pair in expr[1..].chunks(2) {
        let (Tok::Op(op), Some(n)) = (&pair[0], pair.get(1)) else { continue };
        let n = num(n);
        if *op == '×' || *op == '÷' {
            let t = terms.pop().unwrap_or_else(Dec::zero);
            terms.push(apply(&t, *op, &n)?);
        } else {
            ops.push(*op);
            terms.push(n);
        }
    }
    let mut total = terms[0].clone();
    for (op, n) in ops.iter().zip(&terms[1..]) {
        total = apply(&total, *op, n)?;
    }
    Ok(total)
}

/// Thousands separators on what is being typed ("1234.5" -> "1,234.5").
pub fn group(text: &str) -> String {
    let neg = text.starts_with('-');
    let body = text.trim_start_matches('-');
    let (whole, frac) = match body.split_once('.') {
        Some((w, f)) => (w, Some(f)),
        None => (body, None),
    };
    let whole = whole.trim_start_matches('0');
    let whole = if whole.is_empty() { "0" } else { whole };
    let mut grouped = String::new();
    for (i, c) in whole.chars().enumerate() {
        if i > 0 && (whole.len() - i) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(c);
    }
    let mut out = String::new();
    if neg {
        out.push('-');
    }
    out.push_str(&grouped);
    if let Some(f) = frac {
        out.push('.');
        out.push_str(f);
    }
    out
}

/// A result: up to SHOWN_DIGITS significant digits, grouped; scientific
/// notation when it doesn't fit (1.2345e20).
pub fn fmt(x: &Dec) -> String {
    if x.is_zero() {
        return "0".into();
    }
    let Ok(r) = x.round(SHOWN_DIGITS) else { return "Error".into() };
    let exp = r.adjusted();
    if (-8..SHOWN_DIGITS as i64).contains(&exp) {
        let mut s = r.to_plain();
        if s.contains('.') {
            s = s.trim_end_matches('0').trim_end_matches('.').to_string();
        }
        return group(&s);
    }
    format!("{}e{exp}", r.mantissa())
}

#[cfg(test)]
mod tests;
