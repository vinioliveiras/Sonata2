"""The Calculator's arithmetic (macOS Calculator, Basic): decimal numbers,
× and ÷ before + and −, repeated =, % and ±. No GTK: tested on its own.

    c = Engine()
    for k in "12+3×4=": c.press(k)
    c.display()          # "24"
Keys: digits, ".", "+", "−", "×", "÷", "=", "%", "±", "C" (clear entry,
then all), "AC", "⌫" (last digit)."""
from decimal import Decimal, localcontext

# ÷0, invalid operations and results beyond Decimal's exponent range (Overflow) show Error
MATH_ERRORS = ArithmeticError

OPS = {"+": 1, "−": 1, "×": 2, "÷": 2}
MAX_DIGITS = 15                  # digits you can type
SHOWN_DIGITS = 12                # significant digits a result shows


class Engine:
    def __init__(self):
        self.all_clear()

    # -- state -------------------------------------------------------------------------
    def all_clear(self):
        self.tokens = []             # [Decimal, op, Decimal, op, ...] waiting for =
        self.entry = None            # the number being typed (text), None: showing a result
        self.value = Decimal(0)      # what is shown when not typing
        self.last = None             # (op, operand) for repeated =
        self.error = False

    @property
    def clear_label(self) -> str:
        """macOS: "C" while there is an entry to clear, "AC" otherwise."""
        return "C" if (self.entry not in (None, "0") or self.error) else "AC"

    def current(self) -> Decimal:
        return Decimal(self.entry) if self.entry not in (None, "", "-") else self.value

    # -- keys ------------------------------------------------------------------------------
    def press(self, key: str) -> None:
        if self.error and key not in ("C", "AC"):
            self.all_clear()
        if key.isdigit():
            self._digit(key)
        elif key == ".":
            self._point()
        elif key in OPS:
            self._operator(key)
        elif key == "=":
            self._equals()
        elif key == "%":
            self._percent()
        elif key == "±":
            self._negate()
        elif key == "⌫":
            self._backspace()
        elif key == "C":
            if self.entry not in (None, "0") or self.error:
                self.entry, self.error = "0", False
            else:
                self.all_clear()
        elif key == "AC":
            self.all_clear()

    def _digit(self, d):
        e = self.entry
        if e is None or e in ("0", "-0"):
            self.entry = (e[:-1] if e == "-0" else "") + d
            return
        if sum(ch.isdigit() for ch in e) < MAX_DIGITS:
            self.entry = e + d

    def _point(self):
        if self.entry is None:
            self.entry = "0."
        elif "." not in self.entry:
            self.entry += "."

    def _backspace(self):
        if self.entry is None:
            return
        self.entry = self.entry[:-1]
        if self.entry in ("", "-"):
            self.entry = "0"

    def _negate(self):
        if self.entry is not None:
            self.entry = self.entry[1:] if self.entry.startswith("-") else "-" + self.entry
        else:
            self.value = -self.value

    def _percent(self):
        x = self.current()
        try:
            if len(self.tokens) >= 2 and self.tokens[-1] in ("+", "−"):
                x = self.tokens[-2] * x / 100       # 200 + 10 % -> 200 + 20 (macOS)
            else:
                x = x / 100
        except MATH_ERRORS:
            self._fail()
            return
        self.entry, self.value = None, x

    def _operator(self, op):
        if self.entry is None and self.tokens and self.tokens[-1] in OPS:
            self.tokens[-1] = op                    # changed my mind: the new operator
            return
        self.tokens += [self.current(), op]
        self.entry = None
        self.last = None
        # show what is known so far: everything for + and −, the product chain for × and ÷
        try:
            self.value = self._fold(self.tokens[:-1], OPS[op])
        except MATH_ERRORS:
            self._fail()

    def _equals(self):
        try:
            if self.tokens:
                operand = self.current()
                expr = self.tokens + [operand]
                self.last = (self.tokens[-1], operand)
                self.value = self._fold(expr, 0)
            elif self.last:                          # = again: repeat the last operation
                self.value = _apply(self.current(), *self.last)
            else:
                self.value = self.current()
        except MATH_ERRORS:
            self._fail()
            return
        self.tokens, self.entry = [], None

    def _fail(self):
        self.all_clear()
        self.error = True

    @staticmethod
    def _fold(expr, level):
        """Evaluate [n, op, n, ...] with × ÷ first; level 2 only folds the
        trailing × ÷ chain (what macOS shows after pressing × or ÷)."""
        if level == 2:
            i = len(expr) - 1
            while i >= 2 and expr[i - 1] in ("×", "÷"):
                i -= 2
            expr = expr[i:]
        terms, ops = [expr[0]], []
        for op, n in zip(expr[1::2], expr[2::2]):
            if op in ("×", "÷"):
                terms[-1] = _apply(terms[-1], op, n)
            else:
                ops.append(op)
                terms.append(n)
        total = terms[0]
        for op, n in zip(ops, terms[1:]):
            total = _apply(total, op, n)
        return total

    # -- what shows -----------------------------------------------------------------------
    def display(self) -> str:
        if self.error:
            return "Error"
        if self.entry is not None:
            return group(self.entry)
        return fmt(self.value)


def _apply(a, op, b):
    with localcontext() as ctx:
        ctx.prec = 34
        if op == "+":
            return a + b
        if op == "−":
            return a - b
        if op == "×":
            return a * b
        if b == 0:
            raise ZeroDivisionError
        return a / b


def group(text: str) -> str:
    """Thousands separators on what is being typed ("1234.5" -> "1,234.5")."""
    neg = text.startswith("-")
    whole, dot, frac = text.lstrip("-").partition(".")
    return ("-" if neg else "") + f"{int(whole or 0):,}" + dot + frac


def fmt(x: Decimal) -> str:
    """A result: up to SHOWN_DIGITS significant digits, grouped; scientific
    notation when it doesn't fit (1.2345e20)."""
    if x == 0:
        return "0"
    with localcontext() as ctx:
        ctx.prec = SHOWN_DIGITS
        r = +x
    exp = r.adjusted()
    if -8 <= exp < SHOWN_DIGITS:
        s = format(r, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return group(s)
    m = format(r.scaleb(-exp).normalize(), "f")
    return f"{m}e{exp}"
