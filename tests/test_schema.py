import json
from pathlib import Path

import pytest

from sourceshelf import cli
from sourceshelf.fiche import FicheError, load_fiches, validate, validate_dir

VALID = """---
id: arxiv-2501.00001
title: "Example"
kind: preprint
status: new
origin: test
---
tekst
"""


def write(dir_: Path, name: str, content: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / name
    path.write_text(content, encoding="utf-8")
    return path


def test_valid_fiche_parses(tmp_path):
    path = write(tmp_path, "arxiv-2501.00001.md", VALID)
    fiches = load_fiches(tmp_path)
    assert fiches[0].meta["id"] == "arxiv-2501.00001"
    assert fiches[0].path == path
    assert validate(fiches[0]) == []


@pytest.mark.parametrize("fields", ["id", "title", "status", "origin"])
def test_missing_required_field(tmp_path, fields):
    content = "\n".join(r for r in VALID.splitlines() if not r.startswith(f"{fields}:"))
    write(tmp_path, "arxiv-2501.00001.md", content)
    errors = validate(load_fiches(tmp_path)[0])
    assert any(fields in f for f in errors)


def test_invalid_status(tmp_path):
    write(tmp_path, "arxiv-2501.00001.md", VALID.replace("status: new", "status: broken"))
    assert any("status" in f for f in validate(load_fiches(tmp_path)[0]))


def test_status_removed_is_valid(tmp_path):
    write(tmp_path, "arxiv-2501.00001.md", VALID.replace("status: new", "status: removed"))
    assert validate(load_fiches(tmp_path)[0]) == []


def test_file_name_must_be_id(tmp_path):
    write(tmp_path, "iets-anders.md", VALID)
    assert any("file name" in f for f in validate(load_fiches(tmp_path)[0]))


def test_bad_sha256(tmp_path):
    write(tmp_path, "arxiv-2501.00001.md", VALID.replace("status: new", "status: new\nsha256: abc"))
    assert any("sha256" in f for f in validate(load_fiches(tmp_path)[0]))


def test_file_requires_sha256(tmp_path):
    write(tmp_path, "arxiv-2501.00001.md", VALID.replace("status: new", "status: new\nfile: a.pdf"))
    assert any("sha256" in f for f in validate(load_fiches(tmp_path)[0]))


def test_duplicate_id(tmp_path):
    write(tmp_path, "arxiv-2501.00001.md", VALID)
    write(tmp_path / "sub", "arxiv-2501.00001.md", VALID)
    assert any("duplicate" in f for f in validate_dir(tmp_path))


def test_fiche_without_frontmatter(tmp_path):
    write(tmp_path, "x.md", "no frontmatter")
    with pytest.raises(FicheError):
        load_fiches(tmp_path)


def test_check_exit_code(tmp_path, capsys):
    write(tmp_path / "fiches", "arxiv-2501.00001.md", VALID)
    assert cli.main(["--root", str(tmp_path), "check"]) == 0
    write(tmp_path / "fiches", "broken.md", VALID.replace("status: new", "status: x"))
    assert cli.main(["--root", str(tmp_path), "check"]) == 1
    assert "broken" in capsys.readouterr().out


def test_check_empty_dir_is_green(tmp_path):
    assert cli.main(["--root", str(tmp_path), "check"]) == 0


def test_index_contains_id_title_status_sha(tmp_path):
    write(tmp_path / "fiches", "arxiv-2501.00001.md", VALID.replace("status: new", "status: new\nsha256: " + "a" * 64))
    assert cli.main(["--root", str(tmp_path), "index"]) == 0
    data = json.loads((tmp_path / "index" / "index.json").read_text())
    assert data == [{"id": "arxiv-2501.00001", "title": "Example", "year": None, "kind": "preprint",
                     "status": "new", "sha256": "a" * 64}]
