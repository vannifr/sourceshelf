import fcntl
import json
from datetime import UTC, datetime
from urllib.parse import quote

import yaml

from sourceshelf import cli
from sourceshelf import update as upd
from sourceshelf.fiche import load_fiches, write_fiche
from sourceshelf.net import NetError, Response
from sourceshelf.run import load_sources
from tests.conftest import FIXTURES, FakeNet

NOW = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
LANDING = "https://dora.dev/research/"
OLD = "https://services.google.com/fh/files/misc/2024_final_dora_report.pdf"
NEW = "https://services.google.com/fh/files/misc/2025_state_of_ai_dora_report.pdf"
WORK = "https://api.openalex.org/works"
SELECT = "select=id,doi,title,locations"


def html(name):
    return Response(200, "text/html", (FIXTURES / name).read_bytes())


def js(name):
    return Response(200, "application/json", (FIXTURES / name).read_bytes())


class HeadNet(FakeNet):
    def __init__(self, answers, heads=None):
        super().__init__(answers)
        self.heads = heads or {}

    def head(self, url):
        answer = self.heads.get(url)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise NetError("head refused")
        return answer


def repo(tmp_path, sources=None):
    sources = sources if sources is not None else [
        {"id": "dora", "kind": "url-list", "urls": [OLD], "cadence_days": 30, "quality": "official",
         "series": "dora", "landing": LANDING}]
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump({"sources": sources}), encoding="utf-8")
    (tmp_path / "fiches").mkdir(exist_ok=True)


def fiche(tmp_path, fid, text="", **meta):
    base = {"id": fid, "title": f"Title {fid}", "status": "new", "origin": "kb add"}
    write_fiche(tmp_path / "fiches" / f"{fid}.md", {**base, **meta}, text)


def get(tmp_path, fid):
    return next(f for f in load_fiches(tmp_path / "fiches") if f.meta["id"] == fid)


def log(tmp_path):
    path = tmp_path / "logs" / "RUNS.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_sources_tolerate_series_and_landing(tmp_path):
    repo(tmp_path)
    s = load_sources(tmp_path)[0]
    assert s.series == "dora" and s.landing == LANDING


def edition_net(mini_pdf):
    return FakeNet({LANDING: html("update-landing.html"), NEW: Response(200, "application/pdf", mini_pdf)})


def test_edition_fixture_creates_superseding_fiche(tmp_path, mini_pdf):
    repo(tmp_path)
    fiche(tmp_path, "url-old", url=OLD, year=2024, kind="report", quality="official")
    r = upd.update(tmp_path, edition_net(mini_pdf), NOW, only="edition")
    assert r.code == 0 and len(r.editions) == 1
    new_id, previous = r.editions[0]
    created = get(tmp_path, new_id)
    assert previous == "url-old" and created.meta["supersedes"] == "url-old" and created.meta["series"] == "dora"
    assert created.meta["quality"] == "official"
    assert get(tmp_path, "url-old").meta["series"] == "dora"
    assert new_id in (tmp_path / "index" / "index.json").read_text()
    assert "update finished editions=1" in log(tmp_path)
    again = upd.update(tmp_path, edition_net(mini_pdf), NOW, only="edition")
    assert again.editions == [] and again.code == 0


def test_edition_dry_run_writes_nothing(tmp_path, mini_pdf):
    repo(tmp_path)
    fiche(tmp_path, "url-old", url=OLD, year=2024)
    r = upd.update(tmp_path, edition_net(mini_pdf), NOW, only="edition", dry_run=True)
    assert r.candidates == [("dora", NEW, "url-old")]
    assert len(load_fiches(tmp_path / "fiches")) == 1
    assert not (tmp_path / "logs").exists() and not (tmp_path / "index").exists()


def test_edition_without_previous_and_series_already_set(tmp_path, mini_pdf):
    first = tmp_path / "first"
    first.mkdir()
    repo(first)
    r = upd.update(first, edition_net(mini_pdf), NOW, only="edition")
    assert r.editions and get(first, r.editions[0][0]).meta.get("supersedes") is None
    second = tmp_path / "second"
    second.mkdir()
    repo(second)
    fiche(second, "url-old", url=OLD, year=2024, series="dora")
    r = upd.update(second, edition_net(mini_pdf), NOW, only="edition")
    assert r.editions[0][1] == "url-old"


