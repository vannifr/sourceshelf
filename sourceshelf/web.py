import base64
import hashlib
import json
import re
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sourceshelf.fiche import load_fiches
from sourceshelf.run import SourcesError, _state, load_sources
from sourceshelf.search import search

PAGE_FILE = Path(__file__).with_name("web.html")
MAX_QUERY = 200
MAX_LIMIT = 50


def _csp(page: str) -> str:
    def digest(tag: str) -> str:
        hashes = [
            "'sha256-" + base64.b64encode(hashlib.sha256(m.encode("utf-8")).digest()).decode() + "'"
            for m in re.findall(rf"<{tag}>(.*?)</{tag}>", page, re.DOTALL)
        ]
        return " ".join(hashes) or "'none'"
    return (f"default-src 'none'; style-src {digest('style')}; script-src {digest('script')}; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _http_url(value) -> str:
    s = str(value or "")
    return s if s.startswith(("http://", "https://")) else ""


def _host(url: str) -> str:
    return urlparse(url).hostname or "" if url else ""


def sources_data(root: Path) -> dict:
    state = _state(root)
    fiches = load_fiches(root / "fiches")
    error = None
    try:
        sources = load_sources(root)
    except SourcesError as e:
        sources, error = [], str(e)
    configured = []
    for b in sources:
        s = state.get(b.id, {})
        configured.append({
            "id": b.id, "kind": b.kind, "cadence_days": b.cadence_days, "query": b.query,
            "urls": [_http_url(u) for u in b.urls if _http_url(u)],
            "last_ok": s.get("last_ok"), "last_error": s.get("last_error"),
            "error": s.get("error"),
        })
    domains = Counter(_host(_http_url(f.meta.get("url"))) or "local (no URL)" for f in fiches)
    return {
        "error": error,
        "fiche_count": len(fiches),
        "error_count": sum(1 for f in fiches if f.meta.get("error")),
        "unverified_count": sum(1 for f in fiches if isinstance(f.meta.get("machine-summary"), dict)
                                   and str(f.meta["machine-summary"].get("verified", "")) != "yes"),
        "configured": configured,
        "domains": [{"domain": d, "count": n} for d, n in sorted(domains.items(), key=lambda x: (-x[1], x[0]))],
    }


def search_data(root: Path, query: str, limit: int, fields: bool = False, kind: str | None = None) -> dict:
    query = query.strip()[:MAX_QUERY]
    by_id = {str(f.meta["id"]): f.meta for f in load_fiches(root / "fiches")}
    out = []
    for t in search(root, query, limit, fields=fields, kind=kind):
        m = by_id.get(t.id, {})
        url = _http_url(m.get("url"))
        out.append({
            "id": t.id, "title": t.title, "score": round(t.score, 2), "path": t.path,
            "page": t.page, "line": t.line, "snippet": t.snippet,
            "url": url, "domain": _host(url), "authors": m.get("authors"), "year": m.get("year"),
            "origin": m.get("origin"), "status": m.get("status"),
            "verification": m.get("verification"), "fetched": m.get("fetched"),
            "fiche": f"fiches/{t.id}.md",
        })
    return {"query": query, "results": out}


def make_handler(root: Path):
    csp = _csp(PAGE_FILE.read_text(encoding="utf-8"))

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, kind: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _host_allowed(self) -> bool:
            port = self.server.server_port
            if self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return True
            self._json(403, {"error": "host not allowed"})
            return False

        def do_GET(self):
            if not self._host_allowed():
                return
            url = urlparse(self.path)
            try:
                if url.path == "/":
                    self._send(200, PAGE_FILE.read_bytes(), "text/html; charset=utf-8")
                elif url.path == "/api/sources":
                    self._json(200, sources_data(root))
                elif url.path == "/api/search":
                    q = parse_qs(url.query)
                    query = (q.get("q") or [""])[0]
                    try:
                        limit = min(max(int((q.get("limit") or ["10"])[0]), 1), MAX_LIMIT)
                    except ValueError:
                        limit = 10
                    kind = (q.get("kind") or [""])[0]
                    self._json(200, search_data(root, query, limit, fields=(q.get("fields") or [""])[0] == "1",
                                              kind=kind if kind in ("own", "external") else None))
                else:
                    self._json(404, {"error": "not found"})
            except Exception:  # noqa: BLE001
                self._json(500, {"error": "internal error"})

        def _not_allowed(self):
            if not self._host_allowed():
                return
            self._json(405, {"error": "GET only, the interface is read-only"})

        do_POST = do_PUT = do_DELETE = do_PATCH = _not_allowed

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(root: Path, port: int = 8765) -> int:
    server = HTTPServer(("127.0.0.1", port), make_handler(root))
    print(f"sourceshelf at http://127.0.0.1:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
