import hashlib
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sourceshelf.extract import run_tool
from sourceshelf.fiche import load_fiches
from sourceshelf.paths import data_path

TOKEN = re.compile(r"[a-z0-9À-ɏ]{3,}")
K1, B = 1.2, 0.75
CACHE_VERSION = 3
OWN_ORIGINS = ("kb add (local)", "migration-source-material")
RRF_K = 60


@dataclass
class Hit:
    id: str
    title: str
    score: float
    path: str
    page: int | None
    line: int | None
    snippet: str


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.lower())


def _name(meta: dict) -> str:
    parts = [str(meta.get("id", "")), " ".join(meta.get("e-ids", [])), str(meta.get("legacy-path", "")),
             str(meta.get("file", "")), str(meta.get("text", ""))]
    return " ".join(parts).replace("_", " ").replace("-", " ")


def _head(meta: dict) -> str:
    summ = meta.get("machine-summary")
    text = summ.get("text", "") if isinstance(summ, dict) and not _is_confidential(meta) else ""
    return f"{meta.get('title', '')} {text}"


def is_own(meta: dict) -> bool:
    return str(meta.get("origin", "")) in OWN_ORIGINS


def _stamp(path: Path) -> list[int]:
    if not path.is_file():
        return [0, 0]
    st = path.stat()
    return [st.st_mtime_ns, st.st_size]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""


def _load_cache(path: Path) -> dict:
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cache.get("docs", {}) if cache.get("v") == CACHE_VERSION else {}


def _documents(root: Path) -> tuple[list, dict]:
    cache_path = root / "data" / "logs" / "bm25-cache.json"
    old = _load_cache(cache_path)
    new: dict = {}
    fiches = []
    changed = False
    for f in load_fiches(root / "fiches"):
        fid = str(f.meta["id"])
        text_path = data_path(root, f.meta.get("text"))
        stamp = _stamp(f.path) + (_stamp(text_path) if text_path else [])
        entry = old.get(fid)
        if not entry or entry["sig"] != stamp:
            if _is_confidential(f.meta):
                counts = Counter(tokens(str(f.meta.get("title", ""))))
            else:
                counts = Counter(tokens(_name(f.meta) + " " + _read(f.path) + " " + (_read(text_path) if text_path else "")))
            entry = {"sig": stamp, "c": dict(counts), "k": dict(Counter(tokens(_head(f.meta))))}
            changed = True
        new[fid] = entry
        fiches.append(f)
    if changed or set(old) != set(new):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"v": CACHE_VERSION, "docs": new}), encoding="utf-8")
        tmp.replace(cache_path)
    return fiches, new


def _location(root: Path, fiche, terms: set[str]) -> tuple[str, int | None, int | None, str]:
    if _is_confidential(fiche.meta):
        return f"fiches/{fiche.path.name}", None, None, ""
    text_path = data_path(root, fiche.meta.get("text"))
    if text_path and text_path.is_file():
        path, content = f"data/{fiche.meta['text']}", _read(text_path)
    else:
        path, content = f"fiches/{fiche.path.name}", _read(fiche.path)
    pages = content.split("\f")
    best_page, best_n = None, 0
    if len(pages) > 1:
        for i, p in enumerate(pages, 1):
            n = sum(1 for t in tokens(p) if t in terms)
            if n > best_n:
                best_page, best_n = i, n
    scope = pages[best_page - 1] if best_page else content
    line_base = sum(p.count("\n") for p in pages[: (best_page or 1) - 1]) + (best_page or 1) - 1
    best_line, best_snippet, best_score = None, "", 0
    for i, line in enumerate(scope.split("\n"), 1):
        n = len(terms & set(tokens(line)))
        if n > best_score:
            best_line, best_snippet, best_score = line_base + i, line.strip()[:160], n
    return path, best_page, best_line, best_snippet


