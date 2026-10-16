"""What Sonata calls its parts on screen. Only the words people see: code,
settings keys, commands (`sonata2 launchpad`), D-Bus actions and .desktop
ids keep their technical names, so nothing depending on them changes.

Sonata has its own names (not affiliated with Apple: see README, Design and
trademarks); a future theme may bring its own words here.

They stay English here (a desktop entry's Name, a window title matched);
shown, they're translated: _(names.APPS)."""
from .i18n import N_

APPS = N_("Apps")                  # the full-screen app grid (code: launchpad)
APPS_MENU = N_("Apps Menu")        # the same apps in a panel (code: launchpad_window)
SEARCH = N_("Search")              # search anything (code: spotlight)
OVERVIEW = N_("Overview")          # every window at once (code: mission / Wayfire scale)
THIS_COMPUTER = N_("This Computer")  # Files' search scope: everywhere
