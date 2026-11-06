//! Spotlight's calculator (spotlight.py's calculate): numbers and
//! + - * / % ** (^, x and ÷ too) and parentheses, nothing else -- the
//! Python one evaluates its ast with the same rules: Python's precedence,
//! integers kept exact, a division giving a float, the power kept small
//! (a huge one would take forever to print).

use num_bigint::BigInt;
use num_traits::{Signed, ToPrimitive, Zero};

/// ~4200 digits: under Python's int->str limit, instant to compute
const MAX_BITS: u64 = 14000;

#[derive(Clone, Debug, PartialEq)]
enum Num {
    Int(BigInt),
    Float(f64),
}

impl Num {
    fn f(&self) -> f64 {
        match self {
            Num::Int(i) => i.to_f64().unwrap_or(f64::INFINITY),
            Num::Float(f) => *f,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
enum Tok {
    Num(Num),
    Op(&'static str),
    Open,
    Close,
}

fn lex(s: &str) -> Option<Vec<Tok>> {
    let b: Vec<char> = s.chars().collect();
    let (mut i, mut out) = (0, Vec::new());
    while i < b.len() {
        let c = b[i];
        if c.is_whitespace() {
            i += 1;
        } else if c.is_ascii_digit() || (c == '.' && b.get(i + 1).is_some_and(|d| d.is_ascii_digit())) {
            let start = i;
            if c == '0' && b.get(i + 1).is_some_and(|x| "xXoObB".contains(*x)) {
                let radix = match b[i + 1].to_ascii_lowercase() {
                    'x' => 16,
                    'o' => 8,
                    _ => 2,
                };
                i += 2;
                let s0 = i;
                while i < b.len() && (b[i].is_digit(radix) || b[i] == '_') {
                    i += 1;
                }
                let digits: String = b[s0..i].iter().filter(|c| **c != '_').collect();
                out.push(Tok::Num(Num::Int(BigInt::parse_bytes(digits.as_bytes(), radix)?)));
                continue;
            }
            while i < b.len() && (b[i].is_ascii_digit() || b[i] == '_') {
                i += 1;
            }
            let mut float = false;
            if i < b.len() && b[i] == '.' {
                float = true;
                i += 1;
                while i < b.len() && (b[i].is_ascii_digit() || b[i] == '_') {
                    i += 1;
                }
            }
            if i < b.len() && (b[i] == 'e' || b[i] == 'E') {
                let mut j = i + 1;
                if j < b.len() && (b[j] == '+' || b[j] == '-') {
                    j += 1;
                }
                if j < b.len() && b[j].is_ascii_digit() {
                    float = true;
                    i = j;
                    while i < b.len() && b[i].is_ascii_digit() {
                        i += 1;
                    }
                }
            }
            let text: String = b[start..i].iter().filter(|c| **c != '_').collect();
            if i < b.len() && (b[i].is_alphanumeric() || b[i] == '_') {
                return None; // "2x", "1j": not a plain number
            }
            out.push(Tok::Num(if float { Num::Float(text.parse().ok()?) } else { Num::Int(text.parse().ok()?) }));
        } else if c == '*' && b.get(i + 1) == Some(&'*') {
            out.push(Tok::Op("**"));
            i += 2;
        } else if c == '/' && b.get(i + 1) == Some(&'/') {
            return None; // floor division: not one of the calculator's
        } else if let Some(op) = ["+", "-", "*", "/", "%"].iter().find(|o| o.starts_with(c)) {
            out.push(Tok::Op(op));
            i += 1;
        } else if c == '(' {
            out.push(Tok::Open);
            i += 1;
        } else if c == ')' {
            out.push(Tok::Close);
            i += 1;
        } else {
            return None;
        }
    }
    Some(out)
}

struct Parser {
    toks: Vec<Tok>,
    at: usize,
}

impl Parser {
    fn peek(&self) -> Option<&Tok> {
        self.toks.get(self.at)
    }

    fn op(&mut self, ops: &[&str]) -> Option<&'static str> {
        match self.peek() {
            Some(Tok::Op(o)) if ops.contains(o) => {
                let o = *o;
                self.at += 1;
                Some(o)
            }
            _ => None,
        }
    }

    // sum := term (('+'|'-') term)*
    fn sum(&mut self) -> Option<Num> {
        let mut v = self.term()?;
        while let Some(o) = self.op(&["+", "-"]) {
            let r = self.term()?;
            v = binary(o, v, r)?;
        }
        Some(v)
    }

    // term := factor (('*'|'/'|'%') factor)*
    fn term(&mut self) -> Option<Num> {
        let mut v = self.factor()?;
        while let Some(o) = self.op(&["*", "/", "%"]) {
            let r = self.factor()?;
            v = binary(o, v, r)?;
        }
        Some(v)
    }

    // factor := ('+'|'-') factor | power
    fn factor(&mut self) -> Option<Num> {
        match self.op(&["+", "-"]) {
            Some("-") => Some(match self.factor()? {
                Num::Int(i) => Num::Int(-i),
                Num::Float(f) => Num::Float(-f),
            }),
            Some(_) => self.factor(),
            None => self.power(),
        }
    }

    // power := atom ['**' factor]   (right-associative; -2**2 is -(2**2))
    fn power(&mut self) -> Option<Num> {
        let base = self.atom()?;
        if self.op(&["**"]).is_some() {
            let exp = self.factor()?;
            return binary("**", base, exp);
        }
        Some(base)
    }

    fn atom(&mut self) -> Option<Num> {
        match self.toks.get(self.at)?.clone() {
            Tok::Num(n) => {
                self.at += 1;
                Some(n)
            }
            Tok::Open => {
                self.at += 1;
                let v = self.sum()?;
                (self.peek() == Some(&Tok::Close)).then(|| self.at += 1)?;
                Some(v)
            }
            _ => None,
        }
    }
}

/// Python's float %: the result takes the divisor's sign.
fn py_fmod(a: f64, b: f64) -> f64 {
    let m = a % b;
    if m != 0.0 && (m < 0.0) != (b < 0.0) { m + b } else { m }
}

fn binary(op: &str, a: Num, b: Num) -> Option<Num> {
    use Num::*;
    Some(match (op, a, b) {
        ("+", Int(x), Int(y)) => Int(x + y),
        ("-", Int(x), Int(y)) => Int(x - y),
        ("*", Int(x), Int(y)) => Int(x * y),
        ("%", Int(x), Int(y)) => {
            if y.is_zero() {
                return None;
            }
            let m = &x % &y;
            Int(if !m.is_zero() && (m.is_negative() != y.is_negative()) { m + y } else { m })
        }
        ("**", Int(x), Int(y)) => {
            let e = y.to_i64()?;
            if e.abs() > 100 || x.bits() * e.unsigned_abs() > MAX_BITS {
                return None;
            }
            if e < 0 {
                if x.is_zero() {
                    return None;
                }
                Float(x.to_f64()?.powi(e as i32))
            } else {
                Int(num_traits::pow(x, e as usize))
            }
        }
        ("/", a, b) => {
            if b.f() == 0.0 {
                return None;
            }
            Float(a.f() / b.f())
        }
        ("**", a, b) => {
            if b.f().abs() > 100.0 {
                return None;
            }
            let (x, y) = (a.f(), b.f());
            if x == 0.0 && y < 0.0 {
                return None;
            }
            // a negative base to a fraction: NaN, refused below (Python
            // answers with a complex number there; no use in Spotlight)
            Float(x.powf(y))
        }
        (op, a, b) => {
            let (x, y) = (a.f(), b.f());
            match op {
                "+" => Float(x + y),
                "-" => Float(x - y),
                "*" => Float(x * y),
                "%" => {
                    if y == 0.0 {
                        return None;
                    }
                    Float(py_fmod(x, y))
                }
                _ => return None,
            }
        }
    })
}

/// Python's repr() of a float: the shortest form that reads back, with
/// an exponent from 1e16 up and under 1e-4.
pub fn py_float(v: f64) -> String {
    let a = v.abs();
    if a != 0.0 && !(1e-4..1e16).contains(&a) {
        let s = format!("{v:e}"); // "1e16", "1.5e-5"
        let (m, e) = s.split_once('e').unwrap_or((&s, "0"));
        let e: i32 = e.parse().unwrap_or(0);
        return format!("{m}e{}{:02}", if e < 0 { '-' } else { '+' }, e.abs());
    }
    format!("{v:?}")
}

fn grouped(i: &BigInt) -> String {
    let s = i.abs().to_string();
    let mut out = String::new();
    for (k, ch) in s.chars().enumerate() {
        if k > 0 && (s.len() - k) % 3 == 0 {
            out.push(' ');
        }
        out.push(ch);
    }
    if i.is_negative() { format!("-{out}") } else { out }
}

/// The answer to `text` as Spotlight shows it ("1 234 567", "0.5"), or
/// None when it isn't a sum to work out.
pub fn calculate(text: &str) -> Option<String> {
    let expr = text.trim().replace('×', "*").replace('÷', "/").replace('^', "**").replace(',', ".");
    let ops_after_first = expr.chars().skip(1).any(|c| "+-*/%".contains(c));
    if expr.is_empty() || !expr.chars().any(|c| c.is_ascii_digit()) || !ops_after_first {
        return None;
    }
    let mut p = Parser { toks: lex(&expr)?, at: 0 };
    let v = p.sum()?;
    if p.at != p.toks.len() {
        return None;
    }
    match v {
        Num::Int(i) => Some(grouped(&i)),
        Num::Float(f) => {
            if !f.is_finite() {
                return None;
            }
            let r = (f * 1e10).round() / 1e10; // round(v, 10)
            let r = if (r * 1e10).is_finite() { r } else { f };
            if r.fract() == 0.0 && r.abs() < 1e300 {
                return BigInt::from_f64_exact(r).map(|i| grouped(&i));
            }
            Some(py_float(r))
        }
    }
}

trait FromF64 {
    fn from_f64_exact(f: f64) -> Option<BigInt>;
}

impl FromF64 for BigInt {
    fn from_f64_exact(f: f64) -> Option<BigInt> {
        num_traits::FromPrimitive::from_f64(f)
    }
}

#[cfg(test)]
mod tests {
    use super::calculate;

    #[test]
    fn sums_as_python_works_them_out() {
        let cases = [
            ("1+1", Some("2")),
            ("2*3+4", Some("10")),
            ("-2**2", Some("-4")),
            ("2**-1", Some("0.5")),
            ("2^10", Some("1 024")),
            ("10/4", Some("2.5")),
            ("10/5", Some("2")),
            ("7%3", Some("1")),
            ("-7%3", Some("2")),
            ("7.5%-2", Some("-0.5")),
            ("0.1+0.2", Some("0.3")),
            ("3 × 4", Some("12")),
            ("9 ÷ 3", Some("3")),
            ("1,5*2", Some("3")),
            ("(1+2)*3", Some("9")),
            ("1000000*1000", Some("1 000 000 000")),
            ("1e20*10", Some("1 000 000 000 000 000 000 000")),
            ("1e300*1e10", None), // inf: no answer
            ("1/3", Some("0.3333333333")),
            ("0.00001*1", Some("1e-05")),
            ("1_000+1", Some("1 001")),
            ("0x10+1", Some("17")),
        ];
        for (q, want) in cases {
            assert_eq!(calculate(q).as_deref(), want, "{q}");
        }
    }

    #[test]
    fn not_a_sum() {
        for q in ["", "42", "-5", "abc+1", "1/0", "5%0", "2**1000000", "10**101", "1//2", "(1+2", "1+", "2x+1", "os.system('x')+1", "1 2+3", "(-8)**0.5"] {
            assert_eq!(calculate(q), None, "{q}");
        }
    }
}
