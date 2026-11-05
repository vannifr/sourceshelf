import hashlib
import json
import os
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import yaml

from sourceshelf.add import _find_existing, add_source
from sourceshelf.fiche import load_fiches, write_atomic
from sourceshelf.ids import arxiv_id
from sourceshelf.index import write_index
from sourceshelf.lock import Busy, write_lock
from sourceshelf.net import NetError

ATOM = {"a": "http://www.w3.org/2005/Atom"}
CAP = 25
LOCKED = 75
KINDS = ("arxiv-query", "url-list", "feed")
COMMIT_PATHS = ("fiches", "index", "digest", "logs")
def _identity() -> tuple:
    args: list[str] = []
    if os.environ.get("KB_GIT_NAME"):
        args += ["-c", f"user.name={os.environ['KB_GIT_NAME']}"]
    if os.environ.get("KB_GIT_EMAIL"):
        args += ["-c", f"user.email={os.environ['KB_GIT_EMAIL']}"]
    return tuple(args)


class SourcesError(Exception):
    pass


@dataclass
class Source:
    id: str
    kind: str
    cadence_days: int
    query: str = ""
    urls: list = field(default_factory=list)
    max: int = 10


@dataclass
class RunResult:
    code: int
    new: int = 0
    failed: int = 0
    failed_sources: list = field(default_factory=list)
    remaining: int = 0
    index_errors: list = field(default_factory=list)
    state_error: str | None = None
    changed: int = 0


def load_sources(root: Path) -> list[Source]:
    path = root / "sources.yaml"
    try:
        raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("sources", [])
    except (OSError, yaml.YAMLError) as error:
        raise SourcesError(f"sources.yaml unreadable: {error}") from error
    sources = []
    for r in raw:
        if r.get("kind") not in KINDS or not r.get("id") or not r.get("cadence_days"):
            raise SourcesError(f"invalid source: {r}")
        if r["kind"] == "arxiv-query" and not r.get("query"):
            raise SourcesError(f"arxiv-query without query: {r['id']}")
        if r["kind"] in ("url-list", "feed") and not r.get("urls"):
            raise SourcesError(f"{r['kind']} without urls: {r['id']}")
        sources.append(Source(r["id"], r["kind"], int(r["cadence_days"]), r.get("query", ""), r.get("urls", []),
                            int(r.get("max", 10))))
    return sources


def _state(root: Path) -> dict:
    return _read_state(root, move=False)[0]


def _read_state(root: Path, move: bool = True) -> tuple[dict, str | None]:
    path = root / "data" / "state.json"
    if not path.is_file():
        return {}, None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise TypeError("not an object")
        return _normalize_state(state), None
    except OSError as error:
        return {}, f"data/state.json not readable ({error}); progress of all sources was reset for this run"
    except (ValueError, TypeError) as error:
        broken = path.with_name("state.json.broken")
        counter = 1
        while broken.exists():
            broken = path.with_name(f"state.json.broken.{counter}")
            counter += 1
        if move:
            path.replace(broken)
        return {}, f"data/state.json unreadable ({error}); kept as {broken.name}, progress of all sources was reset"


STATE_KEYS = {"laatste_ok": "last_ok", "laatste_fout": "last_error", "opeenvolgende_fouten": "consecutive_errors",
              "gezien_sha": "seen_sha", "fout": "error", "onvolledig": "incomplete", "laatste_run": "last_run"}


def _normalize_state(state: dict) -> dict:
    return {sid: {STATE_KEYS.get(k, k): v for k, v in entry.items()} if isinstance(entry, dict) else entry
            for sid, entry in state.items()}


def _save_state(root: Path, state: dict) -> None:
    path = root / "data" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(state, indent=1, ensure_ascii=False))


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def due(root: Path, now: datetime) -> list[tuple[Source, str]]:
    state = _state(root)
    out = []
    for b in load_sources(root):
        ok = _time(state.get(b.id, {}).get("last_ok"))
        if ok is None:
            out.append((b, "never succeeded"))
        elif now - ok >= timedelta(days=b.cadence_days):
            out.append((b, f"last success {ok.date()}, cadence {b.cadence_days} days"))
    return out


def _runs_path(root: Path) -> Path:
    return root / "logs" / "RUNS.md"


def _write_line(root: Path, now: datetime, text: str) -> None:
    path = _runs_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# Runs\n\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(f"- {now.isoformat(timespec='seconds')} {text}\n")


def _interrupted(root: Path) -> str | None:
    path = _runs_path(root)
    if not path.exists():
        return None
    open_start = None
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.removeprefix("- ").split(" ", 1)
        if len(parts) < 2:
            continue
        if parts[1] in ("started", "gestart"):
            open_start = parts[0]
        elif parts[1].startswith(("finished", "klaar")):
            open_start = None
    return open_start


