import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

from sourceshelf.add import add_source
from sourceshelf.discover import BUDGET_MESSAGE, openalex_get
from sourceshelf.fiche import load_fiches, write_fiche
from sourceshelf.ids import arxiv_id, normalize_url
from sourceshelf.index import write_index
from sourceshelf.lock import Busy, write_lock
from sourceshelf.net import NetError
from sourceshelf.run import SourcesError, _read_state, _save_state, _write_line, load_sources

PARTS = ("edition", "preprint", "links")
OPENALEX = "https://api.openalex.org/works"
OPENALEX_SELECT = "id,doi,title,locations"
OPENALEX_CAP = 40
LINK_CAP = 200
EDITION_CAP = 3
REMOVE_AFTER = 2
INCONCLUSIVE = (401, 403, 429)
LINK_OUTAGE_MIN = 5
PUBLISHED = ("journal", "conference")
YEAR = re.compile(r"(?<!\d)(20\d\d)(?!\d)")


@dataclass
class UpdateResult:
    editions: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    dois: list = field(default_factory=list)
    failing: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    busy: bool = False

    @property
    def code(self) -> int:
        return 1 if self.errors or self.busy else 0


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.hrefs.append(href)


def _year(text: str) -> int:
    years = [int(y) for y in YEAR.findall(text)]
    return max(years) if years else 0


def _edition_year(meta: dict) -> int:
    return _year(str(meta.get("url", ""))) or int(meta.get("year") or 0)


