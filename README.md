# sourceshelf

A small local library for sources: fetch publications, pages and feeds, store them with a short metadata card (a "fiche"), optionally summarize them with a language model, and search them with BM25. No database, no server needed: fiches are Markdown files with YAML frontmatter, texts are plain files, search is plain Python.

Why: finding a source you read months ago within a minute, with a pointer to the place in the original (page or line), while new sources arrive on their own and failed fetches never go silent.

## In plain words (for non-technical readers)

sourceshelf is a personal digital bookshelf for things you read: articles, reports, web pages, notes, even recordings. You give it a link or a file and it keeps a copy, writes a small index card for it (title, where it came from, when you got it) and makes the text searchable. Later you type a few words and it shows the sources that mention them, with the page or line where they appear, so you can check the original yourself.

What it does for you:

- **Finds again:** search by words in the text, not just by title.
- **Stays current:** list sources (for example a research feed) and it fetches new items on a schedule.
- **Is honest about errors:** if a download fails, the card says so; nothing fails silently.
- **Keeps private things private:** mark a source confidential and it stays out of search results, shown snippets and the summary service.
- **Marks machine text:** automatic summaries are labelled as such. They are pointers, not proof: verify a quote against the original before you use it.

What it is not: not a cloud service, not a reference manager with citations, and not an AI that answers questions. It runs on your own computer; nothing leaves it unless you explicitly ask for a summary (`sourceshelf describe`), which sends the text to a language-model provider.

You use it from a terminal (the `sourceshelf` command) or, read-only, in a browser on your own machine (`sourceshelf serve`). The try-it commands below work on a small made-up example library.

## Where the name comes from

The project started as an internal tool called *kennisbank*, Dutch for "knowledge base". The public name is sourceshelf, and the only command is `sourceshelf` (also `python3 -m sourceshelf`). If you want a shorter name, add a shell alias.

What the old name does not mean: it is not a knowledge base in the AI or wiki sense. There is no knowledge graph, no ontology, no question answering and no generated knowledge. It is a shelf of source documents with index cards and text search; what you conclude from them stays with you.

## Install

Python 3.12 or newer.

```sh
pip install .            # installs the `sourceshelf` command
pip install '.[audio]'   # optional: local audio transcription (faster-whisper)
```

System tools (not installed by pip): `pdftotext` (package `poppler-utils`) for PDF text extraction, `rg` (package `ripgrep`) for `sourceshelf search --exact`. Without them everything else works; a command that needs a missing tool stops with a message that names the package (`sourceshelf add` of a PDF stores the file, marks the fiche `status: new` with an `error:` line, and can be retried after installing). Formats, exit codes, web API and limits: `docs/reference.md`; contributing: `CONTRIBUTING.md`; licences of components: `THIRD-PARTY.md`. Without a checkout you can also run `python3 -m sourceshelf`.

## Use

A library is a folder with `fiches/`, `data/` (texts and files, usually kept out of git), `index/` and an optional `sources.yaml`. Point `sourceshelf` at it with `--root <folder>` or the `KB_ROOT` environment variable; the default is the current directory.

```sh
export KB_ROOT=~/library
sourceshelf add https://example.org/paper.pdf     # fetch, store, index
sourceshelf add ./notes.docx                      # local file: pdf, html, docx, txt, md, vtt, srt, audio
sourceshelf search compost nitrogen --json        # BM25 search, with page or line
sourceshelf search --exact "carbon and nitrogen"  # literal search through rg
sourceshelf get <id>                              # show a fiche
sourceshelf check                                 # validate fiches, index freshness and file hashes
sourceshelf run                                   # fetch what is due from sources.yaml
sourceshelf serve                                 # read-only web UI on 127.0.0.1:8765
```

Try it on the bundled example library:

```sh
python3 -m sourceshelf --root examples/library check
python3 -m sourceshelf --root examples/library search "carbon nitrogen" --json
python3 scripts/recall.py --root examples/library --questions examples/recall.tsv
```

Machine output (summaries, extracted text) is always marked as machine output in the fiche; treat it as a pointer, and verify a quote against the source before citing it. A failed fetch is never silent: the fiche gets `status: new` and an `error:` line.

## Configuration

| Variable | Meaning |
|---|---|
| `KB_ROOT` | library folder (default: current directory) |
| `KB_GIT_NAME`, `KB_GIT_EMAIL` | identity for the commits `sourceshelf run` makes (default: your git config) |
| `KB_MODEL` | model for `sourceshelf describe` (default `anthropic/claude-haiku-5.5`) |
| `KB_SUMMARY_LANG` | summary language (default English) |
| `KB_ENDPOINT` | chat-completions endpoint (default OpenRouter) |
| `OPENROUTER_API_KEY` | API key for `sourceshelf describe`; read from the environment only |

## Summaries without a third party

`sourceshelf describe` talks to any chat-completions endpoint. To keep the text on your own machine, point it at a local server that speaks that protocol (for example Ollama or llama.cpp):

```sh
export KB_ENDPOINT=http://127.0.0.1:11434/v1/chat/completions
export KB_MODEL=<name of your local model>
export OPENROUTER_API_KEY=local      # any non-empty value; a local server ignores it
sourceshelf describe --all --dry-run
```

Plain `http` is accepted only for `localhost`, `127.0.0.1` and `::1`. The path is tested end to end against a stub server in `tests/test_describe.py`; it has not been tested against a specific local model server, and summary quality depends on the model you choose.

## Privacy warnings

- `sourceshelf describe` sends the full source text (up to 400,000 characters per source) to the configured endpoint, a third party by default. It runs only when you call it, never from `sourceshelf add` or `sourceshelf run`. Fiches marked `confidential: yes` are never sent. Do not describe material you may not share with that provider.
- You are responsible for what you fetch and keep: copyright, licences and site terms apply to the pages, PDFs and feeds you download, and to any summary derived from them. This repository ships no third-party content; the example library is synthetic.
- Fetching contacts the servers you name and reveals your IP address to them.

## Quality and tests

- More than 200 automated tests, about 98% line coverage (the CI summary shows the exact figure); the build fails below 95% (`pytest --cov=sourceshelf --cov-fail-under=95`).
- Regression tests for the safety promises: confidential text never appears in `--exact` search; URLs other than http(s) and addresses on internal networks are refused (also after redirects); file paths in a card cannot point outside `data/`; oversized downloads and unsafe docx files are refused.
- "Golden" tests pin the exact output of search, the web API, the index and the card format, so an unintended change shows up as a failing test. To accept an intended change, run `KB_GOLDEN_UPDATE=1 pytest tests/test_golden.py` and review the diff.
- Coverage is shown automatically: every CI run prints the per-file coverage table on the run's summary page (GitHub Actions) and fails below 95%. Locally: `python3 -m pytest --cov=sourceshelf --cov-report=term-missing`.
- A recall test checks that known questions still find the right sources in the example library (`scripts/recall.py`).
- `sourceshelf check` validates every card, the index and the file hashes. CI also runs `ruff`, a leak scan (gitleaks) and weekly dependency updates (Dependabot).
- All test data is synthetic. Tests run offline; network calls are faked.

## Development

```sh
pip install -e '.[dev]'
sh scripts/verify.sh        # compile, ruff, pytest, sourceshelf check on the examples
```

Licensed under Apache-2.0, see `LICENSE`. Security notes: `SECURITY.md`.