def _arxiv_items(net, source: Source) -> list[str]:
    url = (f"https://export.arxiv.org/api/query?search_query={quote_plus(source.query)}"
           f"&sortBy=submittedDate&sortOrder=descending&max_results={source.max}")
    resp = net(url)
    if resp.status != 200:
        raise NetError(f"arXiv API returned {resp.status}")
    try:
        feed = ET.fromstring(resp.content)
    except ET.ParseError as error:
        raise NetError(f"arXiv API returned invalid XML: {error}") from error
    ids = [arxiv_id(e.findtext("a:id", namespaces=ATOM) or "") for e in feed.findall("a:entry", ATOM)]
    return [f"https://arxiv.org/abs/{i}" for i in ids if i]


def _feed_items(net, source: Source) -> list[str]:
    links = []
    for url in source.urls:
        resp = net(url)
        if resp.status != 200:
            raise NetError(f"feed {url} returned {resp.status}")
        try:
            root_el = ET.fromstring(resp.content)
        except ET.ParseError as error:
            raise NetError(f"feed {url} is invalid XML: {error}") from error
        for item in root_el.iter():
            name = item.tag.rsplit("}", 1)[-1]
            if name == "item":
                link = next((e.text for e in item if e.tag.rsplit("}", 1)[-1] == "link" and e.text), None)
            elif name == "entry":
                link = next((e.get("href") for e in item if e.tag.rsplit("}", 1)[-1] == "link" and e.get("href")),
                            None)
            else:
                continue
            if link:
                links.append(link.strip())
    return links[:source.max]


def _items(net, source: Source) -> list[str]:
    if source.kind == "arxiv-query":
        return _arxiv_items(net, source)
    if source.kind == "feed":
        return _feed_items(net, source)
    return list(source.urls)


def _change(root, net, source: Source, url: str, existing, seen: dict) -> str | None:
    resp = net(url)
    if resp.status != 200:
        raise NetError(f"HTTP {resp.status} for {url}")
    now_sha = hashlib.sha256(resp.content).hexdigest()
    previous = seen.get(url) or existing.meta.get("sha256")
    seen[url] = now_sha
    if previous and previous != now_sha:
        return str(existing.meta["id"])
    return None


def _process_source(root, net, source: Source, date: str, room: int, seen: dict | None = None):
    items = _items(net, source)
    new, failed, remaining, changed = [], [], 0, []
    seen = {} if seen is None else seen
    for url in items:
        existing = _find_existing(root, url, None)
        if existing and not existing.meta.get("error"):
            if source.kind == "url-list" and not arxiv_id(url):
                fid = _change(root, net, source, url, existing, seen)
                if fid:
                    changed.append((fid, url))
            continue
        if len(new) >= room:
            remaining += 1
            continue
        res = add_source(url, root, net, date)
        if res.new:
            new.append(res.id)
            if res.error:
                failed.append(res.id)
    return new, failed, remaining, changed


def _digest(root, now, result, new_ids, interrupted, state, sources, cap_reached, changed=()) -> str:
    fiches = {str(f.meta["id"]): f for f in load_fiches(root / "fiches")}
    r = result
    lines = [f"# Digest {now.date()}", "",
              (f"Run {now.isoformat(timespec='seconds')}: {r.new} new, {r.failed} failed, "
               f"{len(r.failed_sources)} source(s) failed, remaining {r.remaining}."), ""]
    if interrupted:
        lines += [f"## Interrupted run {interrupted}", "",
                   ("The previous run started but did not finish (`started` without `finished` in `logs/RUNS.md`); "
                    "check `data/logs/run.log` and `digest/FOUT*.md`."), ""]
    for crash in sorted((root / "digest").glob("FOUT*.md")):
        lines += ["## Crash reported by systemd", "", f"File `{crash.name}`:", "",
                   crash.read_text(encoding="utf-8").strip(), ""]
    warnings = []
    for b in sources:
        s = state.get(b.id, {})
        ok = _time(s.get("last_ok"))
        if s.get("consecutive_errors", 0) >= 2 or (ok and now - ok > timedelta(days=2 * b.cadence_days)):
            warnings.append(f"- {b.id}: {s.get('consecutive_errors', 0)} consecutive errors, "
                                  f"last success {ok.date() if ok else 'never'}")
    if warnings:
        lines += ["## Warning: sources without success", "", *warnings, ""]
    error_sources = [b for b in sources if state.get(b.id, {}).get("error")]
    if error_sources:
        lines += ["## Failed sources", ""]
        lines += [f"- {b.id}: {state[b.id]['error']} (since {state[b.id].get('last_error', '?')[:10]})"
                   for b in error_sources] + [""]
    lines += [f"## New ({len(new_ids)})", ""]
    for i in new_ids:
        m = fiches[i].meta if i in fiches else {}
        lines.append(f"- {i}: {m.get('title', '?')} ({m.get('url', '')})")
    lines.append("")
    if changed:
        lines += [f"## Changed ({len(changed)})", "",
                   "Content differs from the stored version; fetch it again by hand with `sourceshelf add` after removing the fiche, or review the difference.", ""]
        lines += [f"- {i}: {u}" for i, u in changed] + [""]
    open_errors = [f for f in fiches.values() if f.meta.get("error")]
    if open_errors:
        lines += ["## Failed fetches", ""]
        lines += [f"- {f.meta['id']}: {f.meta['error']}" for f in open_errors] + [""]
    if r.state_error:
        lines += ["## State unreadable", "", r.state_error, ""]
    if r.index_errors:
        lines += ["## Index not updated", "", "`index/index.json` was not refreshed because of invalid fiches:", ""]
        lines += [f"- {m}" for m in r.index_errors] + [""]
    if cap_reached:
        lines += ["## Cap reached", "",
                   f"Maximum of {CAP} new sources per run; {r.remaining} wait for the next run.", ""]
    return "\n".join(lines)


