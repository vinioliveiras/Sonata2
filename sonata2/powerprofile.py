"""The energy mode you chose comes back when the power adapter does (Vini:
unplugged, the laptop went to Low Power -- right -- but plugged in again it
stayed there instead of going back to Automatic, his choice).

    powerprofile.remember("balanced")        # chosen in the menu bar or Settings
    f = powerprofile.Follow(); f.changed()   # on every power event (topbar)

Another tool (power-profiles-daemon, asusd, TLP...) may lower it on
battery; on the adapter again, the chosen one is set back -- unless a
full-screen game raised it meanwhile (gamemode.PowerBoost)."""
from . import config
from .backend import system

NAME = "power"
DEFAULTS = {"profile": "balanced"}


def chosen() -> str:
    p = config.load(NAME, DEFAULTS).get("profile")
    return p if p in dict(system.POWER_PROFILES) else "balanced"


def remember(profile: str) -> None:
    if profile in dict(system.POWER_PROFILES):
        config.update(NAME, profile=profile)


class Follow:
    def __init__(self, on_ac=system.on_ac, current=system.power_profile_fast, apply=system.set_power_profile,
                 boosted=None):
        self.on_ac, self.current, self.apply = on_ac, current, apply
        if boosted is None:
            from . import gamemode
            boosted = gamemode.boosted
        self.boosted = boosted
        self.ac = on_ac()

    def changed(self) -> bool:
        """A power event: True if the chosen mode was set back."""
        ac, was = self.on_ac(), self.ac
        self.ac = ac
        if not ac or was:
            return False                      # only the moment it's plugged in again
        cur, want = self.current(), chosen()
        if not cur or cur == want or self.boosted():
            return False
        return bool(self.apply(want))
