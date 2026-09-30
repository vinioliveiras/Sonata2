"""Up Next: the play queue with shuffle and repeat (off / all / one), as in
macOS Music. Pure logic, no GTK.

`items` keeps the order the songs were queued in; `order` is the playing
order (a permutation of it, shuffled or not) and `pos` the current place in
`order`. Turning shuffle on keeps the current song and shuffles the rest;
turning it off goes back to the queued order from the current song."""
import random

REPEAT_OFF, REPEAT_ALL, REPEAT_ONE = "off", "all", "one"
REPEAT_CYCLE = {REPEAT_OFF: REPEAT_ALL, REPEAT_ALL: REPEAT_ONE, REPEAT_ONE: REPEAT_OFF}


class Queue:
    def __init__(self, rng: random.Random = None):
        self.items = []
        self.order = []
        self.pos = -1
        self.shuffle = False
        self.repeat = REPEAT_OFF
        self.rng = rng or random.Random()

    # state ---------------------------------------------------------------------------------
    @property
    def current(self):
        return self.items[self.order[self.pos]] if 0 <= self.pos < len(self.order) else None

    def __len__(self) -> int:
        return len(self.items)

    def upcoming(self) -> list:
        """What plays after the current song (repeat all wraps around once)."""
        rest = [self.items[i] for i in self.order[self.pos + 1:]]
        if self.repeat == REPEAT_ALL and self.pos > 0:
            rest += [self.items[i] for i in self.order[:self.pos]]
        return rest

    # building ------------------------------------------------------------------------------
    def set(self, items, start: int = 0) -> None:
        """Play `items` from items[start] (a double-click in a list)."""
        self.items = list(items)
        start = max(0, min(start, len(self.items) - 1))
        self.order = list(range(len(self.items)))
        self.pos = start if self.items else -1
        if self.shuffle and self.items:
            self._shuffle_rest(start)

    def _shuffle_rest(self, first: int) -> None:
        rest = [i for i in range(len(self.items)) if i != first]
        self.rng.shuffle(rest)
        self.order = [first] + rest
        self.pos = 0

    def play_next(self, items) -> None:
        """Insert right after the current song (opening a file, "Play Next")."""
        items = list(items)
        if not items:
            return
        base = len(self.items)
        self.items.extend(items)
        new = list(range(base, base + len(items)))
        at = self.pos + 1
        self.order[at:at] = new
        if self.pos < 0:
            self.pos = 0

    def append(self, items) -> None:
        """At the end ("Play Later")."""
        base = len(self.items)
        self.items.extend(items)
        self.order.extend(range(base, len(self.items)))
        if self.pos < 0 and self.items:
            self.pos = 0

    def clear(self) -> None:
        self.items, self.order, self.pos = [], [], -1

    def jump(self, offset: int) -> None:
        """Play the upcoming song `offset` places ahead (a click in Up Next)."""
        if self.order:
            self.pos = (self.pos + offset) % len(self.order)

    # modes ---------------------------------------------------------------------------------
    def set_shuffle(self, on: bool) -> None:
        if on == self.shuffle:
            return
        self.shuffle = on
        if not self.items:
            return
        cur = self.order[self.pos] if self.pos >= 0 else 0
        if on:
            self._shuffle_rest(cur)
        else:
            self.order = list(range(len(self.items)))
            self.pos = cur

    def cycle_repeat(self) -> str:
        self.repeat = REPEAT_CYCLE[self.repeat]
        return self.repeat

    # moving --------------------------------------------------------------------------------
    def next(self, auto: bool = False):
        """The next song, or None at the end. auto: the song ended by itself
        (repeat one plays it again; the Next button skips anyway)."""
        if not self.order:
            return None
        if auto and self.repeat == REPEAT_ONE:
            return self.current
        if self.pos + 1 < len(self.order):
            self.pos += 1
            return self.current
        if self.repeat != REPEAT_OFF:            # repeat all (or Next under repeat one): from the top
            if self.shuffle and len(self.order) > 1:
                last = self.order[self.pos]
                rest = [i for i in range(len(self.items)) if i != last]
                self.rng.shuffle(rest)
                self.order = rest + [last]
            self.pos = 0
            return self.current
        return None

    def previous(self):
        if not self.order:
            return None
        if self.pos > 0:
            self.pos -= 1
        elif self.repeat != REPEAT_OFF:
            self.pos = len(self.order) - 1
        return self.current
