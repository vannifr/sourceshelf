import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sourceshelf.extract import (
    MissingTool,
    audio_to_text,
    docx_to_text,
    html_title,
    html_to_text,
    pdf_to_text,
    subtitle_to_text,
)
from sourceshelf.fiche import FicheError, load_fiches, write_fiche
from sourceshelf.ids import arxiv_id, id_for, normalize_url
from sourceshelf.net import NetError, Response

WEB_EXT = {".pdf", ".html", ".htm"}
TEXT_EXT = {".txt", ".md"}
SUBTITLE_EXT = {".vtt", ".srt"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus"}
FILE_EXT = TEXT_EXT | SUBTITLE_EXT | AUDIO_EXT | {".docx"}
CITATION = "  citation-value: only after verification against the source"
MACHINE = {
    "docx": "  extraction: docx to text",
    "text": "  input: local text file, unchanged",
    "subtitle": "  extraction: subtitle file to text, timestamps removed",
    "audio": "  transcription: faster-whisper small int8",
}

ATOM = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


class UnknownType(Exception):
    pass


@dataclass
class Result:
    id: str
    new: bool
    error: str | None = None


def _arxiv_path(aid: str) -> str:
    return aid.replace("-", "/", 1) if "-" in aid else aid


def _find_existing(root: Path, url: str, doi: str | None):
    target = normalize_url(url)
    for f in load_fiches(root / "fiches"):
        fu = f.meta.get("url")
        if (fu and normalize_url(str(fu)) == target) or (doi and f.meta.get("doi") == doi):
            return f
    return None


def _arxiv_metadata(net, aid: str) -> dict:
    resp = net(f"https://export.arxiv.org/api/query?id_list={_arxiv_path(aid)}")
    if resp.status != 200:
        raise NetError(f"arXiv API returned {resp.status}")
    try:
        entry = ET.fromstring(resp.content).find("a:entry", ATOM)
    except ET.ParseError as error:
        raise NetError(f"arXiv API returned invalid XML: {error}") from error
    if entry is None or entry.findtext("a:title", namespaces=ATOM) is None:
        raise NetError(f"arXiv does not know {aid}")
    def squash(t):
        return re.sub(r"\s+", " ", t or "").strip()

    return {
        "title": squash(entry.findtext("a:title", namespaces=ATOM)),
        "authors": ", ".join(squash(a.findtext("a:name", namespaces=ATOM)) for a in entry.findall("a:author", ATOM)),
        "year": int(entry.findtext("a:published", namespaces=ATOM)[:4]),
        "doi": entry.findtext("x:doi", namespaces=ATOM),
        "abstract": squash(entry.findtext("a:summary", namespaces=ATOM)),
    }


def _store(root: Path, fid: str, resp, url: str, meta: dict) -> None:
    data = root / "data"
    pdf = "pdf" in resp.content_type.lower() or resp.content[:5] == b"%PDF-"
    ext, kind_dir = ("pdf", "pdf") if pdf else ("html", "html")
    source = data / kind_dir / f"{fid}.{ext}"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(resp.content)
    meta["file"] = f"{kind_dir}/{fid}.{ext}"
    meta["sha256"] = hashlib.sha256(resp.content).hexdigest()
    target = data / "text" / f"{fid}.txt"
    if pdf:
        try:
            has_text = pdf_to_text(source, target)
        except MissingTool as error:
            raise NetError(str(error)) from error
    else:
        text = resp.content.decode("utf-8", errors="ignore")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html_to_text(text), encoding="utf-8")
        has_text = True
        if "title" not in meta:
            meta["title"] = html_title(text) or urlsplit(url).path.rsplit("/", 1)[-1] or url
    if has_text:
        meta["text"] = f"text/{fid}.txt"
    else:
        target.unlink(missing_ok=True)
        meta["text-layer"] = "no"
    if "title" not in meta:
        meta["title"] = urlsplit(url).path.rsplit("/", 1)[-1] or url


