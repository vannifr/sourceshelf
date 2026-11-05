# Contributing

sourceshelf is a small, best-effort project. Issues and pull requests are welcome.

- Python 3.12 or newer (CI tests 3.12 and 3.13, Linux only). Setup: `pip install -e '.[dev]'`; you also need `pdftotext` (poppler) and `rg` (ripgrep) for the full test run.
- Run `sh scripts/verify.sh` before you push: compile, `ruff check`, pytest with a 95% coverage floor, and `sourceshelf check` on the example library. Do not run `ruff format`.
- Code, CLI text, docs and tests are English. Library content (fiches, texts) may be in any language.
- New behaviour needs a test. Anything touching `confidential`, fetching or file paths needs a regression test for the failure it guards against.
- Errors are never silent: a failed operation gives a message and a non-zero exit code, not a traceback and not a quiet success.
- Test data must be synthetic. Never commit personal data, real sources or secrets (CI runs gitleaks).
- Report vulnerabilities privately, see `SECURITY.md`. Format, commands and limits: `docs/reference.md`.

## Where to start

Issue templates (`.github/ISSUE_TEMPLATE/`) ask for what a maintainer needs: version, command, expected and actual result. Good first contributions, each small and self-contained:

- a documentation fix or a clearer error message (add a test that shows the old message);
- a new input format for `sourceshelf add` (extraction function in `sourceshelf/extract.py`, a synthetic fixture, a test);
- a recall question for `examples/recall.tsv` on the synthetic example library;
- a test for an error path you hit (full disk, missing tool, corrupt fiche).

This is a one-maintainer project: replies can take days, and a change that adds a dependency or changes the fiche format needs an issue first.

## CI and repository settings

CI (`.github/workflows/ci.yml`) runs `scripts/verify.sh` on Python 3.12 and 3.13, a non-editable install check, `pip-audit`, a gitleaks scan, `actionlint`, and a weekly scheduled run. Ruff selects `E4,E7,E9,F,I,B,UP,S`. The ignored security rules (`S310`, `S314`, `S506`, `S603`, `S607`) are reviewed false positives: URL schemes are checked in `sourceshelf/net.py`, `rg`, `pdftotext` and `git` are called with fixed argument lists and no shell, and the YAML loader is libyaml's safe loader.

Recommended settings for the maintainer (they cannot be set from files): protect `main`, require the `test`, `install`, `audit`, `gitleaks` and `workflow-lint` checks and one review, and enable private vulnerability reporting.
