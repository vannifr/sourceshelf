# sourceshelf: agent instructions

Rules for an agent working in this repository: a small Python tool that keeps a local library of sources as fiches and searches them. Setup, behaviour and limits: `README.md`, `CONTRIBUTING.md`, `docs/reference.md`.

## Profiel (profile)

- Profile: poort (public GitHub repository `vannifr/sourceshelf`)
- Tier C: `confidential` handling, fetching (`sourceshelf/net.py`) and file path handling (`sourceshelf/paths.py`); every change needs a regression test for the failure it guards against
- Deploy: none; CI only (`.github/workflows/ci.yml`, GitHub Actions)
- Sensitivity: public repository, English only; synthetic test data only; no personal data, real sources or secrets; vulnerabilities go through `SECURITY.md`
- Check: `sh scripts/verify.sh` (compile, `ruff check`, pytest with 95% coverage floor, `sourceshelf check` on `examples/library`)

## Rules

- Python 3.12 or newer. Never run `ruff format`.
- Code, CLI text, docs and tests are English; library content may be in any language.
- New behaviour needs a test. Errors are never silent: a message and a non-zero exit code.
- A change that adds a dependency or changes the fiche format needs an issue first.
- Do not add internal process files (backlog, goals, plans) to this public repository.

## Agent working rules

- Global working rules: `~/.claude/CLAUDE.md`; do not copy them here.
- Repo conventions are checked by `~/projects/agent-config/tools/docs-check/check_repo_docs.py`; keep this file under 120 lines.
