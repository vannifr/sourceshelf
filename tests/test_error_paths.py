import fcntl
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sourceshelf import cli, fiche
from sourceshelf.fiche import load_fiche, write_fiche
from sourceshelf.index import write_index
from tests.test_run import ArxivNet, make_repo, read_file

META = {"id": "a", "title": "A", "status": "new", "origin": "t"}
NOW = datetime(2026, 10, 23, 7, 20, tzinfo=UTC)


def _fail_on_write(monkeypatch):
    real = Path.write_text

    def broken(self, data, *a, **k):
        real(self, data[: len(data) // 2], *a, **k)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", broken)


def test_full_disk_leaves_existing_fiche_intact(tmp_path, monkeypatch):
    path = tmp_path / "fiches" / "a.md"
    write_fiche(path, META, "old content")
    _fail_on_write(monkeypatch)
    with pytest.raises(OSError):
        write_fiche(path, {**META, "title": "new"}, "new content")
    monkeypatch.undo()
    assert load_fiche(path).meta["title"] == "A"
    assert [p.name for p in path.parent.iterdir()] == ["a.md"]


def test_full_disk_leaves_index_intact(tmp_path, monkeypatch):
    write_fiche(tmp_path / "fiches" / "a.md", META, "")
    write_index(tmp_path)
    before = (tmp_path / "index" / "index.json").read_bytes()
    write_fiche(tmp_path / "fiches" / "b.md", {**META, "id": "b"}, "")
    _fail_on_write(monkeypatch)
    with pytest.raises(OSError):
        write_index(tmp_path)
    monkeypatch.undo()
    assert (tmp_path / "index" / "index.json").read_bytes() == before


def test_corrupt_state_is_visible_in_runlog_and_digest(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "state.json").write_text("{broken", encoding="utf-8")
    from sourceshelf.run import run
    run(tmp_path, ArxivNet(["2501.00001"], mini_pdf, arxiv_xml), NOW)
    assert "state.json unreadable" in read_file(tmp_path, "logs/RUNS.md")
    assert "state.json unreadable" in read_file(tmp_path, "digest/LATEST.md")


def test_search_with_corrupt_fiche_gives_message_and_exit_1(tmp_path, capsys):
    write_fiche(tmp_path / "fiches" / "a.md", META, "kwartel")
    (tmp_path / "fiches" / "broken.md").write_text("no frontmatter", encoding="utf-8")
    assert cli.main(["--root", str(tmp_path), "search", "kwartel"]) == 1
    assert "broken.md" in capsys.readouterr().out


def test_get_and_resolve_with_corrupt_fiche_give_message(tmp_path, capsys):
    (tmp_path / "fiches").mkdir()
    (tmp_path / "fiches" / "broken.md").write_text("---\nid: [\n---\n", encoding="utf-8")
    for cmd in (["get", "a"], ["resolve", "x"], ["describe", "a"], ["index"]):
        assert cli.main(["--root", str(tmp_path), *cmd]) == 1, cmd
        assert "broken.md" in capsys.readouterr().out


@pytest.mark.parametrize("cmd", [["add", "x.txt"], ["describe", "a"], ["index"]])
def test_write_command_refuses_during_running_write(tmp_path, capsys, cmd):
    write_fiche(tmp_path / "fiches" / "a.md", META, "")
    (tmp_path / "x.txt").write_text("text", encoding="utf-8")
    (tmp_path / "data").mkdir()
    with (tmp_path / "data" / "run.lock").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        assert cli.main(["--root", str(tmp_path), *cmd]) == 1
    assert "another writer is busy" in capsys.readouterr().out
    assert not any(p.name.startswith("local-") for p in (tmp_path / "fiches").iterdir())


def test_write_command_releases_lock_afterwards(tmp_path):
    write_fiche(tmp_path / "fiches" / "a.md", META, "")
    assert cli.main(["--root", str(tmp_path), "index"]) == 0
    with (tmp_path / "data" / "run.lock").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_atomic_write_leaves_no_tmp(tmp_path):
    path = tmp_path / "fiches" / "a.md"
    write_fiche(path, META, "x")
    assert os.listdir(path.parent) == ["a.md"]
    assert fiche.load_fiche(path).text.strip() == "x"


def test_cli_full_disk_gives_message_no_traceback(tmp_path, monkeypatch, capsys):
    write_fiche(tmp_path / "fiches" / "a.md", META, "")
    _fail_on_write(monkeypatch)
    assert cli.main(["--root", str(tmp_path), "index"]) == 1
    out = capsys.readouterr().out
    assert "No space left" in out


def test_state_read_error_does_not_move_and_reports(tmp_path, monkeypatch):
    from sourceshelf import run as runmod
    (tmp_path / "data").mkdir()
    path = tmp_path / "data" / "state.json"
    path.write_text("{}", encoding="utf-8")
    real = Path.read_text

    def refuse(self, *a, **k):
        if self == path:
            raise PermissionError(13, "denied")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", refuse)
    state, error = runmod._read_state(tmp_path)
    assert state == {} and "not readable" in error
    assert path.exists()


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.startswith("sourceshelf ")


def test_missing_rg_gives_clear_message(tmp_path, monkeypatch, capsys):
    (tmp_path / "fiches").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert cli.main(["--root", str(tmp_path), "search", "--exact", "foo"]) == 1
    err = capsys.readouterr().err
    assert "rg not found" in err and "ripgrep" in err and "write failed" not in err


def test_missing_pdftotext_marks_fiche_with_error(tmp_path, monkeypatch, arxiv_xml, mini_pdf):
    from sourceshelf.add import add_source
    from tests.test_add import net_arxiv

    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    res = add_source("https://arxiv.org/abs/2501.00001", tmp_path, net_arxiv(arxiv_xml, mini_pdf), "2026-10-20")
    meta = load_fiche(tmp_path / "fiches" / f"{res.id}.md").meta
    assert meta["status"] == "new" and "pdftotext not found" in meta["error"] and "poppler" in meta["error"]