def add_source(url: str, root: Path, net, date: str) -> Result:
    aid = arxiv_id(url)
    existing = _find_existing(root, url, None)
    if aid and not existing:
        existing = _find_existing(root, f"https://arxiv.org/abs/{aid}", None)
    if existing and not existing.meta.get("error"):
        return Result(str(existing.meta["id"]), False)

    meta: dict = {}
    abstract = ""
    fid = id_for(url)
    canonical = f"https://arxiv.org/abs/{aid}" if aid else url
    error = None
    try:
        if aid:
            m = _arxiv_metadata(net, aid)
            abstract = m.pop("abstract")
            meta.update({k: v for k, v in m.items() if v})
            fid = id_for(url, meta.get("doi"))
        resp = net(f"https://arxiv.org/pdf/{_arxiv_path(aid)}" if aid else url)
        if resp.status != 200:
            raise NetError(f"HTTP {resp.status} for {url}")
        _store(root, fid, resp, url, meta)
    except NetError as e:
        error = str(e)
    base = {
        "id": fid,
        "title": meta.pop("title", None) or urlsplit(url).path.rsplit("/", 1)[-1] or url,
    }
    kind = "preprint" if aid else ("report" if meta.get("file", "").startswith("pdf/") else "other")
    fiche = {**base, **{k: v for k, v in meta.items() if k in ("authors", "year")}, "url": canonical,
             "doi": meta.get("doi"), "kind": kind, "origin": "kb add", "fetched": date}
    for k in ("file", "text", "text-layer", "sha256"):
        if k in meta:
            fiche[k] = meta[k]
    fiche["status"] = "new"
    if error:
        fiche["error"] = error
    fiche = {k: v for k, v in fiche.items() if v not in (None, "")}
    text = f"## Abstract (arXiv)\n\n{abstract}" if abstract else ""
    write_fiche(root / "fiches" / f"{fid}.md", fiche, text)
    return Result(fid, True, error)


def _title(path: Path) -> str:
    return path.stem.replace("_", " ").replace("?", "").replace("#", "")


def _add_file(path: Path, root: Path, date: str, content: bytes, digest: str, transcribe) -> Result:
    ext = path.suffix.lower()
    fid = f"local-{digest[:12]}"
    rel = f"source-material/kb-add/{fid}{ext}"
    target = root / "data" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    meta = {"id": fid, "title": _title(path), "status": "new", "origin": "kb add (local)", "kind": "other",
            "file": rel, "sha256": digest, "source-file": path.name, "fetched": date}
    error = None
    kind = "text" if ext in TEXT_EXT else "subtitle" if ext in SUBTITLE_EXT else "audio" if ext in AUDIO_EXT else "docx"
    try:
        if kind == "text":
            text, text_rel = content.decode("utf-8", errors="replace"), rel
            if not text.strip():
                raise ValueError("no text found in the file")
        else:
            if kind == "docx":
                text = docx_to_text(path)
            elif kind == "subtitle":
                text = subtitle_to_text(content.decode("utf-8", errors="replace"))
            else:
                text = (transcribe or audio_to_text)(path)
            text_rel = f"source-material/kb-add/{fid}.txt"
            if not text.strip():
                raise ValueError("no text found in the file")
            (root / "data" / text_rel).write_text(text, encoding="utf-8")
        meta["text"] = text_rel
    except Exception as e:  # noqa: BLE001
        error = str(e) or type(e).__name__
        meta["error"] = error
    write_fiche(root / "fiches" / f"{fid}.md", meta, f"machine:\n{MACHINE[kind]}\n{CITATION}")
    return Result(fid, True, error)


def add_local(path: Path, root: Path, date: str, transcribe=None) -> Result:
    ext = path.suffix.lower()
    if ext not in WEB_EXT | FILE_EXT:
        raise UnknownType(f"unknown file type {ext or '(no extension)'}; supported: "
                           + ", ".join(sorted(WEB_EXT | FILE_EXT)))
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    fid = f"local-{digest[:12]}"
    for f in load_fiches(root / "fiches"):
        if f.meta.get("sha256") == digest and not (ext in FILE_EXT and f.meta.get("error")):
            return Result(str(f.meta["id"]), False)
    if ext in FILE_EXT:
        return _add_file(path, root, date, content, digest, transcribe)
    mime_type = "application/pdf" if content[:5] == b"%PDF-" else "text/html"
    meta: dict = {}
    _store(root, fid, Response(200, mime_type, content), _title(path), meta)
    fiche = {"id": fid, "title": meta.pop("title"), "kind": "report" if meta["file"].startswith("pdf/") else "other",
             "origin": "kb add (local)", "source-file": path.name, "fetched": date, **meta, "status": "new"}
    write_fiche(root / "fiches" / f"{fid}.md", fiche, "")
    return Result(fid, True)


__all__ = ["FicheError", "Result", "UnknownType", "add_local", "add_source"]