def test_edition_errors_are_reported_and_logged(tmp_path, mini_pdf):
    repo(tmp_path)
    fiche(tmp_path, "url-old", url=OLD, year=2024)
    r = upd.update(tmp_path, FakeNet({LANDING: Response(503, "", b"")}), NOW, only="edition")
    assert r.code == 1 and "HTTP 503" in r.errors[0]
    assert "update error: edition dora" in log(tmp_path)
    r = upd.update(tmp_path, FakeNet({LANDING: html("update-landing.html"), NEW: Response(500, "", b"")}), NOW,
                   only="edition")
    assert r.code == 1 and "HTTP 500" in r.errors[0]
    r = upd.update(tmp_path, FakeNet({}), NOW, only="edition")
    assert r.code == 1


def test_edition_existing_fiche_not_new(tmp_path, monkeypatch, mini_pdf):
    repo(tmp_path)
    fiche(tmp_path, "url-old", url=OLD, year=2024)

    class R:
        id, new, error = "x", False, None

    monkeypatch.setattr(upd, "add_source", lambda *a: R())
    r = upd.update(tmp_path, edition_net(mini_pdf), NOW, only="edition")
    assert r.editions == [] and r.code == 0


def test_invalid_sources_file_is_error(tmp_path):
    (tmp_path / "sources.yaml").write_text("sources: [{id: x}]", encoding="utf-8")
    (tmp_path / "fiches").mkdir()
    r = upd.update(tmp_path, FakeNet({}), NOW, only="edition")
    assert r.code == 1 and "invalid source" in r.errors[0]


def preprint(tmp_path, fid="arxiv-2501.00001", **extra):
    meta = {"url": "https://arxiv.org/abs/2501.00001", "kind": "preprint", "title": "Sample Preprint Title",
            "quality": "preprint"}
    fiche(tmp_path, fid, "## Abstract (arXiv)\n\nText.", **{**meta, **extra})


ARXIV_URL = f"{WORK}/https://doi.org/10.48550/arxiv.2501.00001?{SELECT}"


def test_preprint_fixture_gets_doi_via_arxiv_id(tmp_path):
    repo(tmp_path, [])
    preprint(tmp_path)
    r = upd.update(tmp_path, FakeNet({ARXIV_URL: js("openalex-published.json")}), NOW, only="preprint")
    assert r.code == 0 and r.dois == [("arxiv-2501.00001", "10.1145/3600000.3600001")]
    f = get(tmp_path, "arxiv-2501.00001")
    assert f.meta["doi"] == "10.1145/3600000.3600001" and f.meta["quality"] == "peer-reviewed"
    assert "machine:\n  published-version: doi 10.1145/3600000.3600001" in f.text
    assert "2026-11-11 via OpenAlex (https://openalex.org/W111)" in f.text
    assert f.text.startswith("## Abstract (arXiv)")


def test_preprint_title_search_and_old_style_id(tmp_path):
    repo(tmp_path, [])
    preprint(tmp_path, "arxiv-cs-0501001", url="https://arxiv.org/abs/cs/0501001", title="Sample: Preprint title!")
    miss = f"{WORK}/https://doi.org/10.48550/arxiv.cs/0501001?{SELECT}"
    search = f"{WORK}?search={quote('Sample: Preprint title!')}&per-page=5&{SELECT}"
    net = FakeNet({miss: Response(404, "", b""), search: js("openalex-search.json")})
    assert upd.update(tmp_path, net, NOW, only="preprint", dry_run=True).dois == [("arxiv-cs-0501001", "10.1000/journal.1")]
    net = FakeNet({miss: Response(200, "", b'{"locations": []}'), search: js("openalex-search.json")})
    r = upd.update(tmp_path, net, NOW, only="preprint")
    assert r.dois == [("arxiv-cs-0501001", "10.1000/journal.1")]


def test_preprint_without_publication_is_left(tmp_path):
    repo(tmp_path, [])
    preprint(tmp_path, url="https://example.org/paper", title="Unpublished title")
    search = f"{WORK}?search={quote('Unpublished title')}&per-page=5&{SELECT}"
    r = upd.update(tmp_path, FakeNet({search: js("openalex-search.json")}), NOW, only="preprint")
    assert r.dois == [] and r.code == 0 and "doi" not in get(tmp_path, "arxiv-2501.00001").meta


