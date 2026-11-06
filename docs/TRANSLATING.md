# Translating Sonata

Sonata shows its own words in the system's language: Portuguese (Brazil),
Portuguese (Portugal), and English for everything else. These notes are for
whoever adds or changes text next. That includes Claude: read this file
before touching any string the user sees.

## How it works

- **Which language is used.** The `sonata-i18n` crate
  ([rust/crates/sonata-i18n](../rust/crates/sonata-i18n/src/lib.rs)) reads
  the session's locale once per process: `LC_ALL`, then `LC_MESSAGES`,
  `LANG` and `LANGUAGE`.
  - `pt_PT` gives Portugal's Portuguese.
  - Any other `pt` gives Brazil's.
  - Anything else gives English.

  Settings > Language & Region sets the system's language, and that takes
  effect at the next login.
- **Catalogs.** Translations live in `locale/<pt_BR|pt_PT>/*.json`,
  one file per area of Sonata. The files are merged when loaded.
  - A plain string looks like `{"English": "translation"}`.
  - A plural looks like `{"singular": ["one", "other"]}`, keyed by the
    English singular.
  - A string that depends on context looks like
    `{"context\u0004English": "translation"}`.
  - A string with no translation shows in English. That is not an error at
    runtime, but the tests fail on it.
  - Installed, the catalogs are a copy in `<prefix>/share/sonata2-rs/locale`
    (install.sh); from a clone, `locale/` itself.
- **App names.** `sonata2 desktop-files` (and the Dock as it starts) gives every Sonata desktop entry
  `Name[pt_BR]=` / `Name[pt_PT]=` lines, and the same for `Comment` and
  action names, taken from the catalogs. GIO then shows the right name in the
  Dock, Apps and the menu bar.
  - The entry's English `Name=` must have a translation; it usually goes in
    `desktop.json`.
  - Web apps keep the name the user gave them.

## Marking strings in code

```rust
use sonata_i18n::{fmt, n_, tr, trn, trp};

tr!("Move to Trash")                                       // shown now
fmt(&tr!("Copy {name}"), &[("name", n)])                   // placeholders: fmt() after tr!()
fmt(&trn!("{n} item", "{n} items", n), &[("n", &n.to_string())])   // plurals
const TITLES: [(&str, &str); 1] = [("dock", n_!("Dock"))]; // a table...
sonata_i18n::gettext(title)                                // ...translated where it is shown
trp!("stopwatch", "Reset")                                 // one English word, two meanings
```

Rules (`cargo test -p sonata-i18n` checks the first three):
- **Literal strings only, on one line.** `tr!(&format!("Copy {n}"))` can
  never be found in a catalog; `format!` the result of `tr!` instead (or
  `fmt`, for `{name}` placeholders).
- **`trn!` takes two literal strings**, and `trp!`/`nc_!` take a literal
  context.
- **Every marked string is translated in both catalogs**, with the same
  `{placeholders}` and the same number of `%` signs.
- **Translate whole sentences.** Never glue pieces together like
  `_("Open") + " " + name`; word order differs between languages. Use one
  template with a placeholder.
- **Leave alone what isn't words:** config keys, D-Bus names, desktop ids,
  commands, log lines, CSS classes, and window titles that only the
  compositor sees (Dock, OSD, Menu Bar surfaces).
- **Window titles a person sees** go through `tr!()`. For example
  `.title(tr!("Calendar"))`.
- **Don't translate brands:** Wi-Fi, Bluetooth, Dock, Sonata, VPN, USB,
  Claude, TextEdit, Feedbacker, app names from other projects.
- **`glib::set_application_name(...)` stays English.** Recent documents
  match on it.

## Adding or changing text: the routine

1. Mark the string in code, following the rules above.
2. Add it to the area's catalog in **both** `pt_BR` and `pt_PT`.
3. Run `python3 tools/i18n_missing.py rust/apps/<area>/` (or
   `rust/crates/<crate>/`). It lists missing translations and placeholder
   mismatches, for that area only.
4. From `rust/`, isolated (CLAUDE.md): `cargo test -p sonata-i18n` (every
   marked string) and `cargo test -p sonata2-rs desktop` (the apps' names).
5. When you change an English string, its old catalog entries no longer
   match: rename the key in both catalogs.
6. Every English string has **one** translation per language across all
   catalog files. `catalogs_are_json_objects_and_agree_with_themselves`
   fails when two files disagree; a second translation would otherwise win at random.

## Length: don't break the layout

Portuguese is often 20–40% longer than English. Before adding a long
translation, find where the string is shown:

- **Menus, alerts, settings rows, lists.** These grow or wrap, so length is
  fine.
- **Fixed-width places.** These cut text with "…", so keep it short.
  - Control Center tiles.
  - Menu bar panels with a set width (`sonata_ui::panel`).
  - Dock labels, segmented buttons, toolbar buttons.
  - Settings combo rows: the selected value shares the row with the title.
    "Full Screen or Apps Menu" was cut to "Ful…" until the subtitle was
    shortened.
  - Small windows (About, Copy).
- **When unsure, look at it.** Open the component in both languages, for
  example `LANGUAGE=pt_PT sonata2 settings` or a component's `--preview`
  window. Shorten the translation, or the English subtitle, rather than let
  it clip.
- **Small windows must show all of their content without a scroll bar**,
  with the longest language too. About This Computer grows to its content
  (it sizes itself to its content).

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
| Dock | o Dock | a Dock (feminine) |
| Keep in Dock | Manter no Dock | Manter na Dock |
| Open at Login | Abrir ao Iniciar Sessão | Abrir no início de sessão |
| Language & Region | Idioma e Região | Idioma e região |
| Preview (the app) | Pré-Visualização | Pré-visualização |

Portugal's spelling: "controlo", "ecrã", "cronómetro", "câmara" (not
"controle", "tela", "cronômetro", "câmera"). Brazil's: "controle", "tela",
"cronômetro", "câmera".

## Adding a language

1. Add its code to `SUPPORTED` in `rust/crates/sonata-i18n/src/lib.rs` and
   teach `detect()` its locale names.
2. Create `locale/<code>/` with the same files as `pt_BR`.
3. If its plural rule differs from English's, add it to `ngettext()`.
4. Add it to Settings' languages and regions (`sonata-system`'s locales.rs).
5. Run the routine above. The tests check every language in `SUPPORTED`.
