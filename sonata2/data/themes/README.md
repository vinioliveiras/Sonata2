# Sonata window themes (GTK)

| Theme | What | License |
|---|---|---|
| `Sonata-Light/`, `Sonata-Dark/` | [WhiteSur-gtk-theme](https://github.com/vinceliuice/WhiteSur-gtk-theme) (Big Sur look, same author as MacTahoe) prebuilt `release/WhiteSur-{Light,Dark}.tar.xz` at commit `d578265`; only gtk-2.0/3.0/4.0 kept | MIT (`LICENSE-WhiteSur`) |

Used only inside the Sonata session: `tools/session-env.sh` puts
`sonata2/data` first in `XDG_DATA_DIRS` and sets `GTK_THEME=Sonata-Light`
(or `-Dark`), so GTK3/GTK4/libadwaita apps get Big Sur title bars and
controls without touching the theme of other desktops.

Updating: take the new release tarballs from WhiteSur, replace the gtk-*
folders, keep these index.theme files, update the commit above.
