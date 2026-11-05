from sourceshelf.fiche import write_fiche
from sourceshelf.search import resolve


def fiche(root, fid, old, extra=None):
    meta = {"id": fid, "title": fid, "status": "used", "origin": "migration-source-material", "legacy-path": old}
    meta.update(extra or {})
    write_fiche(root / "fiches" / f"{fid}.md", meta, "content")


def test_resolve_legacy_path_exact(tmp_path):
    fiche(tmp_path, "local-aaa", "evidence/sources/agentic-se-2026-09-28/rapport.md")
    fiche(tmp_path, "local-bbb", "evidence/sources/agentic-se-2026-09-28/deel-t12.md")
    assert resolve(tmp_path, "evidence/sources/agentic-se-2026-09-28/rapport.md") == ["local-aaa"]
    assert resolve(tmp_path, "evidence/sources/agentic-se-2026-09-28/onbekend.md") == []


def test_resolve_legacy_path_directory(tmp_path):
    fiche(tmp_path, "local-aaa", "evidence/sources/agentic-se-2026-09-28/rapport.md")
    fiche(tmp_path, "local-bbb", "evidence/sources/agentic-se-2026-09-28/deel-t12.md")
    fiche(tmp_path, "local-ccc", "evidence/sources/openspace-proceedings-analyse/a.md")
    assert sorted(resolve(tmp_path, "evidence/sources/agentic-se-2026-09-28/")) == ["local-aaa", "local-bbb"]
    assert resolve(tmp_path, "evidence/sources/agentic") == []


def test_resolve_legacy_path_source_materials_and_second_file(tmp_path):
    fiche(tmp_path, "local-aaa", "source-materials/agile-academy/08_Whitepaper_draft.md",
          {"legacy-path-extra": ["source-materials/agile-academy/08_Whitepaper_draft.docx"]})
    assert resolve(tmp_path, "source-materials/agile-academy/08_Whitepaper_draft.md") == ["local-aaa"]
    assert resolve(tmp_path, "source-materials/agile-academy/08_Whitepaper_draft.docx") == ["local-aaa"]


def test_resolve_legacy_path_leaves_papers_and_id_alone(tmp_path):
    fiche(tmp_path, "local-aaa", "evidence/sources/papers/L001.md", {"e-ids": ["L001"]})
    assert resolve(tmp_path, "L001") == ["local-aaa"]
    assert resolve(tmp_path, "evidence/sources/papers/L001.md") == ["local-aaa"]
    assert resolve(tmp_path, "evidence/sources/papers/L002.md") == []
