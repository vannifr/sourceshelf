import json

from sourceshelf.fiche import load_fiche
from sourceshelf.index import write_index
from sourceshelf.search import search
from tests.test_search import make

CONTENT = "heading one\n\nline with quokka and more"


def _setup(root):
    make(root, "memo-1", "01 Project Plan", "", {"confidential": "yes"}, CONTENT)
    make(root, "open-1", "Open note", "", None, "something about badgers")


def test_fiche_is_confidential_and_has_no_content(tmp_path):
    _setup(tmp_path)
    fiche = tmp_path / "fiches" / "memo-1.md"
    assert load_fiche(fiche).meta["confidential"] == "yes"
    assert "quokka" not in fiche.read_text(encoding="utf-8")


def test_index_contains_no_content(tmp_path):
    _setup(tmp_path)
    assert write_index(tmp_path) == []
    text = (tmp_path / "index" / "index.json").read_text(encoding="utf-8")
    assert "quokka" not in text and "heading" not in text


def test_content_gives_no_hit(tmp_path):
    _setup(tmp_path)
    assert search(tmp_path, "quokka") == []
    assert search(tmp_path, "heading") == []


def test_findable_by_title_without_snippet(tmp_path):
    _setup(tmp_path)
    res = search(tmp_path, "plan")
    assert len(res) == 1 and res[0].id == "memo-1"
    assert res[0].snippet == "" and res[0].line is None and res[0].page is None
    assert res[0].path.startswith("fiches/")


def test_content_also_disappears_from_existing_cache(tmp_path):
    _setup(tmp_path)
    cache = tmp_path / "data" / "logs" / "bm25-cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"v": 1, "docs": {"x": {"sig": [0, 0], "c": {"quokka": 3}}}}))
    assert search(tmp_path, "quokka") == []
    assert "quokka" not in cache.read_text(encoding="utf-8")
