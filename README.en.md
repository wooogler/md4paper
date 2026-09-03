# md4paper

*English · [한국어](README.md)*

A local tool that turns a research-paper PDF into **(1) Markdown with a properly ordered heading
structure** and **(2) a Korean translation in Markdown**. The browser-based web UI is the main way
to use it (the server runs only on your own machine), and a CLI is included for automation.

![Convert screen — section tree on the left, Markdown preview on the right](docs/images/02-convert.png)

- PDF → Markdown extraction runs **on your own machine** via [Docling](https://github.com/docling-project/docling) (MIT).
- You fix heading levels yourself in the section tree, checking against the original PDF side by side.
- Reference parsing, citation links, glossary building, and Korean translation use an **LLM API**
  (optional features, key required).

> **Two things to know up front.** The translation target language is **Korean only** — that is
> what the prompts, the style options, and the glossary are built around. And **the web UI is in
> Korean**, so an English-only reader will find the buttons hard to follow even though everything
> under the hood is language-neutral. The extraction and structure-editing half of the tool works
> for any English-language paper regardless.

## What's different — you can step in

The difference from throwing a whole PDF at an LLM and saying "translate this" is that **there are
places, part way through the pipeline, where a human can correct what the automation did.**

- **You fix the section structure yourself.** When an auto-detected heading level is wrong, change
  it in the tree, and adjust every heading in the same numbering scheme at once. Drop sections you
  don't want; add headings that were missed. The preview updates immediately.
- **It remembers your corrections.** A fix like "Acknowledgments is an h2" is stored per heading
  name and applied automatically to the next paper in the same venue's format.
- **The glossary is settled before translation.** You edit the LLM's candidate terms in a table
  first, then translate with that glossary, so terminology doesn't drift across the document.
- **You choose how it translates.** Sentence-ending register (three Korean styles), whether to
  leave headings in English, and which sections to translate at all — section by section.
- **You review against the original.** Click a section title to jump to that PDF page, check the
  translation in an EN | KO side-by-side view, and hand-edit the Markdown.
- **Anything with a fixed rule is not left to the LLM.** Number-based heading re-leveling,
  matching figure and table captions, tidying image names, protecting math/code/links, and
  stripping running headers, copyright lines, and **the line numbers in the margin of submission
  drafts** are all handled deterministically in code. The LLM is used only for translation, the
  glossary, and reference parsing — which is why results are reproducible and costs are predictable.

---

## ⚠️ Please read this first — this project was "vibe coded"

**Most of the code in this repository was written by an LLM (Claude Code)** and has not been
reviewed line by line by a human. It is a personal research tool published as-is. Use it with the
following in mind.

- **AS-IS, no warranty.** It can break at any time and can silently produce wrong output. Do not
  put it in a production or business-critical pipeline.
- **Extraction can drop content.** Tables, math, two-column layouts, and footnotes are especially
  fragile. Always **check the output against the original PDF** (the web UI has a side-by-side view).
- **Translation is done by an LLM — expect mistranslations, omissions, and hallucinations.** If a
  number, an equation, an experimental result, or a citation number changes, the tool may not
  notice. A human has to verify before you quote or distribute the text.
- **Your paper's body text is sent to an external LLM API.** Running translation, glossary
  building, or reference parsing sends that text to OpenAI/Anthropic/Google servers. **Do not use
  it on unpublished manuscripts, confidential documents, or copyrighted material you may not
  share.** If you only use extraction (PDF → Markdown), no text leaves your machine.
- **API charges are real.** Typically $0.03–0.14 per paper (→ [Cost](#cost--what-does-one-paper-run-you)),
  and the cost is entirely yours. Extraction alone costs nothing.
- **API keys are stored in plaintext** (`~/.config/md4paper/config.toml`, mode 0600 on POSIX). On a
  shared computer, use environment variables instead of the config file.
- **Bug reports are welcome, but no support is promised.** Issues and PRs get looked at when there
  is time.

---

## Requirements

| Item | Details |
|---|---|
| OS | Windows 10/11, macOS (Apple Silicon · Intel), Linux |
| Python | 3.11+ — **you don't have to install it yourself** (uv fetches it) |
| Disk | ~**1.3GB** of dependencies (measured on macOS) + a ~**1.1GB** Docling model download on first conversion (larger on Linux, where torch pulls CUDA builds) |
| Memory | 4GB+ recommended (no GPU required; it runs on CPU) |
| Network | Needed to install and to download the model once. Extraction works offline after that |
| LLM key | Only for translation/citations/glossary (optional). One of OpenAI · Anthropic · Google Gemini |
| App window | Optional — a standalone window opened from an icon (pywebview). macOS and Windows use the OS webview as-is; Linux needs WebKit2GTK (without it, it opens in a browser) |

---

## Install

### One-line install — icon and all (recommended)

Paste this into a terminal and it handles uv, Python, md4paper, and the app icon in one go.

**macOS / Linux**:
```bash
curl -LsSf https://raw.githubusercontent.com/wooogler/md4paper/main/install.sh | sh
```

**Windows** (PowerShell):
```powershell
irm https://raw.githubusercontent.com/wooogler/md4paper/main/install.ps1 | iex
```

When it finishes you get an **md4paper icon** in Launchpad / the Start menu. Click it to open
(→ [Open it as an app](#open-it-as-an-app--register-an-icon-optional)). Running the same line again
updates to the latest version.

- **What it does** — installs uv (a package manager that also manages Python) if you don't have it,
  installs md4paper with `uv tool install`, and registers the icon with `md4paper app`. The scripts
  are right here in the repo — [install.sh](install.sh) · [install.ps1](install.ps1) — so read them
  before you run them (piping an unknown script into a shell is something to be careful about in
  general).
- It downloads about 1.3GB of dependencies, so the first run takes a few minutes.
- **Why a script and not an installer (.dmg/.exe)** — the app is **assembled on your machine**, so
  no code signing or notarization is needed, and you don't get macOS's "unidentified developer"
  warning or the Windows SmartScreen prompt. You also don't download a 1.5GB installer.
- Uninstall: `md4paper app --remove && uv tool uninstall md4paper`

### Manual install — if you already use uv, or want to change the code

Install the [uv](https://docs.astral.sh/uv/) package manager and uv takes care of Python too.

#### Step 1 — install uv

**Windows** (PowerShell):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
You have to **close the PowerShell window and open a new one** for the `uv` command to be found.

**macOS / Linux**:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
(Homebrew works on macOS too: `brew install uv`)

Check it: `uv --version`

#### Step 2 — install md4paper and start the web UI

##### Option A: clone it (recommended for development)

**Windows** (PowerShell):
```powershell
git clone https://github.com/wooogler/md4paper.git
cd md4paper
uv sync --extra ui
uv run md4paper ui
```

**macOS / Linux**:
```bash
git clone https://github.com/wooogler/md4paper.git
cd md4paper
uv sync --extra ui
uv run md4paper ui
```

`--extra ui` pulls in the web UI (NiceGUI). The first dependency download takes a few minutes.
To check the install, run `uv run md4paper doctor`.

##### Option B: run it without cloning

**Windows** (PowerShell):
```powershell
uvx --from "md4paper[ui] @ git+https://github.com/wooogler/md4paper" md4paper ui
```

**macOS / Linux**:
```bash
uvx --from 'md4paper[ui] @ git+https://github.com/wooogler/md4paper' md4paper ui
```

The first run fetches dependencies, so it takes a few minutes. With this method the default work
folder is `~/md4paper/output` (`C:\Users\<user>\md4paper\output` on Windows).

---

## Open it as an app — register an icon (optional)

If you used the [one-line install](#one-line-install--icon-and-all-recommended), the icon is
**already registered**. If you cloned the repo, run two commands in it:

```bash
uv sync --extra ui --extra native   # add the app-window dependency (pywebview)
uv run md4paper app                 # register the icon
```

| OS | What gets created | Where it opens from |
|---|---|---|
| macOS | `~/Applications/md4paper.app` | Launchpad · Spotlight · Finder · Dock |
| Windows | `md4paper` in the Start menu (add `--desktop` for a desktop shortcut too) | Start menu |
| Linux | `~/.local/share/applications/md4paper.desktop` | Application menu |

Clicking the icon opens a **standalone app window** rather than a browser tab — the screens and
features are identical to the web UI. The server still binds to 127.0.0.1 only, and closing the
window shuts the server down. To get the same window from a terminal, run
`uv run md4paper ui --native` (or plain `md4paper ui` to open it in a browser).

- **Downloading zips works in the app window too** — instead of a browser download you get the
  **OS save dialog** and pick the location.
- **Environment variables are not inherited.** Launched from the icon, the app does not see a
  `OPENAI_API_KEY` you `export`ed in your shell. Save the key in **AI settings** on the left of the
  home screen instead (`~/.config/md4paper/config.toml`).
- **If nothing happens**, check the log — on macOS, `~/Library/Logs/md4paper.log`. If launching
  fails, the app tells you in a dialog.
- **If you moved the repo or rebuilt the virtualenv**, run `md4paper app` again. The launcher has
  the interpreter path baked in. (With the one-line install the path stays the same across updates,
  so you don't need to.)
- **In environments that can't open an app window** (Linux without WebKit2GTK or Qt, for example),
  the icon **falls back to opening a browser** rather than doing nothing. The reason is written to
  the launch log.
- Uninstall: `md4paper app --remove`

> `uv sync --extra X` removes extras you don't list. For the app window, **always pass both**:
> `uv sync --extra ui --extra native`.

---

## Using the web UI

Running `md4paper ui` prints `http://127.0.0.1:8080` in the terminal and opens a browser.
(To use an app window instead → [Open it as an app](#open-it-as-an-app--register-an-icon-optional).)
**The server binds to 127.0.0.1 (your machine) only, so it is not reachable from outside.** If the
port is taken, it picks a free one and prints the actual address. (`--port 9000` to choose one,
`--no-show` to skip opening the browser, `md4paper ui <name>.md4/` to open an existing job directly.)

### 1. Home — upload, and find things again

![Home screen — PDF dropzone and the list of converted papers](docs/images/01-home.png)

Drop a PDF and conversion starts immediately. Drop several and they're processed in order (batch).
It also accepts `.md` files that are already Markdown (skipping extraction, starting at structure
cleanup).

- Search **Converted papers** on the right by title, author, or venue, and click a card to pick up
  where you left off.
- Paste your API key into **AI settings** on the left and hit "test connection" to verify it right
  away (only needed for translation and citations).
- **Default conversion and translation settings** on the left are the defaults applied to papers
  you upload from now on.
- **Library folders** on the left sets where converted papers accumulate — see
  [Library folders](#library-folders--collecting-converted-papers-in-one-place) below.
- Check several papers in the list to **export** them as a single zip.
- The trash icon on a card offers **hide from list** (files stay, the card is hidden) or **delete
  files** (permanently removes the working directory). Hidden papers come back any time via
  **show N hidden papers** above the list. Deleting files removes the whole paper folder — original
  PDF, extraction cache, images — and also cleans up the copies in your library folders (uncheck the
  box in the dialog to keep those; the dialog shows you exactly which folder paths will go).

### 2. Convert — cleaning up the source-language Markdown

![Convert screen — adjusting heading levels in the section tree](docs/images/02-convert.png)

This is the **1 · Convert** tab at the top. Editing on the left, results on the right.

- **Auto-fix layout** — the button at the top. It has AI find and fix, across the whole document,
  the places extraction mangled (a title split into two lines as `## 2` + `## Background`, math
  unravelled into `x t`, broken paragraphs, lists, and tables). The dialog lets you add **extra
  instructions** (or leave it blank), and `Apply` walks the document section by section. The fixed
  result **rebuilds the section tree and the bulk level adjustments**, while carrying over the
  levels, translation flags, and conversion settings you've already set.
  It does not change the words of the text — each chunk is checked to confirm the original content
  survived, and any chunk that doesn't match is left untouched. If you don't like the result,
  `Revert to previous state` in the same dialog rolls the whole thing back.
- **Section tree** — detected headings in order. Change the level with the dropdown on the left
  (`Heading 1`–`6`), or pick `to body text` (demote a heading into a normal paragraph), `merge with
  above`, `delete entirely`, or `italic`. A `run-in` badge means a subheading that was inline with
  the body, as in "3.1.2 Title. Body text…"; a `title` badge marks the document title.
- **Author cleanup** — two-column PDFs jam authors, emails, and affiliations onto one line;
  conversion separates and tidies them per author (LLM labels + code reassembly if you have a key,
  rule-based otherwise). The eight authors in the screenshot above are that result.
- **Bulk level adjustment** — groups a numbering scheme like `3.2.1` by depth and levels them all
  at once.
- **Conversion settings** — citation style (numeric / author-year / short-name combinations),
  reference links, image handling, and so on.
- Toggle **Edit Markdown** at the top right to hand-edit the resulting Markdown.
- **Click a figure to blow it up full screen** — scaled down to the panel width, a chart's axis and
  legend text is unreadable. Zoom with the wheel, a trackpad pinch, or `+`/`-` (the point under the
  cursor stays put), drag to pan while zoomed, double-click or `0` to fit, `Esc` or a background
  click to close. It works on the figures in the viewer tab's original and translation panes too.
- Everything you change is **saved automatically** and reflected in the preview immediately. There
  is no "save" button.

### 3. Side by side with the PDF

![Markdown and PDF side by side](docs/images/03-pdf.png)

Click **Markdown + PDF** and the original PDF appears on the right. Clicking a title in the section
tree on the left **jumps to the PDF page** that section is on, so you can visually confirm nothing
was dropped in extraction. **PDF** alone gives you the PDF full width.

### 4. Translate — what, and in what register

![Translate screen — choosing sections to translate and editing the glossary table](docs/images/04-translate.png)

This is the **2 · Translate** tab at the top.

- **Sections to translate** — only checked sections are translated (saving cost and time).
  Unchecked ones stay in the English original. Leaving out the references is usually the natural
  choice.
- **Translation options** — sentence-ending register (three Korean styles: plain 해라체, polite
  합니다체, soft 해요체), whether to leave headings in English, and so on.
- **Glossary** — key terms are pulled automatically from the abstract, the introduction, and the
  section titles. Before translating, you can fix the Korean rendering and the handling of each term
  in the table — `translate (by meaning)` / `transliterate (by sound)` / `keep original` / `gloss the
  original on first use`. The screenshot shows actual extracted results (`self-attention → 셀프
  어텐션`, `hidden state → 은닉 상태`). Press **"Translate with this glossary"** and the whole
  document is unified on those choices.

### 5. Viewer — original and translation side by side

![Viewer screen — English original and Korean translation side by side](docs/images/05-viewer.png)

This is the **3 · Viewer** tab at the top. Navigate with the table of contents on the left and
review the original next to the translation (scrolling is synced proportionally). This is where you
confirm that a term set to `gloss the original on first use` came out as "시퀀스 변환(sequence
transduction)". The **Original / Translation / PDF** buttons turn each pane on and off, and the top
right downloads the English or Korean zip directly.

Hover over a sentence and **the sentence it pairs with on the other side is highlighted too** — so
you can follow by eye which sentence is which. Equal sentence counts are matched 1:1 in order; if
the translation split one sentence into two, length ratios match them up to 1:2 and 2:1. Sentences
that couldn't be paired are shown in grey instead of blue, so "no match found here" isn't hidden.

**Drag over the body text and a color bar appears** for highlighting, with `Add note` to attach a
note. The unit is the **sentence**, so dragging a few characters selects the whole sentence and
**marks it in the same color in both the original and the translation** — the note is shared too.
Click a highlight again and a card shows both the original and translated sentence, where you can
change the color, edit the note, or delete it; the note icon in the toolbar opens the full list and
jumps to each spot. Annotations are saved to `annotations.json` in the work folder, so they're
still there next time you open the paper, and the **Notes md** button exports the quoted original,
the translation, and your notes as a single Markdown file.

If reassembly or re-translation shifts the body text, highlights relocate themselves by matching the
phrase. The ones that genuinely can't be found aren't deleted — they stay in the list marked
`position not found`.

### 6. Ask the paper — every answer points at the paragraph it came from (LLM key required)

Questions that come up while reading go in via the **speech-bubble icon** in the viewer toolbar. Like
the PDF pane, it **takes a column on the right** rather than covering the text (the content narrows
to make room), and it answers about this one paper only. Drag its left edge to resize; the width is
remembered.

- **Click a citation number in the answer and it jumps to the paragraph behind it.** The point is to
  show you where the answer came from instead of asking you to trust it — a `to the text` button
  scrolls the viewer to that paragraph.
- **Ask in Korean and it still searches the English original.** Retrieval is BM25 (pure Python, no
  embeddings), splitting English into words and Korean into character bigrams. When the vocabulary
  doesn't line up, the LLM expands the question into synonym keywords and it searches again. The
  answer comes back **in the language you asked in**.
- **Your own highlights and notes are searched alongside the paper** — "what did I say where I marked
  that?" is answered in the same window as a question about the text. Since a note is **your writing**
  and not the paper, the answer says so explicitly and cites it separately. The **include notes**
  toggle in the header turns it off.
- **It says when it doesn't know.** It is instructed not to invent anything absent from the retrieved
  paragraphs, and citations pointing at paragraphs that were never in the prompt are stripped before
  the answer is drawn.
- The conversation is kept in `chat.json` in the work folder, so it's still there next time, and
  **the actual cost is shown on every turn** (two LLM calls per question — one to expand the search
  terms, one to answer). `Clear` in the header wipes the history.
- With no LLM key, the panel tells you why. Only this feature is unavailable; everything else works.

---

## Export — which format, and where it goes

Pick from the **Export format** dropdown at the top right (Universal / Notion / Obsidian) and press
**"Download English (zip)"** or **"Download Korean (zip)"**. From the home screen you can also bundle
several papers into one zip. Your format choice is remembered.

Unzipped, it looks like this:

```
<paper-name>-en/
├── <paper-name>.en.md    ← Markdown converted to the format you chose
└── images/               ← only the figures and tables actually referenced in the text
```

The source `en.md` / `ko.md` are always kept in universal form and **converted only at download
time**, so switching formats and downloading again loses nothing. (The CLI's `--flavor` is the
exception: it writes the files in the work folder in that format.)

### Universal — the default

Plain standard Markdown. Images are `![](images/fig-01.png)`, citations are in-document anchor links
like `[1](#ref-1)`, and footnotes are `<sup>` HTML.

- **Where it's used** — reading in a GitHub/GitLab repo; Markdown editors like VS Code, Typora,
  Zettlr; converting to PDF/DOCX/HTML with Pandoc or Quarto; static sites like MkDocs, Hugo, Jekyll;
  pasting a whole paper into an LLM.
- **How to use it** — unzip and open the `.md`. Note that the **`.md` and `images/` must stay in the
  same folder** for figures to show. Move the folder, not just the file.
- Citation links (`#ref-N`) work in GitHub and the VS Code preview — clicking `[1]` in the body
  jumps to the references.
- If you don't know yet where it's going, take Universal. The other two formats derive from it.

### Notion

Converts the syntax Notion's importer can't handle: in-document anchor links are dropped (Notion
breaks them into `about:blank#...`), and citations become **the DOI/arXiv URL when there is one**,
or plain text otherwise. Footnote numbers become Unicode superscripts (¹ ² ³), and image alt text is
emptied (Notion shows alt as a caption, which duplicates the separate caption block).

- **How to import** — in Notion, bottom left, **Import → Markdown & CSV**, and select the
  **zip file without unzipping it**. The figures come along.
- **Known artifact** — the import leaves one empty page named `images`. Delete it. (This structure
  gives the most reliable body import, so it was left alone.)

### Obsidian

Converts to Obsidian syntax: images become wiki embeds `![[<paper-folder>/images/fig-01.png]]`,
citations become block links `[1](#^ref-1)`, and reference lines get a block id `^ref-1` at the end.
So **clicking a citation in the body jumps to that reference, and `Cmd/Ctrl + ←` returns you to
where you were reading.** Footnotes work the same way.

- **How to install** — unzip and copy the `<paper-name>-en` **folder as a whole** into your vault.
- **Careful** — the folder name is baked into the embed paths, so **renaming the folder breaks the
  figures.** The paper name is in the folder name so that images don't collide when you put many
  papers in one vault.

## Library folders — collecting converted papers in one place

Instead of downloading and unzipping every time, you can have **Markdown land automatically in a
folder you choose when conversion and translation finish**. Expand **Library folders** on the left
of the home screen and click the **folder icon** to open the OS folder picker (or just paste a path).

**English Markdown, Korean Markdown, and the original PDF can go to three different folders** — for
example `Papers/EN`, `Papers/KO`, and `Papers/PDF` in an Obsidian vault. You can set only the ones
you need, and if English and Korean go to the same folder, `.en` / `.ko` is appended to the filenames
to keep them apart.

```
Papers/EN/2017_Attention_Vaswani.md          ← one file per paper
Papers/EN/images/2017_Attention_Vaswani/     ← figures isolated in a per-paper folder
Papers/KO/2017_Attention_Vaswani.md          ← the translation, same name, different folder
Papers/PDF/2017_Attention_Vaswani.pdf        ← the original PDF under the same name → easy to find from the md
```

- The format follows your **Export format** setting (Universal / Notion / Obsidian).
- Exporting the same paper again **overwrites** — this collects papers, not versions.
- Auto-save happens ① when conversion finishes ② when translation finishes ③ when you change the
  structure or settings and it reassembles. With auto-save off, export by hand with **Save to
  folder** on the paper screen or **export already-converted papers now** on the home screen (you
  can check several in the list and send them at once).

### Filename rules

The paper folder, the PDF in the work folder, and the md/PDF in your library folders **all use the
same base name**. The base name is built from the bibliographic info (extracted by the LLM) using a
**naming rule** — the default is `{year}_{title}_{author}` (e.g. `2017_AttentionIsAllYouNeed_Vaswani`)
and you can change it in the **Library folders** panel.

- Pieces: `{year}` year · `{title}` short title (CamelCase) · `{author}` first author's surname ·
  `{venue}` venue. Missing pieces (unknown year, etc.) are dropped automatically.
- Even if you uploaded a file named `2412.01234v2.pdf`, the folder and PDF are renamed to the rule
  when conversion finishes.
- The **Clean up existing paper and PDF names** button applies the current rule to every paper you
  have already converted — renaming folders, PDFs, and library copies, and cleaning up copies
  exported under old names. (Papers with no bibliographic info are skipped, since no name can be
  built.)

The same settings from the terminal:

```bash
uv run md4paper library                                   # show current library folders
uv run md4paper library --en ~/Papers/EN --ko ~/Papers/KO --pdf ~/Papers/PDF
uv run md4paper library --export                          # export every converted paper
uv run md4paper library --off all                         # turn it off
uv run md4paper naming                                    # show the naming rule
uv run md4paper naming "{author}{year}_{title}"           # change the rule
uv run md4paper naming --apply                            # clean up existing paper and PDF names
```

### Bibliographic enrichment (optional)

Papers with a missing year or venue can be filled in from **public bibliographic APIs**: the
**Enrich bibliography** button in the library panel, or `md4paper enrich --all`.

- [OpenAlex](https://openalex.org) first; if only arXiv matches, [Crossref](https://www.crossref.org)
  fills in the published venue. Both are free and need no key.
- **Only the paper title is sent** (the body text does not leave your machine). Values read from the
  PDF are **not overwritten** — only empty fields are filled.
- A result is accepted only when its title matches ours closely enough. Crossref returns its top hit
  regardless of similarity (observed: a query for a 2026 paper returned a 2010 book), so without this
  check a wrong year would slip in silently.
- Put an email in `[enrich].mailto` to use the APIs' polite pool:
  `md4paper enrich --all --mailto you@example.com`


---

## LLM API keys (only needed for translation and citation features)

The simplest way is to paste the key into the **AI settings** panel on the web UI home screen and
press **test connection**. From a terminal:

```bash
uv run md4paper keys set openai      # saved to ~/.config/md4paper/config.toml (mode 0600)
uv run md4paper keys list            # check what's configured (values masked)
```

Or environment variables — recommended on a shared computer:

| Provider | Environment variable | Default model |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | `gpt-5.6-luna` |
| Anthropic | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` |
| Google Gemini | `GEMINI_API_KEY` | `gemini-3.5-flash-lite` |

The default models are the cheapest tier of each vendor's latest generation. If your account doesn't
have one of them, pick a different provider in the UI or pass `--model <name>` on the CLI (older
models still work fine).

## Cost — what does one paper run you?

**PDF → Markdown extraction costs $0.** Extraction is entirely local. The only things that cost
money are reference parsing, glossary generation, translation, and questions to the viewer chatbot.

**Measured**: *Attention Is All You Need* (15 pages, 49k characters of body text), the paper used
for this README's screenshots, with **automatic glossary generation + full translation** on the
default model `gpt-5.6-luna`, cost — as reported by the tool itself — **$0.031** (15 chunks). Cost
is close to linear in body length, so:

| Paper | Default `gpt-5.6-luna` |
|---|---|
| Short paper (~50k chars of body) — **measured** | **$0.031** |
| Typical conference paper (~70k chars) | ~$0.04 |
| Long paper with appendices (~240k chars) | ~$0.14 |

Add reference parsing (citation links) on top and it's another $0.01–0.02 per paper.

**The viewer chatbot** sends eight retrieved paragraphs and a few recent turns per question, not the
whole paper, so it is far smaller than a translation — the exact figure is printed with each answer,
so watch that for the first few.

> An OpenAI price cut on 2026-07-30 took `gpt-5.6-luna` from $1/$6 to $0.2/$1.2 (per 1M tokens),
> making the figures above one fifth of what they were. The measured row applies the new prices to
> the same measured token usage. That cut made the default model the cheapest across all three
> providers — switching to Gemini used to be much cheaper, but `gemini-3.5-flash-lite` ($0.3/$2.5)
> is now more expensive. Choose a provider on translation-quality preference now, not cost.

Guards against runaway spending:

- **The actual cost is printed when a run finishes** — `cost ≈ $0.0306 → paper.ko.md`
- **Translation cache** — chunks whose content hasn't changed are not re-translated when you fix the
  structure and run again.
- **Scope selection** — translate only the sections you need.
- **The chatbot prints its cost on every turn** — a long conversation stays visible as it adds up.

> The measured figures are computed from the price table in [base.py](src/md4paper/llm/base.py) as of
> 2026-07. Vendor pricing changes, so run a short paper first and check the actual cost it prints.

## Where files live

| Item | Path |
|---|---|
| Work folder (default) | run from the repo: `<repo>/output/` · run via `uvx`: `~/md4paper/output/` |
| Per-paper results | `<workfolder>/<name>/<name>.md4/` (extracted source, structure, translation, logs) |
| Final Markdown | `<name>.md4/out/paper.en.md`, `paper.ko.md`, `out/images/` |
| Library folders (optional) | a folder you choose, holding `<paper-name>.md` + `images/<paper-name>/` + `<paper-name>.pdf` — English, Korean, and PDF separately |
| Settings and API keys | `~/.config/md4paper/config.toml` (Windows: `C:\Users\<user>\.config\md4paper\config.toml`) |
| Remembered heading decisions | `~/.config/md4paper/heading_prefs.json` |
| Docling model cache | `~/.cache/huggingface` (Windows: `C:\Users\<user>\.cache\huggingface`) |
| App launcher (optional) | macOS `~/Applications/md4paper.app` · Windows Start menu · Linux `~/.local/share/applications` |
| App launch log | macOS `~/Library/Logs/md4paper.log` (when launched from the icon) |

To change the work folder: **Library folders → Work folder → Change** on the home screen,
`uv run md4paper workspace <path>`, or `md4paper ui --upload-dir <path>`.

The default **depends on how you launch it**, as the table shows, but the value at the time you first
start the UI is written to the config file. So if you start with a clone and later switch to the icon
(one-line install), it **keeps looking at the same folder** — papers you converted don't vanish from
the list. Where the resulting Markdown accumulates is set separately, under
[Library folders](#library-folders--collecting-converted-papers-in-one-place).

---

## CLI (optional — for automation and batch runs)

Most of what the web UI does is available in a terminal, which is handy for scripting many papers or
running on a server.

```bash
uv run md4paper doctor                  # check the environment
uv run md4paper convert paper.pdf       # PDF → paper.md4/out/paper.en.md
uv run md4paper review paper.md4/       # open the section manifest in $EDITOR, then reassemble
uv run md4paper cite paper.md4/         # parse references + link in-text citations (LLM)
uv run md4paper glossary paper.md4/     # build the glossary before translating (LLM)
uv run md4paper translate paper.md4/    # translate to Korean → paper.md4/out/paper.ko.md (LLM)
uv run md4paper ui paper.md4/           # open this job in the web UI
uv run md4paper workspace               # show / change the work folder
uv run md4paper library                 # show / change library folders (English, Korean, PDF)
uv run md4paper naming                  # show / change the naming rule; --apply to clean up existing names
uv run md4paper enrich --all            # fill empty years and venues from public bibliographic APIs
uv run md4paper prefs list              # list remembered heading decisions
uv run md4paper app                     # register the double-clickable app icon (--remove to undo)
```

Main options:

- `convert --ocr` — OCR for scanned PDFs (unnecessary for born-digital papers, and slow)
- `convert --flavor standard|obsidian|notion` — export format (see [Export](#export--which-format-and-where-it-goes) above)
- `translate --style 합니다체` — set the Korean register
- `cite --style keep|authoryear|short` — citation style
- `review` — opens the manifest in `$EDITOR` (Notepad on Windows if unset)

If you installed with `uvx`, drop the `uv run` — just `md4paper convert paper.pdf`.

## Troubleshooting

**General**

- Run `md4paper doctor` first. If every required item is ✓, conversion will work. An LLM key showing
  as `-` is not a failure — it means "optional feature not configured".
- Converted papers missing from the list → the files weren't deleted; **the work folder is pointing
  somewhere else**. When the list is empty, the home screen shows the folder path it's looking at, so
  switch back to your old folder under **Library folders → Work folder** (`md4paper workspace <path>`
  does the same).
- Clicked the app icon and no window appeared → check the launch log (macOS:
  `~/Library/Logs/md4paper.log`). Usually you skipped `uv sync --extra ui --extra native`, or you
  moved the repo and didn't re-run `md4paper app`.
- No AI key visible in the app window → icon launches don't inherit shell environment variables. Save
  the key under **AI settings** on the home screen.
- First conversion is slow → it's downloading the Docling model (~1.1GB). It's fast from the second
  time on.
- Output looks wrong → fix the heading levels in the section tree and compare against the PDF in the
  side-by-side view. A scanned PDF needs OCR.

**Windows**

- `uv: command not found` → open a new PowerShell, or log out and back in.
- If you don't have `git`, install it from [git-scm.com](https://git-scm.com/download/win), or use
  **Option B**, which needs no clone.
- Execution policy error (`running scripts is disabled`) → check you didn't drop the
  `-ExecutionPolicy ByPass` part of the uv install command.
- If install fails in a folder with a very long path, try a short one (e.g. `C:\dev\md4paper`).

**macOS**

- Both Apple Silicon and Intel run on CPU (no GPU needed).
- If the browser doesn't open by itself, go to the `http://127.0.0.1:8080` printed in the terminal.

**Linux**

- `ImportError: libGL.so.1: cannot open shared object file` → an OpenCV runtime dependency:
  ```bash
  sudo apt install -y libgl1 libglib2.0-0     # Debian/Ubuntu
  ```
- On a headless server, run with `--no-show` and connect over an SSH port forward:
  ```bash
  md4paper ui --no-show --port 8080
  ssh -L 8080:127.0.0.1:8080 <user>@<server>   # from your local machine
  ```
  The server binds to 127.0.0.1 only, so **don't expose it to the internet without authentication**
  (it was not built for that).
- Your distro's default torch may be a CUDA build, which makes the install large. It works without a
  GPU.

## Development

```bash
uv sync --extra ui
uv run pytest -q          # tests (all of them run against a fake provider, with no LLM calls)
uv run ruff check .       # lint
```

The design document and milestones are in [PLAN.md](PLAN.md) (Korean).
`uv sync --extra X` removes the extras you don't list, so pass them all at once if you need several.

## License

[MIT](LICENSE). **AS-IS, no warranty** — see the warning section above.

The dependencies are all permissive too — Docling, pydantic, NiceGUI (MIT); pypdfium2, click, httpx
(BSD); PyTorch, OpenCV, the LLM SDKs (Apache-2.0); Pillow (HPND). **There are no copyleft (GPL/AGPL)
dependencies.** (PyMuPDF (AGPL), previously used to render PDF pages, was replaced by pypdfium2,
which does the same job.)

Copyright in the **output** of conversion and translation belongs to the copyright holder of the
original paper. Whether you may redistribute it is yours to check.
