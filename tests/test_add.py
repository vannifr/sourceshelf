
import pytest

from sourceshelf import cli
from sourceshelf.add import add_source
from sourceshelf.fiche import load_fiches
from sourceshelf.net import NetError, Response
from tests.conftest import FakeNet, sha

ARXIV_API = "https://export.arxiv.org/api/query?id_list=2501.00001"
ARXIV_URL = "https://arxiv.org/abs/2501.00001v2"
ARXIV_PDF = "https://arxiv.org/pdf/2501.00001"


def net_arxiv(arxiv_xml, mini_pdf):
    return FakeNet({
        ARXIV_API: Response(200, "application/atom+xml", arxiv_xml),
        ARXIV_PDF: Response(200, "application/pdf", mini_pdf),
    })


def test_arxiv_creates_fiche_with_sha_and_text(tmp_path, arxiv_xml, mini_pdf):
    res = add_source(ARXIV_URL, tmp_path, net_arxiv(arxiv_xml, mini_pdf), "2026-10-20")
    assert res.id == "arxiv-2501.00001" and res.new
    fiche = load_fiches(tmp_path / "fiches")[0]
    m = fiche.meta
    assert m["title"] == "Agentic Software Engineering in Practice"
    assert m["authors"] == "Ada Voorbeeld, Bob Test"
    assert m["year"] == 2025 and m["doi"] == "10.1000/xyz123"
    assert m["kind"] == "preprint" and m["status"] == "new" and m["origin"] == "kb add"
    assert m["fetched"] == "2026-10-20"
    assert m["sha256"] == sha(mini_pdf)
    assert "error" not in m
    assert (tmp_path / "data" / m["file"]).read_bytes() == mini_pdf
    text = (tmp_path / "data" / m["text"]).read_text()
    assert "first page sample" in text and "\f" in text
    assert "We study agents." in fiche.text


def test_second_time_no_duplicate(tmp_path, arxiv_xml, mini_pdf):
    net = net_arxiv(arxiv_xml, mini_pdf)
    add_source(ARXIV_URL, tmp_path, net, "2026-10-20")
    count = len(net.requests_made)
    res = add_source("https://arxiv.org/pdf/2501.00001v1", tmp_path, net, "2026-10-21")
    assert not res.new and res.id == "arxiv-2501.00001"
    assert len(net.requests_made) == count
    assert len(load_fiches(tmp_path / "fiches")) == 1


def test_dedupe_on_url_of_existing_fiche(tmp_path):
    (tmp_path / "fiches").mkdir()
    (tmp_path / "fiches" / "e119.md").write_text(
        "---\nid: e119\ntitle: DORA\nstatus: used\norigin: migration\n"
        "url: https://services.google.com/fh/files/misc/2024_final_dora_report.pdf\n---\n")
    net = FakeNet({})
    res = add_source("https://services.google.com/fh/files/misc/2024_final_dora_report.pdf?utm_source=x",
                   tmp_path, net, "2026-10-20")
    assert not res.new and res.id == "e119" and net.requests_made == []


def test_pdf_from_plain_site(tmp_path, mini_pdf):
    url = "https://example.org/report.pdf"
    net = FakeNet({url: Response(200, "application/pdf", mini_pdf)})
    res = add_source(url, tmp_path, net, "2026-10-20")
    m = load_fiches(tmp_path / "fiches")[0].meta
    assert res.id.startswith("url-") and m["title"] == "report.pdf"
    assert m["kind"] == "report" and m["sha256"] == sha(mini_pdf)


def test_html_page(tmp_path):
    url = "https://example.org/artikel"
    html = b"<html><head><title>Een   titel</title><style>x{}</style></head><body><p>Hallo wereld</p></body></html>"
    net = FakeNet({url: Response(200, "text/html; charset=utf-8", html)})
    res = add_source(url, tmp_path, net, "2026-10-20")
    m = load_fiches(tmp_path / "fiches")[0].meta
    assert m["title"] == "Een titel" and m["file"] == f"html/{res.id}.html"
    assert "Hallo wereld" in (tmp_path / "data" / m["text"]).read_text()
    assert "x{}" not in (tmp_path / "data" / m["text"]).read_text()


@pytest.mark.parametrize("answer", [Response(403, "text/html", b"no"), Response(404, "text/html", b""),
                                      NetError("time-out")])
def test_failed_download_gives_fiche_with_error(tmp_path, answer):
    url = "https://example.org/closed.pdf"
    res = add_source(url, tmp_path, FakeNet({url: answer}), "2026-10-20")
    assert res.error
    m = load_fiches(tmp_path / "fiches")[0].meta
    assert m["status"] == "new" and m["error"] and m["url"] == url
    assert "sha256" not in m


def test_failed_arxiv_metadata_gives_error(tmp_path):
    res = add_source(ARXIV_URL, tmp_path, FakeNet({}), "2026-10-20")
    m = load_fiches(tmp_path / "fiches")[0].meta
    assert res.error and m["status"] == "new" and "error" in m and m["id"] == "arxiv-2501.00001"


def test_retry_after_error_completes_fiche(tmp_path, arxiv_xml, mini_pdf):
    add_source(ARXIV_URL, tmp_path, FakeNet({}), "2026-10-20")
    res = add_source(ARXIV_URL, tmp_path, net_arxiv(arxiv_xml, mini_pdf), "2026-10-21")
    assert res.new is True and not res.error
    fiches = load_fiches(tmp_path / "fiches")
    assert len(fiches) == 1 and "error" not in fiches[0].meta and fiches[0].meta["sha256"] == sha(mini_pdf)


def test_pdf_without_text_layer(tmp_path):
    url = "https://example.org/scan.pdf"
    empty = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
    add_source(url, tmp_path, FakeNet({url: Response(200, "application/pdf", empty)}), "2026-10-20")
    assert load_fiches(tmp_path / "fiches")[0].meta["text-layer"] == "no"


def test_cli_add_and_get(tmp_path, monkeypatch, arxiv_xml, mini_pdf, capsys):
    monkeypatch.setattr("sourceshelf.cli.make_net", lambda: net_arxiv(arxiv_xml, mini_pdf))
    assert cli.main(["--root", str(tmp_path), "add", ARXIV_URL]) == 0
    assert (tmp_path / "index" / "index.json").exists()
    assert cli.main(["--root", str(tmp_path), "get", "arxiv-2501.00001"]) == 0
    out = capsys.readouterr().out
    assert "Agentic Software Engineering in Practice" in out and "data/pdf/arxiv-2501.00001.pdf" in out
    assert cli.main(["--root", str(tmp_path), "get", "does-not-exist"]) == 1


def test_cli_add_exit_1_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr("sourceshelf.cli.make_net", lambda: FakeNet({}))
    assert cli.main(["--root", str(tmp_path), "add", "https://example.org/x.pdf"]) == 1