def test_preprint_skips_confidential_and_existing_doi(tmp_path):
    repo(tmp_path, [])
    preprint(tmp_path, "a", confidential="yes")
    preprint(tmp_path, "b", doi="10.1/x")
    preprint(tmp_path, "c", kind="report")
    net = FakeNet({})
    assert upd.update(tmp_path, net, NOW, only="preprint").code == 0 and net.requests_made == []


def test_preprint_dry_run_and_machine_note_variants(tmp_path):
    repo(tmp_path, [])
    preprint(tmp_path)
    net = FakeNet({ARXIV_URL: js("openalex-published.json")})
    r = upd.update(tmp_path, net, NOW, only="preprint", dry_run=True)
    assert r.dois and "doi" not in get(tmp_path, "arxiv-2501.00001").meta
    assert upd._machine_note("", "  x: 1") == "machine:\n  x: 1\n"
    assert upd._machine_note("a\nmachine:\n  k: v\nrest", "  x: 1") == "a\nmachine:\n  k: v\n  x: 1\nrest"
    assert upd._machine_note("a\nmachine:", "  x: 1") == "a\nmachine:\n  x: 1"


def test_preprint_errors_stop_and_cap(tmp_path, monkeypatch):
    repo(tmp_path, [])
    preprint(tmp_path, "a", url="https://example.org/a")
    preprint(tmp_path, "b", url="https://example.org/b")
    r = upd.update(tmp_path, FakeNet({}), NOW, only="preprint")
    assert r.code == 1 and r.skipped == ["b"] and "preprint a" in r.errors[0]
    monkeypatch.setattr(upd, "OPENALEX_CAP", 1)
    net = FakeNet({f"{WORK}?search={quote('Title a')}&per-page=5&{SELECT}": Response(200, "", b'{"results": []}')})
    preprint(tmp_path, "a", url="https://example.org/a", title="Title a")
    r = upd.update(tmp_path, net, NOW, only="preprint")
    assert r.skipped == ["b"] and r.code == 0
    net.answers[f"{WORK}?search={quote('Title b')}&per-page=5&{SELECT}"] = Response(200, "", b'{"results": []}')
    preprint(tmp_path, "b", url="https://example.org/b", title="Title b")
    r = upd.update(tmp_path, net, NOW, only="preprint")
    assert r.skipped == ["a"] and state(tmp_path)["preprints"] == {"a": "2026-11-11", "b": "2026-11-11"}


def test_openalex_bad_answers(tmp_path):
    repo(tmp_path, [])
    preprint(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "state.json").write_text("[")
    r = upd.update(tmp_path, FakeNet({ARXIV_URL: Response(429, "", b"")}), NOW, only="preprint")
    assert "unreadable" in r.errors[0] and "budget exhausted" in r.errors[1]
    r = upd.update(tmp_path, FakeNet({ARXIV_URL: Response(200, "", b"<html>")}), NOW, only="preprint")
    assert "invalid JSON" in r.errors[0]


def test_openalex_429_one_error_and_key_hidden(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "k1")
    repo(tmp_path, [])
    preprint(tmp_path)
    net = FakeNet({ARXIV_URL + "&api_key=k1": Response(429, "", b"")})
    r = upd.update(tmp_path, net, NOW, only="preprint")
    assert len(r.errors) == 1 and "budget exhausted" in r.errors[0]
    assert "k1" not in " ".join(r.errors) and all("api_key=k1" in u for u in net.requests_made)


def linkrepo(tmp_path):
    repo(tmp_path, [])
    fiche(tmp_path, "ok", url="https://a.example/ok")
    fiche(tmp_path, "dead", url="https://a.example/dead", status="used")
    fiche(tmp_path, "local", origin="kb add (local)")
    fiche(tmp_path, "seen", url="https://a.example/seen", status="seen")
    fiche(tmp_path, "secret", url="https://a.example/secret", confidential="yes")


def state(tmp_path):
    return json.loads((tmp_path / "data" / "state.json").read_text())


