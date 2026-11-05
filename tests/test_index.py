from sourceshelf.cli import cmd_check
from sourceshelf.fiche import write_fiche
from sourceshelf.index import index_drift, write_index


def _fiche(root, fid):
    write_fiche(root / "fiches" / f"{fid}.md", {"id": fid, "title": fid, "status": "new", "origin": "t"}, "")


def test_missing_index_is_no_error(tmp_path):
    _fiche(tmp_path, "a")
    assert index_drift(tmp_path) == []


def test_current_index_is_green(tmp_path):
    _fiche(tmp_path, "a")
    write_index(tmp_path)
    assert index_drift(tmp_path) == []
    assert cmd_check(tmp_path) == 0


def test_outdated_index_is_an_error(tmp_path, capsys):
    _fiche(tmp_path, "a")
    write_index(tmp_path)
    _fiche(tmp_path, "b")
    assert cmd_check(tmp_path) == 1
    assert "index/index.json" in capsys.readouterr().out


def test_write_index_is_deterministic(tmp_path):
    _fiche(tmp_path, "b")
    _fiche(tmp_path, "a")
    write_index(tmp_path)
    first = (tmp_path / "index" / "index.json").read_bytes()
    write_index(tmp_path)
    assert (tmp_path / "index" / "index.json").read_bytes() == first


def test_check_with_data_ignores_outdated_index(tmp_path):
    _fiche(tmp_path, "a")
    write_index(tmp_path)
    _fiche(tmp_path, "b")
    (tmp_path / "data").mkdir()
    assert cmd_check(tmp_path, tmp_path / "data") == 0
