import pytest

from sourceshelf.net import Net, NetError


class Resp:
    def __init__(self, status, ctype, body, headers=None):
        self.status, self.body = status, body
        self.headers = {"Content-Type": ctype, **(headers or {})}

    def read(self, n=-1):
        return self.body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make(routes, sleep_fn=None, clock=None):
    log = []

    def opener(req, timeout=0):
        log.append((req.full_url, req.get_header("User-agent")))
        r = routes[req.full_url]
        if isinstance(r, Exception):
            raise r
        return r

    n = Net(opener=opener, sleep_fn=sleep_fn or (lambda s: None), clock=clock or (lambda: 0.0))
    return n, log


def test_robots_disallow_blocks():
    n, log = make({
        "https://x.org/robots.txt": Resp(200, "text/plain", b"User-agent: *\nDisallow: /private\n"),
        "https://x.org/private/a": Resp(200, "text/html", b"secret"),
    })
    with pytest.raises(NetError, match="robots"):
        n("https://x.org/private/a")
    assert all(u.endswith("robots.txt") for u, _ in log)


def test_robots_missing_is_allowed_and_user_agent():
    from urllib.error import HTTPError

    n, log = make({
        "https://x.org/robots.txt": HTTPError("https://x.org/robots.txt", 404, "nf", {}, None),
        "https://x.org/a": Resp(200, "text/html", b"ok"),
    })
    resp = n("https://x.org/a")
    assert resp.status == 200 and resp.content == b"ok"
    assert log[-1][1].startswith("sourceshelf/")


def test_http_error_becomes_response_with_status():
    from urllib.error import HTTPError

    n, _ = make({
        "https://x.org/robots.txt": Resp(404, "text/plain", b""),
        "https://x.org/a": HTTPError("https://x.org/a", 403, "forbidden", {}, None),
    })
    assert n("https://x.org/a").status == 403


def test_throttle_arxiv_3_seconds():
    sleeping, time_ = [], [100.0]
    n, _ = make({
        "https://export.arxiv.org/robots.txt": Resp(404, "text/plain", b""),
        "https://export.arxiv.org/api/query?id_list=1": Resp(200, "text/xml", b"a"),
        "https://export.arxiv.org/api/query?id_list=2": Resp(200, "text/xml", b"b"),
    }, sleep_fn=lambda s: sleeping.append(s), clock=lambda: time_[0])
    n("https://export.arxiv.org/api/query?id_list=1")
    time_[0] = 101.0
    n("https://export.arxiv.org/api/query?id_list=2")
    assert sleeping and 1.9 <= sleeping[-1] <= 2.1


def test_network_error_becomes_net_error():
    from urllib.error import URLError

    n, _ = make({
        "https://x.org/robots.txt": Resp(404, "text/plain", b""),
        "https://x.org/a": URLError("gone"),
    })
    with pytest.raises(NetError):
        n("https://x.org/a")


def test_arxiv_api_exempt_from_robots_but_page_not():
    n, log = make({
        "https://export.arxiv.org/api/query?id_list=1": Resp(200, "text/xml", b"a"),
        "https://arxiv.org/robots.txt": Resp(200, "text/plain", b"User-agent: *\nDisallow: /e-print\nAllow: /pdf\n"),
        "https://arxiv.org/e-print/1": Resp(200, "x", b"b"),
        "https://arxiv.org/pdf/1": Resp(200, "application/pdf", b"c"),
    })
    assert n("https://export.arxiv.org/api/query?id_list=1").status == 200
    assert all("robots" not in u for u, _ in log)
    with pytest.raises(NetError, match="robots"):
        n("https://arxiv.org/e-print/1")
    assert n("https://arxiv.org/pdf/1").status == 200


def test_embedded_private_ipv6_forms_are_refused(monkeypatch):
    import socket

    from sourceshelf.net import check_url

    monkeypatch.delenv("KB_ALLOW_PRIVATE", raising=False)
    for addr in ("::ffff:127.0.0.1", "64:ff9b::a00:1", "2002:7f00:1::1"):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, _x=addr, **k: [(0, 0, 0, "", (_x, 443, 0, 0))])
        with pytest.raises(NetError):
            check_url("https://example.org/")


def test_redirect_to_other_host_drops_authorization(monkeypatch):
    import urllib.request

    from sourceshelf import net

    monkeypatch.setattr(net, "check_url", lambda url: None)
    req = urllib.request.Request("https://a.example/x", data=b"{}", headers={"Authorization": "Bearer k"})
    new = net._CheckedRedirect().redirect_request(req, None, 302, "x", {}, "https://b.example/y")
    assert "Authorization" not in {k.title() for k in new.headers} | {k.title() for k in new.unredirected_hdrs}
    same = net._CheckedRedirect().redirect_request(req, None, 302, "x", {}, "https://a.example/z")
    assert "Authorization" in {k.title() for k in same.headers}