def _is_report(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(".pdf") or "report" in path


def _editions_of(fiches, source) -> list:
    seed = {normalize_url(u) for u in source.urls}
    return [f for f in fiches if f.meta.get("series") == source.series
            or normalize_url(str(f.meta.get("url", ""))) in seed]


def _candidates(net, source, fiches) -> tuple[list[str], list]:
    resp = net(source.landing)
    if resp.status != 200:
        raise NetError(f"HTTP {resp.status} for {source.landing}")
    parser = _Links()
    parser.feed(resp.content.decode("utf-8", errors="ignore"))
    editions = _editions_of(fiches, source)
    known = {normalize_url(str(f.meta["url"])) for f in fiches if f.meta.get("url")}
    latest_year = max((_edition_year(f.meta) for f in editions), default=0)
    found: dict[str, str] = {}
    for href in parser.hrefs:
        url = urljoin(source.landing, href)
        if urlsplit(url).scheme not in ("http", "https") or not _is_report(url):
            continue
        key = normalize_url(url)
        if key not in known and _year(url) > latest_year:
            found.setdefault(key, url)
    return sorted(found.values(), key=lambda u: (_year(u), u)), editions


def _latest(editions: list):
    return max(editions, key=lambda f: (_edition_year(f.meta), str(f.meta.get("fetched", ""))), default=None)


def _set_meta(fiche, **changes) -> None:
    meta = dict(fiche.meta)
    meta.update(changes)
    write_fiche(fiche.path, meta, fiche.text)
    fiche.meta = meta


def update_editions(root: Path, net, date: str, dry_run: bool, result: UpdateResult) -> None:
    try:
        sources = [s for s in load_sources(root) if s.series and s.landing]
    except SourcesError as error:
        result.errors.append(str(error))
        return
    for source in sources:
        fiches = load_fiches(root / "fiches")
        try:
            urls, editions = _candidates(net, source, fiches)
        except NetError as error:
            result.errors.append(f"edition {source.series}: {error}")
            continue
        previous = _latest(editions)
        for url in urls[:EDITION_CAP]:
            if dry_run:
                result.candidates.append((source.series, url, previous.meta["id"] if previous else None))
                continue
            res = add_source(url, root, net, date, source.quality)
            if res.error:
                result.errors.append(f"edition {source.series}: {url}: {res.error}")
            if not res.new:
                continue
            created = next(f for f in load_fiches(root / "fiches") if f.meta["id"] == res.id)
            changes = {"series": source.series}
            if previous:
                changes["supersedes"] = previous.meta["id"]
                if not previous.meta.get("series"):
                    _set_meta(previous, series=source.series)
            _set_meta(created, **changes)
            result.editions.append((res.id, changes.get("supersedes")))
            previous = created


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _published_doi(work: dict) -> str | None:
    for loc in work.get("locations") or []:
        source = loc.get("source") or {}
        doi = loc.get("doi")
        if source.get("type") in PUBLISHED and doi:
            return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return None


def _openalex(net, url: str, missing_ok: bool = False) -> dict:
    resp = openalex_get(net, url)
    if resp.status == 404 and missing_ok:
        return {}
    if resp.status == 429:
        raise NetError(BUDGET_MESSAGE)
    if resp.status != 200:
        raise NetError(f"OpenAlex HTTP {resp.status} for {url}")
    try:
        return json.loads(resp.content)
    except ValueError as error:
        raise NetError(f"OpenAlex returned invalid JSON for {url}") from error


def _lookup(net, meta: dict) -> tuple[str | None, str | None]:
    aid = arxiv_id(str(meta.get("url", "")))
    if aid:
        work_id = aid.replace("-", "/", 1) if "-" in aid else aid
        work = _openalex(net, f"{OPENALEX}/https://doi.org/10.48550/arxiv.{quote(work_id)}?select={OPENALEX_SELECT}", missing_ok=True)
        doi = _published_doi(work)
        if doi:
            return doi, work.get("id")
    title = str(meta.get("title", ""))
    found = _openalex(net, f"{OPENALEX}?search={quote(title)}&per-page=5&select={OPENALEX_SELECT}")
    for work in found.get("results", []):
        if _norm(str(work.get("title") or "")) == _norm(title):
            doi = _published_doi(work)
            if doi:
                return doi, work.get("id")
    return None, None


def _machine_note(text: str, line: str) -> str:
    lines = text.split("\n")
    for i, current in enumerate(lines):
        if current.strip() == "machine:":
            end = i + 1
            while end < len(lines) and lines[end][:1] in (" ", "\t"):
                end += 1
            return "\n".join([*lines[:end], line, *lines[end:]])
    return f"{text.rstrip()}\n\nmachine:\n{line}\n" if text.strip() else f"machine:\n{line}\n"


def update_preprints(root: Path, net, now: datetime, dry_run: bool, result: UpdateResult) -> None:
    date = now.date().isoformat()
    state, state_error = _read_state(root, move=not dry_run)
    if state_error:
        result.errors.append(state_error)
    checked = state.get("preprints") if isinstance(state.get("preprints"), dict) else {}
    todo = [f for f in load_fiches(root / "fiches")
            if f.meta.get("kind") == "preprint" and not f.meta.get("doi") and f.meta.get("confidential") != "yes"]
    todo.sort(key=lambda f: (checked.get(str(f.meta["id"]), ""), str(f.meta["id"])))
    for fiche in todo[OPENALEX_CAP:]:
        result.skipped.append(str(fiche.meta["id"]))
    for n, fiche in enumerate(todo[:OPENALEX_CAP]):
        fid = str(fiche.meta["id"])
        try:
            doi, work = _lookup(net, fiche.meta)
        except NetError as error:
            result.errors.append(f"preprint {fid}: {error}")
            result.skipped += [str(f.meta["id"]) for f in todo[n + 1:OPENALEX_CAP]]
            break
        checked[fid] = date
        if not doi:
            continue
        result.dois.append((fid, doi))
        if dry_run:
            continue
        note = f"  published-version: doi {doi} found by sourceshelf update on {date} via OpenAlex ({work}); quality changed to peer-reviewed"
        meta = {**fiche.meta, "doi": doi, "quality": "peer-reviewed"}
        write_fiche(fiche.path, meta, _machine_note(fiche.text, note))
    if not dry_run:
        state["preprints"] = checked
        _save_state(root, state)


def _probe(net, url: str) -> int:
    head = getattr(net, "head", None)
    if head:
        try:
            status = head(url).status
            if status < 400:
                return status
        except NetError:
            pass
    return net(url).status


def update_links(root: Path, net, now: datetime, dry_run: bool, result: UpdateResult) -> None:
    date = now.date().isoformat()
    state, state_error = _read_state(root, move=not dry_run)
    if state_error:
        result.errors.append(state_error)
    links = state.get("links") if isinstance(state.get("links"), dict) else {}
    todo = [f for f in load_fiches(root / "fiches")
            if f.meta.get("status") in ("new", "used") and f.meta.get("confidential") != "yes"
            and str(f.meta.get("url", "")).startswith(("http://", "https://"))]
    todo.sort(key=lambda f: (links.get(str(f.meta["id"]), {}).get("last", ""), str(f.meta["id"])))
    for fiche in todo[LINK_CAP:]:
        result.skipped.append(str(fiche.meta["id"]))
    probed = []
    for fiche in todo[:LINK_CAP]:
        url = str(fiche.meta["url"])
        try:
            status = _probe(net, url)
            reason = f"HTTP {status}"
        except NetError as error:
            status, reason = 0, str(error)
            if "robots.txt" in reason or "refusing non-public address" in reason:
                continue
        if status in INCONCLUSIVE:
            continue
        probed.append((fiche, status, reason))
    net_errors = sum(1 for _, status, _ in probed if status == 0)
    outage = len(probed) >= LINK_OUTAGE_MIN and net_errors * 2 > len(probed)
    if outage:
        result.errors.append(f"link check: {net_errors} of {len(probed)} probes failed with a network error, "
                             "no links marked removed (network outage?)")
    for fiche, status, reason in probed:
        fid = str(fiche.meta["id"])
        entry = links.get(fid, {})
        if 0 < status < 400:
            links[fid] = {"failures": 0, "last": date}
            continue
        if outage and status == 0:
            continue
        failures = int(entry.get("failures", 0)) + 1
        links[fid] = {"failures": failures, "last": date, "reason": reason}
        if failures >= REMOVE_AFTER:
            result.removed.append((fid, reason))
            if not dry_run:
                _set_meta(fiche, status="removed")
        else:
            result.failing.append((fid, reason))
    if not dry_run:
        state["links"] = links
        _save_state(root, state)


def _run_parts(root: Path, net, now: datetime, only: str | None, dry_run: bool, result: UpdateResult) -> None:
    date = now.date().isoformat()
    if only in (None, "edition"):
        update_editions(root, net, date, dry_run, result)
    if only in (None, "preprint"):
        update_preprints(root, net, now, dry_run, result)
    if only in (None, "links"):
        update_links(root, net, now, dry_run, result)
    if not dry_run:
        result.errors += write_index(root)


def update(root: Path, net, now: datetime, only: str | None = None, dry_run: bool = False) -> UpdateResult:
    result = UpdateResult()
    try:
        if dry_run:
            _run_parts(root, net, now, only, dry_run, result)
            return result
        with write_lock(root):
            _write_line(root, now, f"update started only={only or 'all'}")
            _run_parts(root, net, now, only, dry_run, result)
            for error in result.errors:
                _write_line(root, now, f"update error: {error}")
            _write_line(root, now, f"update finished editions={len(result.editions)} dois={len(result.dois)} "
                                     f"removed={len(result.removed)} errors={len(result.errors)}")
    except Busy:
        result.busy = True
        result.errors.append("another writer is busy (data/run.lock)")
        _write_line(root, now, "update skipped: lock")
    return result