def _commit(root: Path, now: datetime, r: RunResult) -> None:
    paths = [p for p in COMMIT_PATHS if (root / p).exists()]
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "add", "-A", "--", *paths], check=True, capture_output=True)
    changed = subprocess.run([*git, "status", "--porcelain", "--", *paths], check=True, capture_output=True,
                               text=True).stdout.strip()
    if changed:
        message = f"chore(run): {now.date()} {r.new} new, {r.failed} failed"
        subprocess.run([*git, *_identity(), "commit", "-q", "-m", message, "--", *paths], check=True,
                       capture_output=True)


def run(root: Path, net, now: datetime, source_id: str | None = None, cap: int = CAP,
        commit: bool = True) -> RunResult:
    try:
        with write_lock(root):
            return _run_locked(root, net, now, source_id, cap, commit)
    except Busy:
        _write_line(root, now, "skipped: lock")
        return RunResult(LOCKED)


def _run_locked(root, net, now, source_id, cap, commit) -> RunResult:
    sources = load_sources(root)
    if source_id:
        chosen = [b for b in sources if b.id == source_id]
        if not chosen:
            raise SourcesError(f"unknown source: {source_id}")
    else:
        due_ids = {b.id for b, _ in due(root, now)}
        chosen = [b for b in sources if b.id in due_ids]
    interrupted = _interrupted(root)
    _write_line(root, now, "started")
    state, state_error = _read_state(root)
    r = RunResult(0)
    r.state_error = state_error
    if state_error:
        _write_line(root, now, f"warning: {state_error}")
    new_ids = []
    changed = []
    date = now.date().isoformat()
    for b in chosen:
        s = state.setdefault(b.id, {"last_ok": None, "last_error": None, "consecutive_errors": 0})
        try:
            seen = dict(s.get("seen_sha", {}))
            new, failed, remaining, chg = _process_source(root, net, b, date, cap - r.new, seen)
            s["seen_sha"] = seen
        except (NetError, ET.ParseError) as error:
            s.update(last_error=now.isoformat(timespec="seconds"), error=str(error),
                     consecutive_errors=s["consecutive_errors"] + 1)
            r.failed_sources.append(b.id)
            r.failed += 1
        else:
            new_ids += new
            changed += chg
            r.changed += len(chg)
            r.new += len(new)
            r.failed += len(failed)
            r.remaining += remaining
            s.update(error=None, consecutive_errors=0, incomplete=remaining > 0)
            if not remaining:
                s["last_ok"] = now.isoformat(timespec="seconds")
        s["last_run"] = now.isoformat(timespec="seconds")
        _save_state(root, state)
    r.index_errors = write_index(root)
    digest = _digest(root, now, r, new_ids, interrupted, state, sources, r.remaining > 0, changed)
    (root / "digest").mkdir(exist_ok=True)
    write_atomic(root / "digest" / "LATEST.md", digest)
    for crash in (root / "digest").glob("FOUT*.md"):
        crash.unlink()
    _write_line(root, now, f"finished new={r.new} failed={r.failed} "
                             f"failed_sources={len(r.failed_sources)} remaining={r.remaining} "
                             f"index_errors={len(r.index_errors)} changed={r.changed}")
    if commit:
        _commit(root, now, r)
    return r
