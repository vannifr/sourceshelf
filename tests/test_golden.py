import json
import os
from pathlib import Path

import pytest

from sourceshelf import cli
from sourceshelf.fiche import write_fiche
from tests.test_search import corpus, make
from tests.test_web import _raw, server  # noqa: F401

GOLDEN = Path(__file__).parent / "golden"


def compare(name: str, current: str) -> None:
    path = GOLDEN / name
    if os.environ.get("KB_GOLDEN_UPDATE") == "1":
        path.write_text(current, encoding="utf-8")
    assert current == path.read_text(encoding="utf-8"), f"golden {name} differs (KB_GOLDEN_UPDATE=1 to update)"


def test_golden_search_json(tmp_path, capsys):
    corpus(tmp_path)
    out = {}
    for query in ("quokka", "dogs", "rodents", "nonexistent"):
        code = cli.main(["--root", str(tmp_path), "search", "--json", query])
        out[query] = json.loads(capsys.readouterr().out)
        assert code == (0 if out[query] else 1)
    compare("search-json.json", json.dumps(out, indent=2, ensure_ascii=False) + "\n")


def test_golden_search_order_fields_and_kind(tmp_path, capsys):
    corpus(tmp_path)
    make(tmp_path, "local-eigen", "Eigen cats", "eigen", {"origin": "kb add (local)"}, "cats quokka eigen")
    out = {}
    for flags in ([], ["--fields"], ["--kind", "external"], ["--kind", "own"]):
        cli.main(["--root", str(tmp_path), "search", "--json", *flags, "quokka cats"])
        out[" ".join(flags) or "default"] = [r["id"] for r in json.loads(capsys.readouterr().out)]
    compare("search-order.json", json.dumps(out, indent=2) + "\n")


def test_e2e_subprocess_search(tmp_path):
    import subprocess
    import sys
    corpus(tmp_path)
    r = subprocess.run([sys.executable, "-m", "sourceshelf", "--root", str(tmp_path), "search", "--json", "quokka"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0 and r.stderr == ""
    assert [t["id"] for t in json.loads(r.stdout)][:2] == ["url-aaa", "url-bbb"]


def test_golden_index(tmp_path):
    make(tmp_path, "url-aaa", "Mice", "rodents", {"e-ids": ["L001"], "url": "https://example.org/m"}, "cheese")
    make(tmp_path, "url-ccc", "Dogs", "loyal", {"year": 2020}, "dogs")
    assert cli.main(["--root", str(tmp_path), "index"]) == 0
    compare("index.json", (tmp_path / "index" / "index.json").read_text(encoding="utf-8"))


def test_golden_fiche_key_order(tmp_path):
    meta = {
        "id": "url-gold", "title": "Title", "kind": "report", "status": "used", "origin": "test",
        "url": "https://example.org/a", "doi": "10.1/x", "authors": "A. Author", "year": 2024,
        "e-ids": ["L001"], "file": "a.pdf", "sha256": "0" * 64, "text": "text/url-gold.txt",
        "legacy-path": "evidence/a.md", "confidential": "yes", "no-text": "reason", "error": "broken",
        "machine": {"model": "m"},
        "machine-summary": {"text": "s", "model": "m", "prompt": "s21-b", "date": "2026-01-01"},
    }
    path = tmp_path / "url-gold.md"
    write_fiche(path, meta, "content")
    compare("fiche.md", path.read_text(encoding="utf-8"))


def test_golden_api(server):  # noqa: F811
    port = server.rsplit(":", 1)[1]
    out = {}
    for path in ("/api/search?q=quokka", "/api/search?q=else", "/api/sources"):
        status, body = _raw(server, f"127.0.0.1:{port}", path=path)
        assert status == 200
        out[path] = json.loads(body)
    compare("api.json", json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


@pytest.mark.parametrize("name", ["search-json.json", "search-order.json", "index.json", "fiche.md", "api.json"])
def test_golden_file_exists(name):
    assert (GOLDEN / name).is_file()