def test_links_two_failures_in_a_row_remove(tmp_path):
    linkrepo(tmp_path)
    net = HeadNet({"https://a.example/ok": Response(200, "", b""), "https://a.example/dead": Response(404, "", b"")},
                  {"https://a.example/ok": Response(200, "", b""), "https://a.example/dead": Response(404, "", b"")})
    r = upd.update(tmp_path, net, NOW, only="links")
    assert r.failing == [("dead", "HTTP 404")] and r.removed == [] and r.code == 0
    assert state(tmp_path)["links"]["dead"]["failures"] == 1
    assert state(tmp_path)["links"]["ok"]["failures"] == 0
    assert get(tmp_path, "dead").meta["status"] == "used"
    r = upd.update(tmp_path, net, NOW, only="links")
    assert r.removed == [("dead", "HTTP 404")]
    assert get(tmp_path, "dead").meta["status"] == "removed"
    assert (tmp_path / "index" / "index.json").exists()


def test_links_success_resets_counter_and_state_kept(tmp_path):
    linkrepo(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "state.json").write_text(json.dumps({"dora": {"last_ok": "x"}, "links": {"dead": {"failures": 1}}}))
    net = FakeNet({"https://a.example/ok": Response(200, "", b""), "https://a.example/dead": Response(200, "", b"")})
    r = upd.update(tmp_path, net, NOW, only="links")
    assert r.code == 0 and r.failing == [] and state(tmp_path)["links"]["dead"]["failures"] == 0
    assert state(tmp_path)["dora"] == {"last_ok": "x"}


def test_links_network_error_head_fallback_and_inconclusive(tmp_path):
    linkrepo(tmp_path)
    net = HeadNet({"https://a.example/ok": Response(200, "", b""), "https://a.example/dead": NetError("robots.txt forbids x")},
                  {"https://a.example/ok": Response(405, "", b""), "https://a.example/dead": Response(403, "", b"")})
    r = upd.update(tmp_path, net, NOW, only="links")
    assert r.failing == [] and r.removed == [] and state(tmp_path)["links"]["ok"]["failures"] == 0
    assert "dead" not in state(tmp_path)["links"]
    net = HeadNet({"https://a.example/ok": Response(200, "", b""), "https://a.example/dead": Response(429, "", b"")},
                  {"https://a.example/ok": NetError("x"), "https://a.example/dead": Response(500, "", b"")})
    r = upd.update(tmp_path, net, NOW, only="links")
    assert r.failing == [] and "dead" not in state(tmp_path)["links"]
    r = upd.update(tmp_path, FakeNet({}), NOW, only="links")
    assert {f[0] for f in r.failing} == {"ok", "dead"} and r.code == 0


def test_links_refused_private_address_is_inconclusive(tmp_path):
    linkrepo(tmp_path)
    msg = "refusing non-public address 0.0.0.0 for a.example (set KB_ALLOW_PRIVATE=1 to allow)"
    net = FakeNet({"https://a.example/ok": NetError(msg), "https://a.example/dead": NetError(msg)})
    for _ in range(3):
        r = upd.update(tmp_path, net, NOW, only="links")
        assert r.failing == [] and r.removed == [] and r.errors == []
    assert state(tmp_path)["links"] == {}
    assert get(tmp_path, "dead").meta["status"] == "used"


def test_links_outage_guard(tmp_path):
    repo(tmp_path, [])
    urls = {}
    for i in range(6):
        fiche(tmp_path, f"f{i}", url=f"https://a.example/{i}")
        urls[f"https://a.example/{i}"] = NetError("timed out")
    urls["https://a.example/5"] = Response(404, "", b"")
    urls["https://a.example/4"] = Response(200, "", b"")
    for _ in range(2):
        r = upd.update(tmp_path, FakeNet(urls), NOW, only="links")
    assert len(r.errors) == 1 and "4 of 6 probes failed with a network error" in r.errors[0]
    assert [x[0] for x in r.removed] == ["f5"]
    assert get(tmp_path, "f5").meta["status"] == "removed"
    for i in range(4):
        assert get(tmp_path, f"f{i}").meta["status"] == "new"
        assert f"f{i}" not in state(tmp_path)["links"]
    assert state(tmp_path)["links"]["f4"]["failures"] == 0


