import json

from sourceshelf import cli
from sourceshelf.fiche import load_fiches
from sourceshelf.inventory import read_manifest


def _source_dir(tmp_path, mini_pdf):
    source = tmp_path / "src"
    (source / "sub").mkdir(parents=True)
    (source / ".git").mkdir()
    (source / "a.pdf").write_bytes(mini_pdf)
    (source / "sub" / "n.md").write_text("notitie over kwaliteit\n")
    (source / "sub" / "x.exe").write_bytes(b"x")
    (source / ".git" / "hidden.md").write_text("no")
    return source


def test_inventory_and_add_from(tmp_path, mini_pdf, capsys):
    source, root = _source_dir(tmp_path, mini_pdf), tmp_path / "library"
    man = tmp_path / "m.jsonl"
    assert cli.main(["--root", str(root), "inventory", str(source), "--out", str(man)]) == 0
    items = read_manifest(man)
    assert sorted(i["name"] for i in items) == ["a.pdf", "n.md"]
    assert cli.main(["--root", str(root), "add", "--from", str(man)]) == 0
    assert len(load_fiches(root / "fiches")) == 2
    capsys.readouterr()
    assert cli.main(["--root", str(root), "add", "--from", str(man)]) == 0
    assert "0 new, 2 already existed" in capsys.readouterr().out
    cli.main(["--root", str(root), "inventory", str(source), "--out", str(man), "--only-new"])
    assert read_manifest(man) == []


def test_add_from_skips_confidential_and_reports_error(tmp_path, mini_pdf, capsys):
    source, root = _source_dir(tmp_path, mini_pdf), tmp_path / "library"
    man = tmp_path / "m.jsonl"
    man.write_text(json.dumps({"path": str(source / "a.pdf"), "confidential": True}) + "\n"
                   + json.dumps({"path": str(source / "gone.pdf")}) + "\n"
                   + json.dumps({"path": str(source / "sub" / "n.md")}) + "\n")
    assert cli.main(["--root", str(root), "add", "--from", str(man)]) == 1
    out = capsys.readouterr().out
    assert "1 new, 0 already existed, 1 skipped, 1 error(s)" in out
    assert [f.meta["source-file"] for f in load_fiches(root / "fiches")] == ["n.md"]


def test_add_from_broken_manifest(tmp_path, capsys):
    man = tmp_path / "m.jsonl"
    man.write_text("{not json\n")
    assert cli.main(["--root", str(tmp_path / "library"), "add", "--from", str(man)]) == 1
    assert "manifest unreadable" in capsys.readouterr().out


def test_search_json(tmp_path, capsys):
    from tests.test_search import make
    make(tmp_path, "url-a", "Alfa", "", None, "quokka quokka")
    assert cli.main(["--root", str(tmp_path), "search", "quokka", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data[0]["id"] == "url-a" and {"score", "path", "page", "line", "snippet"} <= set(data[0])


def test_add_from_plain_list_with_url(tmp_path, monkeypatch):
    from sourceshelf import cli

    called = []

    class Res:
        new = True
        error = None
        id = "url-x"

    monkeypatch.setattr(cli, "add_source", lambda url, root, net, date: called.append(url) or Res())
    monkeypatch.setattr(cli, "make_net", lambda: None)
    monkeypatch.setattr(cli, "cmd_index", lambda root: 0)
    plain_list = tmp_path / "l.txt"
    plain_list.write_text("# c\nhttps://example.org/a\n/does/not/exist.pdf\n", encoding="utf-8")
    assert cli.cmd_add_from(tmp_path, plain_list) == 1
    assert called == ["https://example.org/a"]
