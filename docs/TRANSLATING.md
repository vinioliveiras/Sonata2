# Translating Sonata

Sonata shows its own words in the system's language: Portuguese (Brazil),
Portuguese (Portugal), and English for everything else. These notes are for
whoever adds or changes text next. That includes Claude: read this file
before touching any string the user sees.

## How it works

- **Which language is used.** [`sonata2/i18n.py`](../sonata2/i18n.py) reads
  the session's locale once per process: `LC_ALL`, then `LC_MESSAGES`,
  `LANG` and `LANGUAGE`.
  - `pt_PT` gives Portugal's Portuguese.
  - Any other `pt` gives Brazil's.
  - Anything else gives English.

  Settings > Language & Region sets the system's language, and that takes
  effect at the next login.
- **Catalogs.** Translations live in `sonata2/locale/<pt_BR|pt_PT>/*.json`,
  one file per area of Sonata. The files are merged when loaded.
  - A plain string looks like `{"English": "translation"}`.
  - A plural looks like `{"singular": ["one", "other"]}`, keyed by the
    English singular.
  - A string that depends on context looks like
    `{"context\u0004English": "translation"}`.
  - A string with no translation shows in English. That is not an error at
    runtime, but the tests fail on it.
- **App names.** `apps.write_desktop_file` gives every Sonata desktop entry
  `Name[pt_BR]=` / `Name[pt_PT]=` lines, and the same for `Comment` and
  action names, taken from the catalogs. GIO then shows the right name in the
  Dock, Apps and the menu bar.
  - The entry's English `Name=` must have a translation; it usually goes in
    `desktop.json`.
  - Web apps keep the name the user gave them.

## Marking strings in code

```python
from ..i18n import _, ngettext, N_, pgettext, NC_

_("Move to Trash")                                  # shown now
_("Copy {name}").format(name=n)                     # placeholders: .format() after _()
ngettext("{n} item", "{n} items", n).format(n=n)    # plurals
TITLES = {"dock": N_("Dock")}                       # a table built at import...
_(TITLES[part])                                     # ...translated where it is shown
pgettext("stopwatch", "Reset")                      # one English word, two meanings
```

Rules (`tests/test_i18n.py` checks the first three):
- **No f-strings inside `_()`.** `_(f"Copy {n}")` can never be found in a
  catalog.
- **`ngettext` takes two literal strings**, and `pgettext`/`NC_` take a
  literal context.
- **Every marked string is translated in both catalogs**, with the same
  `{placeholders}` and the same number of `%` signs.
- **Translate whole sentences.** Never glue pieces together like
  `_("Open") + " " + name`; word order differs between languages. Use one
  template with a placeholder.
- **Leave alone what isn't words:** config keys, D-Bus names, desktop ids,
  commands, log lines, CSS classes, and window titles that only the
  compositor sees (Dock, OSD, Menu Bar surfaces).
- **Window titles a person sees** go through `_()`. For example
  `title=_("Calendar")`.
- **Don't translate brands:** Wi-Fi, Bluetooth, Dock, Sonata, VPN, USB,
  Claude, TextEdit, Feedbacker, app names from other projects.
- **`GLib.set_application_name(...)` stays English.** Recent documents
  match on it.

## Adding or changing text: the routine

1. Mark the string in code, following the rules above.
2. Add it to the area's catalog in **both** `pt_BR` and `pt_PT`.
3. Run `python3 tools/i18n_missing.py sonata2/<area>/`. It lists missing
   translations and placeholder mismatches, for that area only.
4. Run `python3 -m unittest tests.test_i18n tests.test_app_names_translated`.
5. When you change an English string, its old catalog entries no longer
   match: rename the key in both catalogs.
6. Every English string has **one** translation per language across all
   catalog files. `test_one_translation_per_string` fails when two files
   disagree; a second translation would otherwise win at random.

## Length: don't break the layout

Portuguese is often 20–40% longer than English. Before adding a long
translation, find where the string is shown:

- **Menus, alerts, settings rows, lists.** These grow or wrap, so length is
  fine.
- **Fixed-width places.** These cut text with "…", so keep it short.
  - Control Center tiles.
  - Menu bar panels with a set width (`ui.panel.popup(width=...)`).
  - Dock labels, segmented buttons, toolbar buttons.
  - Settings combo rows: the selected value shares the row with the title.
    "Full Screen or Apps Menu" was cut to "Ful…" until the subtitle was
    shortened.
  - Small windows (About, Copy).
