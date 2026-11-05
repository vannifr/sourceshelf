import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from sourceshelf.web import make_handler
from tests.test_search import make


@pytest.fixture
def server(tmp_path):
    make(tmp_path, "url-aaa", "Mice <b>x</b>", "rodents",
         {"url": "https://example.org/muis", "authors": "A. Author", "year": 2024}, "line about quokka")
    make(tmp_path, "url-bbb", "Cats", "pets", {"url": "javascript:alert(1)"}, "something else")
    (tmp_path / "sources.yaml").write_text(
        "sources:\n  - id: blog\n    kind: feed\n    urls:\n      - https://example.org/feed.xml\n    cadence_days: 7\n",
        encoding="utf-8")
    srv = HTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read()


def test_page_and_sources(server):
    status, body = get(server + "/")
    assert status == 200 and b"<title>Knowledge base</title>" in body
    data = json.loads(get(server + "/api/sources")[1])
    assert data["fiche_count"] == 2
    assert data["configured"][0]["id"] == "blog"
    assert {"domain": "example.org", "count": 1} in data["domains"]
    assert {"domain": "local (no URL)", "count": 1} in data["domains"]


def test_search_gives_source_attribution(server):
    data = json.loads(get(server + "/api/search?q=quokka")[1])
    t = data["results"][0]
    assert t["id"] == "url-aaa" and t["url"] == "https://example.org/muis"
    assert t["domain"] == "example.org" and t["fiche"] == "fiches/url-aaa.md" and t["year"] == 2024


def test_dangerous_url_is_not_passed_on(server):
    data = json.loads(get(server + "/api/search?q=else")[1])
    assert data["results"][0]["url"] == ""


def test_empty_query_and_get_only(server):
    assert json.loads(get(server + "/api/search?q=")[1])["results"] == []
    req = urllib.request.Request(server + "/api/search", data=b"x", method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 405


def test_search_fields_and_kind_parameters(server):
    data = json.loads(get(server + "/api/search?q=quokka&fields=1&kind=external")[1])
    assert "results" in data
    assert "unverified_count" in json.loads(get(server + "/api/sources")[1])


def _raw(server, host, method="GET", path="/api/sources"):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", int(server.rsplit(":", 1)[1]))
    c.putrequest(method, path, skip_host=True)
    if host is not None:
        c.putheader("Host", host)
    c.endheaders()
    r = c.getresponse()
    return r.status, r.read()


def test_foreign_host_header_gives_403(server):
    port = server.rsplit(":", 1)[1]
    for host in ("evil.example", f"evil.example:{port}", "127.0.0.1", f"127.0.0.1.evil.example:{port}", None,
                 f"LOCALHOST:{port}", f"localhost.:{port}", f"[::1]:{port}", f"user@localhost:{port}"):
        status, body = _raw(server, host)
        assert status == 403 and b"quokka" not in body and b"Mice" not in body
    assert _raw(server, f"localhost:{port}")[0] == 200
    assert _raw(server, f"127.0.0.1:{port}")[0] == 200
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert _raw(server, "evil.example", method)[0] == 403
        assert _raw(server, f"127.0.0.1:{port}", method)[0] == 405


def test_500_shows_no_exception_text(server, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-path /srv/x/title")
    monkeypatch.setattr("sourceshelf.web.sources_data", boom)
    status, body = _raw(server, f"127.0.0.1:{server.rsplit(':', 1)[1]}")
    assert status == 500 and b"secret" not in body and json.loads(body) == {"error": "internal error"}


def test_csp_has_no_unsafe_inline_and_matches_page_hashes(server):
    import base64
    import hashlib
    import re

    from sourceshelf.web import PAGE_FILE

    with urllib.request.urlopen(server + "/") as r:
        csp = r.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    page = PAGE_FILE.read_text(encoding="utf-8")
    for tag in ("script", "style"):
        for body in re.findall(rf"<{tag}>(.*?)</{tag}>", page, re.DOTALL):
            assert "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'" in csp
