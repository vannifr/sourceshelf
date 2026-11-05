from sourceshelf import cli
from sourceshelf.add import add_local
from sourceshelf.fiche import load_fiches
from tests.conftest import sha


def test_local_pdf_creates_fiche_without_url(tmp_path, mini_pdf):
    source = tmp_path / "Mijn_Rapport.pdf"
    source.write_bytes(mini_pdf)
    root = tmp_path / "library"
    res = add_local(source, root, "2026-10-21")
    assert res.new and res.id == f"local-{sha(mini_pdf)[:12]}"
    m = load_fiches(root / "fiches")[0].meta
    assert m["title"] == "Mijn Rapport" and m["kind"] == "report" and m["status"] == "new"
    assert m["origin"] == "kb add (local)" and m["sha256"] == sha(mini_pdf)
    assert "url" not in m and m["source-file"] == "Mijn_Rapport.pdf"
    assert (root / "data" / m["file"]).read_bytes() == mini_pdf
    assert "first page sample" in (root / "data" / m["text"]).read_text()


def test_same_content_no_duplicate(tmp_path, mini_pdf):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(mini_pdf)
    b.write_bytes(mini_pdf)
    root = tmp_path / "library"
    first = add_local(a, root, "2026-10-21")
    second = add_local(b, root, "2026-10-21")
    assert not second.new and second.id == first.id
    assert len(load_fiches(root / "fiches")) == 1


def test_local_html_title_from_document(tmp_path):
    source = tmp_path / "pagina.html"
    source.write_text("<html><head><title>Mijn titel</title></head><body><p>hallo wereld</p></body></html>")
    root = tmp_path / "library"
    add_local(source, root, "2026-10-21")
    m = load_fiches(root / "fiches")[0].meta
    assert m["title"] == "Mijn titel" and m["kind"] == "other"


def test_cli_add_path_and_missing_file(tmp_path, mini_pdf, capsys):
    source = tmp_path / "x.pdf"
    source.write_bytes(mini_pdf)
    root = tmp_path / "library"
    assert cli.main(["--root", str(root), "add", str(source)]) == 0
    assert "added: local-" in capsys.readouterr().out
    assert cli.main(["--root", str(root), "add", str(tmp_path / "does-not-exist.pdf")]) == 1
    assert "does not exist" in capsys.readouterr().out
