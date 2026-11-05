import os
import shutil

import pytest

from sourceshelf import cli
from sourceshelf.fiche import write_fiche
from sourceshelf.search import resolve, search


def make(root, fid, title, text, extra=None, file_text=None):
    meta = {"id": fid, "title": title, "status": "used", "origin": "migration", **(extra or {})}
    if file_text is not None:
        path = root / "data" / "text" / f"{fid}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file_text, encoding="utf-8")
        meta["text"] = f"text/{fid}.txt"
    write_fiche(root / "fiches" / f"{fid}.md", meta, text)


def corpus(root):
    make(root, "url-aaa", "Mice", "rodents", {"e-ids": ["L001"], "legacy-path": "evidence/sources/papers/L001.md"},
         "page one about cheese\fpage two about keyword quokka en quokka")
    make(root, "url-bbb", "Cats", "pets", {"e-ids": ["L002"], "file": "papers/candidates/cats.pdf",
                                                     "legacy-path": "evidence/sources/papers/candidates/cats.md"},
         "text\nline two with quokka")
    make(root, "url-ccc", "Dogs", "loyal", None, "only dogs here")


def test_search_ranks_and_gives_page(tmp_path):
    corpus(tmp_path)
    res = search(tmp_path, "quokka")
    assert [r.id for r in res][:2] == ["url-aaa", "url-bbb"]
    assert res[0].page == 2 and "quokka" in res[0].snippet
    assert res[1].page is None and res[1].line == 2


def test_search_empty_and_unknown(tmp_path):
    corpus(tmp_path)
    assert search(tmp_path, "nonexistent") == []
    assert search(tmp_path, "") == []


def test_cache_is_updated(tmp_path):
    corpus(tmp_path)
    search(tmp_path, "quokka")
    assert (tmp_path / "data" / "logs" / "bm25-cache.json").exists()
    make(tmp_path, "url-ddd", "New", "x", None, "quokka quokka quokka quokka")
    assert "url-ddd" in [r.id for r in search(tmp_path, "quokka")]
    (tmp_path / "fiches" / "url-ddd.md").unlink()
    assert "url-ddd" not in [r.id for r in search(tmp_path, "quokka")]


def test_name_counts(tmp_path):
    corpus(tmp_path)
    assert search(tmp_path, "cats")[0].id == "url-bbb"


def test_resolve(tmp_path):
    corpus(tmp_path)
    assert resolve(tmp_path, "L001") == ["url-aaa"]
    assert resolve(tmp_path, "evidence/sources/papers/candidates/cats.md") == ["url-bbb"]
    assert resolve(tmp_path, "url-ccc") == ["url-ccc"]
    assert resolve(tmp_path, "L999") == []


def test_cli_search_and_exact(tmp_path, capsys):
    corpus(tmp_path)
    assert cli.main(["--root", str(tmp_path), "search", "quokka"]) == 0
    out = capsys.readouterr().out
    assert "url-aaa" in out and "p. 2" in out
    if shutil.which("rg"):
        assert cli.main(["--root", str(tmp_path), "search", "--exact", "line two"]) == 0
        assert "url-bbb" in capsys.readouterr().out
    assert cli.main(["--root", str(tmp_path), "search", "nonexistent"]) == 1
    assert cli.main(["--root", str(tmp_path), "resolve", "L001"]) == 0
    assert cli.main(["--root", str(tmp_path), "resolve", "L999"]) == 1


def test_exact_skips_confidential_through_symlinked_dir(tmp_path, capsys):
    if not shutil.which("rg"):
        pytest.skip("rg missing")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "c1.txt").write_text("hidden plugh1\n", encoding="utf-8")
    root = tmp_path / "lib"
    (root / "data").mkdir(parents=True)
    (root / "data" / "text").symlink_to(outside, target_is_directory=True)
    make(root, "url-c2", "Private", "body", {"confidential": "Yes", "text": "text/c1.txt"})
    assert cli.main(["--root", str(root), "search", "--exact", "plugh1"]) == 1
    assert capsys.readouterr().out == ""


def test_exact_skips_hardlinked_copy_of_confidential(tmp_path, capsys):
    if not shutil.which("rg"):
        pytest.skip("rg missing")
    corpus(tmp_path)
    make(tmp_path, "url-c3", "Private", "body", {"confidential": "yes"}, "hidden plugh2")
    (tmp_path / "data" / "papers").mkdir(parents=True, exist_ok=True)
    os.link(tmp_path / "data" / "text" / "url-c3.txt", tmp_path / "data" / "papers" / "copy.txt")
    assert cli.main(["--root", str(tmp_path), "search", "--exact", "plugh2"]) == 1
    assert capsys.readouterr().out == ""


def test_exact_skips_confidential_file_field_and_newline_name(tmp_path, capsys):
    if not shutil.which("rg"):
        pytest.skip("rg missing")
    corpus(tmp_path)
    make(tmp_path, "url-c1", "Private", "body", {"confidential": "yes", "file": "papers/c1.md"}, "plain")
    (tmp_path / "data" / "papers").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "papers" / "c1.md").write_text("hidden plugh\n", encoding="utf-8")
    (tmp_path / "data" / "papers" / "a\nb.txt").write_text("hidden plugh\n", encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "search", "--exact", "plugh"]) == 1
    assert capsys.readouterr().out == ""


def test_exact_skips_confidential(tmp_path, capsys):
    if not shutil.which("rg"):
        pytest.skip("rg missing")
    corpus(tmp_path)
    make(tmp_path, "url-secret", "Secret", "main text", {"confidential": "yes"}, "strictly secret sentence xyzzy")
    make(tmp_path, "url-open", "Open", "main text", None, "public sentence xyzzy")
    assert cli.main(["--root", str(tmp_path), "search", "--exact", "xyzzy"]) == 0
    out = capsys.readouterr().out
    assert "url-open" in out and "secret" not in out and "strictly" not in out
    assert cli.main(["--root", str(tmp_path), "search", "--exact", "strictly secret"]) == 1


def test_bm25_hides_confidential_mixed_case_flag(tmp_path):
    corpus(tmp_path)
    make(tmp_path, "url-c9", "Private", "body", {"confidential": " Yes "}, "hidden plugh9 plugh9")
    assert search(tmp_path, "plugh9") == []


def test_exact_skips_plain_copy_of_confidential(tmp_path, capsys):
    if not shutil.which("rg"):
        pytest.skip("rg missing")
    corpus(tmp_path)
    make(tmp_path, "url-c4", "Private", "body", {"confidential": "yes"}, "hidden plugh4")
    (tmp_path / "data" / "papers").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(tmp_path / "data" / "text" / "url-c4.txt", tmp_path / "data" / "papers" / "copy.txt")
    assert cli.main(["--root", str(tmp_path), "search", "--exact", "plugh4"]) == 1
    assert capsys.readouterr().out == ""
