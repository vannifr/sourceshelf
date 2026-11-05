import fcntl
import json
import subprocess
from datetime import UTC, datetime, timedelta

import yaml

from sourceshelf import cli
from sourceshelf.fiche import load_fiches
from sourceshelf.net import NetError, Response
from sourceshelf.run import due, load_sources, run
from tests.conftest import FakeNet

NOW = datetime(2026, 10, 20, 7, 20, tzinfo=UTC)
FEED = "https://export.arxiv.org/api/query?search_query={q}&sortBy=submittedDate&sortOrder=descending&max_results={n}"


def feed_xml(ids):
    entries = "".join(f"<entry><id>http://arxiv.org/abs/{i}v1</id><title>t{i}</title></entry>" for i in ids)
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()


class ArxivNet(FakeNet):
    def __init__(self, ids, mini_pdf, arxiv_xml, query="agentic", n=10, broken=()):
        super().__init__({})
        self.ids, self.pdf, self.xml, self.broken = ids, mini_pdf, arxiv_xml, set(broken)
        self.feed = FEED.format(q=query.replace(" ", "+"), n=n)

    def __call__(self, url):
        self.requests_made.append(url)
        if url == self.feed:
            if "feed" in self.broken:
                raise NetError("feed unreachable")
            return Response(200, "application/atom+xml", feed_xml(self.ids))
        if "id_list=" in url:
            aid = url.rsplit("=", 1)[1]
            return Response(200, "application/atom+xml", self.xml.replace(b"2501.00001", aid.encode()))
        if "arxiv.org/pdf/" in url:
            aid = url.rsplit("/pdf/", 1)[1]
            if aid in self.broken:
                return Response(403, "", b"")
            return Response(200, "application/pdf", self.pdf)
        raise NetError(f"unexpected: {url}")