def test_links_few_probes_no_outage_guard(tmp_path):
    repo(tmp_path, [])
    for i in range(4):
        fiche(tmp_path, f"f{i}", url=f"https://a.example/{i}")
    net = FakeNet({f"https://a.example/{i}": NetError("timed out") for i in range(4)})
    upd.update(tmp_path, net, NOW, only="links")
    r = upd.update(tmp_path, net, NOW, only="links")
    assert len(r.removed) == 4 and r.errors == []


def test_links_dry_run_cap_and_state_error(tmp_path, monkeypatch):
    linkrepo(tmp_path)
    net = FakeNet({"https://a.example/ok": Response(404, "", b""), "https://a.example/dead": Response(404, "", b"")})
    r = upd.update(tmp_path, net, NOW, only="links", dry_run=True)
    assert len(r.failing) == 2 and not (tmp_path / "data" / "state.json").exists()
    monkeypatch.setattr(upd, "LINK_CAP", 1)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "state.json").write_text("{broken")
    r = upd.update(tmp_path, net, NOW, only="links")
    assert r.skipped == ["ok"] and r.code == 1 and "unreadable" in r.errors[0]
    assert (tmp_path / "data" / "state.json.broken").exists()


def test_update_all_parts_and_lock(tmp_path, mini_pdf):
    repo(tmp_path)
    fiche(tmp_path, "url-old", url=OLD, year=2024)
    net = edition_net(mini_pdf)
    net.answers[OLD] = Response(200, "", b"")
    net.answers[NEW] = Response(200, "application/pdf", mini_pdf)
    r = upd.update(tmp_path, net, NOW)
    assert r.code == 0 and len(r.editions) == 1
    (tmp_path / "data").mkdir(exist_ok=True)
    with (tmp_path / "data" / "run.lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        busy = upd.update(tmp_path, net, NOW)
    assert busy.busy and busy.code == 1 and "skipped: lock" in log(tmp_path)


def test_index_errors_are_reported(tmp_path):
    repo(tmp_path, [])
    (tmp_path / "fiches" / "bad.md").write_text("---\nid: bad\n---\n")
    r = upd.update(tmp_path, FakeNet({}), NOW, only="links")
    assert r.code == 1 and "required field" in r.errors[0]


def test_cli_update(tmp_path, mini_pdf, capsys, monkeypatch):
    repo(tmp_path)
    fiche(tmp_path, "url-old", url=OLD, year=2024)
    monkeypatch.setattr(cli, "make_net", lambda: edition_net(mini_pdf))
    assert cli.main(["--root", str(tmp_path), "update", "--dry-run", "--only", "edition"]) == 0
    assert f"edition candidate (dora): {NEW} supersedes url-old" in capsys.readouterr().out
    assert cli.main(["--root", str(tmp_path), "update", "--only", "edition"]) == 0
    assert "new edition:" in capsys.readouterr().out

    preprint(tmp_path)
    monkeypatch.setattr(cli, "make_net", lambda: FakeNet({ARXIV_URL: js("openalex-published.json")}))
    assert cli.main(["--root", str(tmp_path), "update", "--only", "preprint"]) == 0
    assert "published: arxiv-2501.00001 doi 10.1145" in capsys.readouterr().out


def test_cli_update_reports_failures(tmp_path, capsys, monkeypatch):
    linkrepo(tmp_path)
    monkeypatch.setattr(cli, "make_net", lambda: FakeNet({"https://a.example/ok": Response(404, "", b""),
                                                         "https://a.example/dead": Response(404, "", b"")}))
    assert cli.main(["--root", str(tmp_path), "update", "--only", "links"]) == 0
    assert "link failing: dead" in capsys.readouterr().out
    assert cli.main(["--root", str(tmp_path), "update", "--only", "links"]) == 0
    assert "link removed: dead" in capsys.readouterr().out
    monkeypatch.setattr(upd, "LINK_CAP", 1)
    (tmp_path / "sources.yaml").write_text("sources: [{id: x}]")
    assert cli.main(["--root", str(tmp_path), "update", "--only", "edition"]) == 1
    assert "error: invalid source" in capsys.readouterr().out
    (tmp_path / "fiches" / "broken.md").write_text("no frontmatter")
    assert cli.main(["--root", str(tmp_path), "update", "--only", "links"]) == 1