- **When unsure, look at it.** Render the screen in both languages, for
  example with `GDK_BACKEND=x11 xvfb-run` and `i18n.use("pt_PT")` in a
  small script, or `tools/wl-preview.sh`. Shorten the translation, or the
  English subtitle, rather than let it clip.
- **Small windows must show all of their content without a scroll bar**,
  with the longest language too. About This Computer grows to its content
  (`AboutWindow._fit`).

## Brazil vs. Portugal

Write each one as its speakers expect.
- **Brazil** follows macOS in pt-BR: Title Case in menus and buttons
  ("Nova Pasta") and the informal "você" register ("Navegue pelos seus
  arquivos").
- **Portugal** uses sentence case ("Nova pasta") and the infinitive or
  "o seu / a sua" in descriptions ("Explorar os seus ficheiros").
- **Plurals:** Brazil treats 0 as singular ("0 item"); Portugal treats it as
  plural ("0 itens"). `ngettext` handles this.

Terms already used. Keep them consistent:

| English | pt-BR | pt-PT |
|---|---|---|
| File / Files | Arquivo / Arquivos | Ficheiro / Ficheiros |
| Folder / New Folder | Pasta / Nova Pasta | Pasta / Nova pasta |
| Trash / Move to Trash | Lixeira / Mover para a Lixeira | Lixo / Mover para o Lixo |
| Empty Trash | Esvaziar Lixeira | Esvaziar o Lixo |
| Settings (the app) | Ajustes | Definições |
| Desktop | Mesa | Secretária |
| Display / Displays | Tela / Telas | Ecrã / Ecrãs |
| Screenshot | Captura de Tela | Captura de ecrã |
| Lock Screen | Bloquear Tela | Bloquear ecrã |
| Applications | Aplicativos | Aplicações |
| Apps (the grid) | Apps | Apps |
| Downloads | Downloads | Transferências |
| Mouse | Mouse | Rato |
| Password | Senha | Palavra-passe |
| Delete | Apagar | Eliminar |
| Save | Salvar | Guardar |
| Share | Compartilhar | Partilhar |
| Undo | Desfazer | Anular |
| Rename… | Renomear… | Mudar o nome… |
| Quit (an app) | Encerrar | Sair |
| Log Out | Encerrar Sessão | Terminar sessão |
| Sleep | Repouso | Pôr em pausa |
| Search | Buscar | Pesquisar |
| New Tab | Nova Aba | Novo separador |
| Wallpaper | Papel de Parede | Imagem de fundo |
| Do Not Disturb | Não Perturbe | Não incomodar |
| Control Center | Central de Controle | Centro de Controlo |
| Menu Bar | Barra de Menus | Barra de menus |
| Clipboard | Área de Transferência | Área de transferência |
| Camera | Câmera | Câmara |
| Users | Usuários | Utilizadores |
| Stopwatch / Timer | Cronômetro / Timer | Cronómetro / Temporizador |
| Reset (settings) | Redefinir | Repor |
| Reset (stopwatch, `pgettext("stopwatch", …)`) | Zerar | Repor |
| Default | Padrão | Predefinição |
| Get Info | Obter Informações | Obter informações |
| Show in Files | Mostrar em Arquivos | Mostrar nos Ficheiros |
| Keep in Dock | Manter no Dock | Manter no Dock |
| Open at Login | Abrir ao Iniciar Sessão | Abrir no início de sessão |
| Language & Region | Idioma e Região | Idioma e região |
| Preview (the app) | Pré-Visualização | Pré-visualização |

Portugal's spelling: "controlo", "ecrã", "cronómetro", "câmara" (not
"controle", "tela", "cronômetro", "câmera"). Brazil's: "controle", "tela",
"cronômetro", "câmera".

## Adding a language

1. Add its code to `SUPPORTED` in `sonata2/i18n.py` and teach `detect()` its
   locale names.
2. Create `sonata2/locale/<code>/` with the same files as `pt_BR`.
3. If its plural rule differs from English's, add it to `ngettext()`.
4. Add it to `LANGUAGES` / `REGIONS` in `sonata2/backend/locales.py`.
5. Run the routine above. The tests check every language in `SUPPORTED`.