def _bm25(docs: dict, terms: set[str], fields: str) -> dict:
    n = len(docs)
    length = {fid: sum(d[fields].values()) for fid, d in docs.items()}
    avg = sum(length.values()) / n or 1
    df = Counter(w for d in docs.values() for w in terms if w in d[fields])
    scores = {}
    for fid, d in docs.items():
        s = 0.0
        for w in terms:
            tf = d[fields].get(w)
            if tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * tf * (K1 + 1) / (tf + K1 * (1 - B + B * length[fid] / avg))
        if s:
            scores[fid] = s
    return scores


def _rrf(*score_lists: dict) -> dict:
    out: dict = {}
    for sc in score_lists:
        for rank, fid in enumerate(sorted(sc, key=lambda k: (-sc[k], k)), 1):
            out[fid] = out.get(fid, 0.0) + 1 / (RRF_K + rank)
    return out


def search(root: Path, query: str, limit: int = 5, fields: bool = False, kind: str | None = None) -> list[Hit]:
    terms = set(tokens(query))
    if not terms:
        return []
    fiches, docs = _documents(root)
    by_id = {str(f.meta["id"]): f for f in fiches}
    if kind:
        docs = {fid: d for fid, d in docs.items() if is_own(by_id[fid].meta) == (kind == "own")}
    if not docs:
        return []
    scores = _bm25(docs, terms, "c")
    if fields:
        scores = _rrf(scores, _bm25(docs, terms, "k"))
    top = sorted(scores, key=lambda k: (-scores[k], k))[:limit]
    out = []
    for fid in top:
        f = by_id[fid]
        path, page, line, snip = _location(root, f, terms)
        out.append(Hit(fid, str(f.meta["title"]), scores[fid], path, page, line, snip))
    return out


def _is_confidential(meta: dict) -> bool:
    return str(meta.get("confidential", "")).strip().lower() == "yes"


def _digest(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def search_exact(root: Path, query: str) -> str:
    dirs = [str(p) for p in (root / "fiches", root / "data" / "text", root / "data" / "papers") if p.exists()]
    blocked = set()
    inodes = set()
    digests = set()
    for f in load_fiches(root / "fiches"):
        if _is_confidential(f.meta):
            paths = [f.path]
            for key in ("text", "file"):
                rel = f.meta.get(key)
                if rel:
                    paths.append(root / "data" / str(rel))
            for p in paths:
                blocked.add(os.path.realpath(p))
                if os.path.isfile(p):
                    st = os.stat(p)
                    inodes.add((st.st_dev, st.st_ino))
                    digests.add(_digest(p))
    proc = run_tool(["rg", "-n", "-F", "-i", "--null", "--glob", "*.{md,txt}", "--", query, *dirs],
                    capture_output=True, text=True)
    out = []
    for line in proc.stdout.split("\n"):
        name, sep, rest = line.partition("\0")
        if not sep or not os.path.isfile(name) or os.path.realpath(name) in blocked:
            continue
        st = os.stat(name)
        if (st.st_dev, st.st_ino) not in inodes and _digest(name) not in digests:
            out.append(f"{name}:{rest}")
    return "\n".join(out) + "\n" if out else ""


def _legacy_match(meta: dict, s: str) -> bool:
    paths = [meta.get("legacy-path"), *meta.get("legacy-path-extra", [])]
    paths = [p for p in paths if isinstance(p, str)]
    if s.endswith("/"):
        return any(p.startswith(s) for p in paths)
    return s in paths


def resolve(root: Path, key: str) -> list[str]:
    s = key.strip()
    prefix = "evidence/sources/papers/"
    rel = s[len(prefix):] if s.startswith(prefix) else None
    return [str(f.meta["id"]) for f in load_fiches(root / "fiches")
            if str(f.meta["id"]) == s or s in f.meta.get("e-ids", []) or _legacy_match(f.meta, s)
            or rel is not None and f"papers/{rel}" in (f.meta.get("file"), f.meta.get("text"))]