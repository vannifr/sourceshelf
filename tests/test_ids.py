import pytest

from sourceshelf.ids import arxiv_id, id_for, normalize_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://arxiv.org/abs/2501.00001v2", "2501.00001"),
        ("http://arxiv.org/pdf/2501.00001.pdf", "2501.00001"),
        ("https://arxiv.org/abs/cs/0101001", "cs-0101001"),
        ("https://example.org/abs/2501.00001", None),
    ],
)
def test_arxiv_id(url, expected):
    assert arxiv_id(url) == expected


def test_normalize_url():
    a = normalize_url("HTTPS://Example.org/path/?utm_source=x&b=2&a=1#frag")
    b = normalize_url("https://example.org/path?a=1&b=2")
    assert a == b


def test_id_arxiv_beats_doi():
    assert id_for("https://arxiv.org/abs/2501.00001v2", "10.1000/xyz") == "arxiv-2501.00001"


def test_id_doi():
    assert id_for("https://example.org/x", "10.1000/XYZ.123") == "doi-10.1000-xyz.123"


def test_id_url_hash_stable():
    a = id_for("https://example.org/x?utm_source=a")
    assert a == id_for("https://EXAMPLE.org/x")
    assert a.startswith("url-") and len(a) == 16
