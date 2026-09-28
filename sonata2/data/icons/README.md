# Sonata icon themes

Sonata uses its own icons, independent of the icon theme set in the system.

| Theme | What | License |
|---|---|---|
| `Sonata/` | Our overrides, looked up first. `places/scalable/user-trash(-full).svg`: round trash can from [WhiteSur-icon-theme](https://github.com/vinceliuice/WhiteSur-icon-theme) `src/places/scalable/` | GPL-3.0 |
| `Sonata-MacTahoe/` | [MacTahoe-icon-theme](https://github.com/vinceliuice/MacTahoe-icon-theme) at commit `839848b` (2026-09-10), installed with `./install.sh -n Sonata-MacTahoe` (default blue variant). Symlinks are relative. | GPL-3.0 (see its COPYING) |
| `Sonata-Cursors/` | macOS cursors from [apple_cursor](https://github.com/ful1e5/apple_cursor) v2.0.1 (`macOS` variant), trimmed to sizes 24/32/48 (x1/HiDPI). Used by Wayfire and apps via `XCURSOR_THEME`. | GPL-3.0 (LICENSE-apple_cursor) |

Lookup order in the shell: `Sonata` -> `Sonata-MacTahoe` -> `hicolor` ->
the system's icon theme (for apps neither theme has).

To override an icon, drop an SVG with the freedesktop name into the matching
`Sonata/<context>/scalable/` folder (add the folder to `Directories=` in
`Sonata/index.theme`).

Updating MacTahoe: clone it, run `./install.sh -d /tmp/out -n Sonata-MacTahoe`,
replace `Sonata-MacTahoe/`, set `Name=Sonata-MacTahoe` in its index.theme and
update the commit above.

## Fonts

`sonata2/data/fonts/`: [Inter](https://github.com/rsms/inter) 4.1 variable (SIL OFL 1.1, LICENSE-Inter.txt), the open stand-in for SF Pro. The session adds this folder through its own fontconfig file (`FONTCONFIG_FILE`), nothing is installed system-wide.
