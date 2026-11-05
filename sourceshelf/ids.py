import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ARXIV_NEW = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?$")
ARXIV_OLD = re.compile(r"arxiv\.org/(?:abs|pdf)/([a-z-]+(?:\.[A-Z]{2})?)/(\d{7})(?:v\d+)?(?:\.pdf)?/?$")
TRACKING = ("utm_", "fbclid", "gclid", "mc_")


def arxiv_id(url: str) -> str | None:
    clean = url.split("?")[0].split("#")[0]
    if m := ARXIV_NEW.search(clean):
        return m.group(1)
    if m := ARXIV_OLD.search(clean):
        return f"{m.group(1)}-{m.group(2)}"
    return None


def normalize_url(url: str) -> str:
    d = urlsplit(url.strip())
    query = sorted((k, v) for k, v in parse_qsl(d.query) if not k.lower().startswith(TRACKING))
    path = d.path.rstrip("/") or ""
    return urlunsplit((d.scheme.lower(), d.netloc.lower(), path, urlencode(query), ""))


def doi_slug(doi: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", doi.lower().strip()).strip("-")


def id_for(url: str, doi: str | None = None) -> str:
    if a := arxiv_id(url):
        return f"arxiv-{a}"
    if doi:
        return f"doi-{doi_slug(doi)}"
    return "url-" + hashlib.sha256(normalize_url(url).encode()).hexdigest()[:12]
