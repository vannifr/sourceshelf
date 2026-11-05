import io
import urllib.request
import zipfile

import pytest

from sourceshelf import net as netmod
from sourceshelf.describe import ModelError, check_endpoint, openrouter_model
from sourceshelf.extract import docx_to_text
from sourceshelf.net import Net, NetError, check_url
from tests.test_net import Resp
from tests.test_search import make


def test_scheme_allowlist():
    for url in ("file:///etc/hosts", "ftp://example.org/x", "gopher://x", "data:text/plain,hi", "http:///nohost"):
        with pytest.raises(NetError, match="http and https"):
            check_url(url)


def test_private_addresses_refused(monkeypatch):
    monkeypatch.delenv("KB_ALLOW_PRIVATE", raising=False)
    for url in ("http://127.0.0.1/", "http://169.254.169.254/x", "http://10.0.0.5/x", "http://[::1]/", "http://192.168.1.1/"):
        with pytest.raises(NetError, match="non-public"):
            check_url(url)


def test_private_allowed_by_flag(monkeypatch):
    monkeypatch.setenv("KB_ALLOW_PRIVATE", "1")
    check_url("http://127.0.0.1/")


def test_public_address_passes(monkeypatch):
    monkeypatch.delenv("KB_ALLOW_PRIVATE", raising=False)
    monkeypatch.setattr(netmod.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    check_url("https://example.org/x")


def test_unresolvable_host_is_net_error(monkeypatch):
    monkeypatch.delenv("KB_ALLOW_PRIVATE", raising=False)

    def boom(*a, **k):
        raise OSError("no dns")

    monkeypatch.setattr(netmod.socket, "getaddrinfo", boom)
    with pytest.raises(NetError, match="cannot resolve"):
        check_url("https://nowhere.invalid/")


def test_redirect_to_private_address_refused(monkeypatch):
    monkeypatch.delenv("KB_ALLOW_PRIVATE", raising=False)
    handler = netmod._CheckedRedirect()
    req = urllib.request.Request("https://example.org/a")
    with pytest.raises(NetError, match="non-public"):
        handler.redirect_request(req, None, 302, "Found", {}, "http://169.254.169.254/meta")
    with pytest.raises(NetError, match="http and https"):
        handler.redirect_request(req, None, 302, "Found", {}, "file:///etc/hosts")


def test_redirect_to_public_passes(monkeypatch):
    monkeypatch.setattr(netmod.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    handler = netmod._CheckedRedirect()
    req = urllib.request.Request("https://example.org/a")
    assert handler.redirect_request(req, None, 302, "Found", {}, "https://example.org/b").full_url == "https://example.org/b"


def test_safe_open_checks_before_connecting():
    with pytest.raises(NetError):
        netmod.safe_open(urllib.request.Request("file:///etc/hosts"))


def test_oversized_download_is_error(monkeypatch):
    monkeypatch.setattr(netmod, "MAX_BYTES", 10)
    n = Net(opener=lambda req, timeout=0: Resp(200, "text/html", b"x" * 11), sleep_fn=lambda s: None)
    with pytest.raises(NetError, match="larger than"):
        n._fetch("https://x.org/big")


def test_endpoint_requires_https_except_localhost():
    check_endpoint("https://openrouter.ai/api")
    check_endpoint("http://localhost:11434/v1")
    check_endpoint("http://127.0.0.1:8080/v1")
    for url in ("http://evil.example/v1", "ftp://x/y", "file:///x", "https:///x"):
        with pytest.raises(ModelError, match="https"):
            check_endpoint(url)


def test_credential_not_sent_to_http_endpoint(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setenv("KB_ENDPOINT", "http://evil.example/v1")
    called = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: called.append(a))
    with pytest.raises(ModelError, match="https"):
        openrouter_model()("s", "t")
    assert called == []


def test_text_path_outside_data_is_ignored(tmp_path):
    from sourceshelf.paths import data_path
    (tmp_path / "data").mkdir()
    (tmp_path / "outside.txt").write_text("buiten data", encoding="utf-8")
    assert data_path(tmp_path, "../outside.txt") is None
    assert data_path(tmp_path, "/etc/hosts") is None
    assert data_path(tmp_path, None) is None
    assert data_path(tmp_path, "text/a.txt") == (tmp_path / "data" / "text" / "a.txt").resolve()


def test_search_does_not_read_text_outside_data(tmp_path):
    from sourceshelf.search import search
    (tmp_path / "outside.txt").write_text("giraffe", encoding="utf-8")
    make(tmp_path, "url-x", "Title", "content", {"text": "../outside.txt"})
    assert search(tmp_path, "giraffe") == []


def _docx(xml: bytes, tmp_path):
    path = tmp_path / "a.docx"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    path.write_bytes(buf.getvalue())
    return path


def test_docx_with_entities_refused(tmp_path):
    xml = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
           b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    with pytest.raises(ValueError, match="DTD"):
        docx_to_text(_docx(xml, tmp_path))


def test_docx_too_large_refused(tmp_path, monkeypatch):
    monkeypatch.setattr("sourceshelf.extract.MAX_DOCX_XML", 10)
    with pytest.raises(ValueError, match="too large"):
        docx_to_text(_docx(b"<a>" + b"x" * 100 + b"</a>", tmp_path))


def test_missing_root_is_an_error(tmp_path, capsys):
    from sourceshelf import cli
    for cmd in ("check", "index", "due"):
        assert cli.main(["--root", str(tmp_path / "nope"), cmd]) == 2
    assert "library folder not found" in capsys.readouterr().err


def test_empty_search_says_so_on_stderr(tmp_path, capsys):
    from sourceshelf import cli
    (tmp_path / "fiches").mkdir()
    assert cli.main(["--root", str(tmp_path), "search", "zzzz"]) == 1
    cap = capsys.readouterr()
    assert cap.out == "" and "no results" in cap.err