def make_repo(tmp_path, sources=None):
    sources = sources or [{"id": "arxiv-agentic", "kind": "arxiv-query", "query": "agentic", "max": 10,
                           "cadence_days": 14}]
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump({"sources": sources}), encoding="utf-8")
    for a in (["init", "-q"], ["config", "user.name", "x"], ["config", "user.email", "x@e.nl"]):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("data/\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True, capture_output=True)


def read_file(tmp_path, rel):
    return (tmp_path / rel).read_text(encoding="utf-8")


def test_load_sources_and_due(tmp_path):
    make_repo(tmp_path)
    sources = load_sources(tmp_path)
    assert [b.id for b in sources] == ["arxiv-agentic"] and sources[0].cadence_days == 14
    assert [b.id for b, _ in due(tmp_path, NOW)] == ["arxiv-agentic"]


def test_dedupe_second_run_nothing_new(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    net = ArxivNet(["2501.00001", "2501.00002"], mini_pdf, arxiv_xml)
    r1 = run(tmp_path, net, NOW)
    assert r1.code == 0 and r1.new == 2 and r1.failed == 0
    requests_made = len(net.requests_made)
    r2 = run(tmp_path, net, NOW + timedelta(days=1), source_id="arxiv-agentic")
    assert r2.new == 0 and len(load_fiches(tmp_path / "fiches")) == 2
    assert len(net.requests_made) == requests_made + 1
    assert "0 new" in read_file(tmp_path, "digest/LATEST.md")


def test_not_due_after_success(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    run(tmp_path, ArxivNet(["2501.00001"], mini_pdf, arxiv_xml), NOW)
    assert due(tmp_path, NOW + timedelta(days=13)) == []
    assert [b.id for b, _ in due(tmp_path, NOW + timedelta(days=14))] == ["arxiv-agentic"]
    net = ArxivNet(["2501.00001"], mini_pdf, arxiv_xml)
    assert run(tmp_path, net, NOW + timedelta(days=1)).new == 0 and net.requests_made == []


def test_unreachable_feed_is_visible(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    r = run(tmp_path, ArxivNet([], mini_pdf, arxiv_xml, broken=("feed",)), NOW)
    assert r.code == 0 and r.failed_sources == ["arxiv-agentic"]
    state = json.loads(read_file(tmp_path, "data/state.json"))["arxiv-agentic"]
    assert state["consecutive_errors"] == 1 and state["last_ok"] is None and "feed" in state["error"]
    digest = read_file(tmp_path, "digest/LATEST.md")
    assert "Failed sources" in digest and "arxiv-agentic" in digest
    runs = read_file(tmp_path, "logs/RUNS.md")
    assert "started" in runs and "finished" in runs and "failed_sources=1" in runs
    run(tmp_path, ArxivNet([], mini_pdf, arxiv_xml, broken=("feed",)), NOW + timedelta(days=1), source_id="arxiv-agentic")
    assert json.loads(read_file(tmp_path, "data/state.json"))["arxiv-agentic"]["consecutive_errors"] == 2
    assert "warning" in read_file(tmp_path, "digest/LATEST.md").lower()


def test_failed_download_appears_in_digest(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    net = ArxivNet(["2501.00001", "2501.00002"], mini_pdf, arxiv_xml, broken=("2501.00002",))
    r = run(tmp_path, net, NOW)
    assert r.new == 2 and r.failed == 1
    fiches = {f.meta["id"]: f.meta for f in load_fiches(tmp_path / "fiches")}
    assert "error" in fiches["arxiv-2501.00002"] and "error" not in fiches["arxiv-2501.00001"]
    digest = read_file(tmp_path, "digest/LATEST.md")
    assert "arxiv-2501.00002" in digest and "Failed fetches" in digest


def test_lock_gives_75_and_log_line(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    (tmp_path / "data").mkdir()
    with (tmp_path / "data" / "run.lock").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        net = ArxivNet(["2501.00001"], mini_pdf, arxiv_xml)
        assert run(tmp_path, net, NOW).code == 75
    assert net.requests_made == []
    assert "skipped: lock" in read_file(tmp_path, "logs/RUNS.md")


def test_interrupted_run_at_top_of_digest(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "RUNS.md").write_text("# Runs\n\n- 2026-10-17T07:20:00+00:00 started\n")
    run(tmp_path, ArxivNet(["2501.00001"], mini_pdf, arxiv_xml), NOW)
    digest = read_file(tmp_path, "digest/LATEST.md")
    head = digest.split("\n## ")[1]
    assert head.startswith("Interrupted run") and "2026-10-17" in head
    assert read_file(tmp_path, "logs/RUNS.md").startswith("# Runs\n\n- 2026-10-17T07:20:00+00:00 started\n")


def test_cap_stops_cleanly(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    net = ArxivNet(["2501.00001", "2501.00002", "2501.00003"], mini_pdf, arxiv_xml)
    r = run(tmp_path, net, NOW, cap=2)
    assert r.new == 2 and r.remaining == 1
    assert "cap reached" in read_file(tmp_path, "digest/LATEST.md").lower()
    assert run(tmp_path, net, NOW + timedelta(days=1), source_id="arxiv-agentic", cap=2).new == 1


def test_run_commit_local_without_data(tmp_path, mini_pdf, arxiv_xml, monkeypatch):
    monkeypatch.delenv("KB_GIT_EMAIL", raising=False)
    monkeypatch.delenv("KB_GIT_NAME", raising=False)
    make_repo(tmp_path)
    run(tmp_path, ArxivNet(["2501.00001", "2501.00002"], mini_pdf, arxiv_xml), NOW)
    def g(*a):
        return subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, text=True).stdout

    assert g("log", "-1", "--format=%s") .strip() == "chore(run): 2026-10-20 2 new, 0 failed"
    assert g("log", "-1", "--format=%an <%ae>").strip() == "x <x@e.nl>"
    assert "Co-Authored-By" not in g("log", "-1", "--format=%B")
    tracked = g("ls-files").split()
    assert "digest/LATEST.md" in tracked and "logs/RUNS.md" in tracked and not any(p.startswith("data/") for p in tracked)
    assert g("status", "--porcelain", "--", "fiches", "index", "digest", "logs").strip() == ""


def test_url_list_source(tmp_path, mini_pdf):
    url = "https://example.org/report.pdf"
    make_repo(tmp_path, [{"id": "report", "kind": "url-list", "urls": [url], "cadence_days": 30}])
    net = FakeNet({url: Response(200, "application/pdf", mini_pdf)})
    r = run(tmp_path, net, NOW)
    assert r.new == 1 and r.failed == 0
    assert run(tmp_path, net, NOW + timedelta(days=31)).new == 0


def test_cli_due_and_run(tmp_path, mini_pdf, arxiv_xml, monkeypatch, capsys):
    make_repo(tmp_path)
    monkeypatch.setattr(cli, "make_net", lambda: ArxivNet(["2501.00001"], mini_pdf, arxiv_xml))
    assert cli.main(["--root", str(tmp_path), "due"]) == 0
    assert "arxiv-agentic" in capsys.readouterr().out
    assert cli.main(["--root", str(tmp_path), "run"]) == 0
    assert "1 new" in capsys.readouterr().out
    assert cli.main(["--root", str(tmp_path), "due"]) == 0
    assert "arxiv-agentic" not in capsys.readouterr().out


def test_error_md_appears_in_digest_and_is_removed(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    (tmp_path / "digest").mkdir()
    (tmp_path / "digest" / "FOUT.md").write_text("# Run failed\n\nsegfault in run.log\n")
    assert "FOUT.md" in cli_due_output(tmp_path)
    run(tmp_path, ArxivNet(["2501.00001"], mini_pdf, arxiv_xml), NOW)
    digest = read_file(tmp_path, "digest/LATEST.md")
    assert "Crash reported by systemd" in digest and "segfault in run.log" in digest
    assert not (tmp_path / "digest" / "FOUT.md").exists()


def test_backup_error_gets_own_heading_and_keeps_run_error(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    (tmp_path / "digest").mkdir()
    (tmp_path / "digest" / "FOUT-run.md").write_text("# Failed: run\n\nsegfault in run.log\n")
    (tmp_path / "digest" / "FOUT-backup.md").write_text("# Failed: backup\n\nrestic did not finish\n")
    assert "FOUT-backup.md" in cli_due_output(tmp_path)
    run(tmp_path, ArxivNet(["2501.00001"], mini_pdf, arxiv_xml), NOW)
    digest = read_file(tmp_path, "digest/LATEST.md")
    assert "segfault in run.log" in digest and "restic did not finish" in digest
    assert "# Failed: backup" in digest
    assert not list((tmp_path / "digest").glob("FOUT*.md"))


def cli_due_output(root):
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main(["--root", str(root), "due"])
    return buf.getvalue()


def test_index_error_visible_in_digest_and_runlog(tmp_path, mini_pdf, arxiv_xml):
    make_repo(tmp_path)
    (tmp_path / "fiches").mkdir()
    (tmp_path / "fiches" / "broken.md").write_text("---\nid: broken\n---\n", encoding="utf-8")
    run(tmp_path, ArxivNet(["2501.00001"], mini_pdf, arxiv_xml), NOW, commit=False)
    assert "## Index not updated" in read_file(tmp_path, "digest/LATEST.md")
    assert "index_errors=" in read_file(tmp_path, "logs/RUNS.md").splitlines()[-1]
    assert "index_errors=0" not in read_file(tmp_path, "logs/RUNS.md").splitlines()[-1]


def test_commit_identity_comes_from_environment(tmp_path, mini_pdf, arxiv_xml, monkeypatch):
    monkeypatch.setenv("KB_GIT_EMAIL", "ik@example.org")
    monkeypatch.setenv("KB_GIT_NAME", "me")
    make_repo(tmp_path)
    run(tmp_path, ArxivNet(["2501.00001"], mini_pdf, arxiv_xml), NOW)
    g = subprocess.run(["git", "-C", str(tmp_path), "log", "-1", "--format=%an <%ae>"], check=True,
                       capture_output=True, text=True).stdout.strip()
    assert g == "me <ik@example.org>"


RSS = (b'<rss version="2.0"><channel><item><title>a</title><link>https://news.example/a</link></item>'
       b'<item><title>b</title><link>https://news.example/b</link></item></channel></rss>')
ATOM_FEED = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>c</title>'
            b'<link href="https://news.example/c"/></entry></feed>')


def page(text):
    return Response(200, "text/html", f"<html><title>{text}</title><body>{text}</body></html>".encode())


def feed_repo(tmp_path, url="https://news.example/feed.xml", max_=10):
    make_repo(tmp_path, [{"id": "news", "kind": "feed", "urls": [url], "max": max_, "cadence_days": 3}])


def test_feed_rss_gives_new_items(tmp_path):
    feed_repo(tmp_path)
    net = FakeNet({"https://news.example/feed.xml": Response(200, "application/rss+xml", RSS),
                   "https://news.example/a": page("first"), "https://news.example/b": page("second")})
    r = run(tmp_path, net, NOW)
    assert r.new == 2 and r.failed == 0
    assert {f.meta["url"] for f in load_fiches(tmp_path / "fiches")} == {"https://news.example/a",
                                                                          "https://news.example/b"}
    r2 = run(tmp_path, net, NOW + timedelta(days=4))
    assert r2.new == 0


def test_feed_atom_and_new_item_on_next_run(tmp_path):
    feed_repo(tmp_path)
    net = FakeNet({"https://news.example/feed.xml": Response(200, "application/atom+xml", ATOM_FEED),
                   "https://news.example/c": page("third")})
    assert run(tmp_path, net, NOW).new == 1
    net.answers["https://news.example/feed.xml"] = Response(200, "application/rss+xml", RSS)
    net.answers.update({"https://news.example/a": page("a"), "https://news.example/b": page("b")})
    assert run(tmp_path, net, NOW + timedelta(days=4)).new == 2


def test_feed_respects_max(tmp_path):
    feed_repo(tmp_path, max_=1)
    net = FakeNet({"https://news.example/feed.xml": Response(200, "application/rss+xml", RSS),
                   "https://news.example/a": page("a")})
    r = run(tmp_path, net, NOW)
    assert r.new == 1 and "https://news.example/b" not in net.requests_made


def test_feed_invalid_xml_is_source_failure(tmp_path):
    feed_repo(tmp_path)
    net = FakeNet({"https://news.example/feed.xml": Response(200, "text/xml", b"<rss")})
    r = run(tmp_path, net, NOW)
    assert r.failed_sources == ["news"]


def test_changed_page_appears_in_digest_once(tmp_path):
    make_repo(tmp_path, [{"id": "reg", "kind": "url-list", "urls": ["https://reg.example/wet"],
                          "cadence_days": 7}])
    net = FakeNet({"https://reg.example/wet": page("version one")})
    assert run(tmp_path, net, NOW).new == 1
    assert "Changed" not in read_file(tmp_path, "digest/LATEST.md")
    run(tmp_path, net, NOW + timedelta(days=8))
    assert "Changed" not in read_file(tmp_path, "digest/LATEST.md")
    net.answers["https://reg.example/wet"] = page("version two")
    r = run(tmp_path, net, NOW + timedelta(days=16))
    assert r.new == 0 and r.changed == 1
    digest = read_file(tmp_path, "digest/LATEST.md")
    assert "Changed (1)" in digest and "https://reg.example/wet" in digest
    run(tmp_path, net, NOW + timedelta(days=24))
    assert "Changed" not in read_file(tmp_path, "digest/LATEST.md")


def test_change_not_lost_when_later_url_fails(tmp_path):
    make_repo(tmp_path, [{"id": "reg", "kind": "url-list",
                          "urls": ["https://reg.example/a", "https://reg.example/b"], "cadence_days": 7}])
    net = FakeNet({"https://reg.example/a": page("one"), "https://reg.example/b": page("two")})
    assert run(tmp_path, net, NOW).new == 2
    net.answers["https://reg.example/a"] = page("one changed")
    net.answers["https://reg.example/b"] = Response(503, "", b"")
    r = run(tmp_path, net, NOW + timedelta(days=8))
    assert r.failed_sources == ["reg"]
    net.answers["https://reg.example/b"] = page("two")
    r = run(tmp_path, net, NOW + timedelta(days=9), source_id="reg")
    assert r.changed == 1 and "Changed (1)" in read_file(tmp_path, "digest/LATEST.md")


def test_feed_http_error_is_source_failure(tmp_path):
    feed_repo(tmp_path)
    r = run(tmp_path, FakeNet({"https://news.example/feed.xml": Response(503, "", b"")}), NOW)
    assert r.failed_sources == ["news"]
