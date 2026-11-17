import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, quote_plus, urlencode

from sourceshelf import spread
from sourceshelf.fiche import write_atomic
from sourceshelf.ids import arxiv_id
from sourceshelf.net import NetError

API = "https://api.openalex.org"
DEFAULT_CADENCE = 30
DEFAULT_COUNTER = ("limitations", "failure", "replication", "risk")
DEFAULT_MAX_CALLS = 120
MAX_TOPICS = 8
MIN_AUTHOR_FICHES = 2
MIN_DOMAIN_FICHES = 3
MIN_TOPIC_FICHES = 2
RESOLVE_REFS = 20
KEEP_ITEMS = 8
KEEP_CHANNELS = 10
CITED_PER_FICHE = 5
COUNTER_PER_TOPIC = 2
MAX_ERRORS = 5
WORK_FIELDS = "id,doi,title,authorships,referenced_works,topics,language,cited_by_count"
LIST_FIELDS = "id,doi,title,cited_by_count,language"
DOI_PREFIX = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)


class DiscoveryError(Exception):
    pass


def parse_discovery(raw) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DiscoveryError("discovery must be a mapping")
    cadence = raw.get("cadence_days", DEFAULT_CADENCE)
    if not isinstance(cadence, int) or isinstance(cadence, bool) or cadence < 1:
        raise DiscoveryError("discovery.cadence_days must be a positive integer")
    max_calls = raw.get("max_calls", DEFAULT_MAX_CALLS)
    if not isinstance(max_calls, int) or isinstance(max_calls, bool) or max_calls < 10:
        raise DiscoveryError("discovery.max_calls must be an integer of at least 10")
    topics = raw.get("topics", [])
    counter = raw.get("counter_terms", list(DEFAULT_COUNTER))
    for name, value in (("topics", topics), ("counter_terms", counter)):
        if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
            raise DiscoveryError(f"discovery.{name} must be a list of non-empty strings")
    if len(topics) > MAX_TOPICS:
        raise DiscoveryError(f"discovery.topics has more than {MAX_TOPICS} phrases")
    if not counter:
        raise DiscoveryError("discovery.counter_terms must not be empty")
    return {"cadence_days": cadence, "max_calls": max_calls, "topics": [t.strip() for t in topics],
            "counter_terms": [t.strip() for t in counter]}


BUDGET_MESSAGE = "OpenAlex budget exhausted (429): retry after midnight UTC or set OPENALEX_API_KEY"


def openalex_url(url: str) -> str:
    key = os.environ.get("OPENALEX_API_KEY", "")
    if not key:
        return url
    return f"{url}{'&' if '?' in url else '?'}{urlencode({'api_key': key})}"


def redact_key(text: str) -> str:
    key = os.environ.get("OPENALEX_API_KEY", "")
    if key:
        for form in (key, quote(key, safe=""), quote_plus(key)):
            text = text.replace(form, "***")
    return re.sub(r"(api_key=)[^&\s'\"]+", r"\1***", text)


def openalex_get(net, url: str):
    try:
        return net(openalex_url(url))
    except NetError as error:
        raise NetError(redact_key(str(error))) from None


class _Api:
    def __init__(self, net, budget: int):
        self.net, self.budget, self.calls, self.errors = net, budget, 0, []
        self.exhausted = False

    def left(self) -> int:
        return self.budget - self.calls

    def get(self, path: str, **params):
        if self.exhausted or self.left() <= 0:
            return None
        self.calls += 1
        url = f"{API}/{path}?{urlencode(params, safe=':|,()')}"
        resp = openalex_get(self.net, url)
        if resp.status == 404:
            return None
        if resp.status == 429:
            self.exhausted = True
            self.errors.append(BUDGET_MESSAGE)
            return None
        if resp.status != 200:
            self.errors.append(f"OpenAlex answered {resp.status} for {path}")
            return None
        try:
            data = json.loads(resp.content)
        except ValueError:
            self.errors.append(f"OpenAlex returned invalid JSON for {path}")
            return None
        return data if isinstance(data, dict) else None


def _short(openalex_id) -> str:
    return str(openalex_id or "").rsplit("/", 1)[-1]


def _doi(value) -> str:
    return DOI_PREFIX.sub("", str(value or "")).strip().lower()


def _ident(meta: dict) -> str | None:
    doi = _doi(meta.get("doi"))
    if doi:
        return doi
    aid = arxiv_id(str(meta.get("url") or ""))
    return f"10.48550/arxiv.{aid}".lower() if aid else None


def _title_key(title) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()


def _library(fiches) -> tuple[set, set]:
    dois, titles = set(), set()
    for f in fiches:
        ident = _ident(f.meta)
        if ident:
            dois.add(ident)
        titles.add(_title_key(f.meta.get("title")))
    return dois, titles


def _in_library(work: dict, library: tuple[set, set]) -> bool:
    return _doi(work.get("doi")) in library[0] or _title_key(work.get("title")) in library[1]


def _cache_path(root: Path) -> Path:
    return root / "data" / "discover.json"


def _read_cache(root: Path) -> dict:
    try:
        data = json.loads(_cache_path(root).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _work_entry(work: dict) -> dict:
    authors = [[_short(a.get("author", {}).get("id")), a.get("author", {}).get("display_name") or ""]
               for a in work.get("authorships") or [] if a.get("author", {}).get("id")]
    return {"authors": authors,
            "topics": [t["display_name"] for t in work.get("topics") or [] if t.get("display_name")],
            "refs": [_short(r) for r in work.get("referenced_works") or []],
            "language": work.get("language") or "unknown", "cited": [], "id": _short(work.get("id"))}


def _slim(work: dict) -> dict:
    return {"id": _short(work.get("id")), "doi": _doi(work.get("doi")), "title": work.get("title") or "",
            "cited_by": work.get("cited_by_count") or 0, "language": work.get("language") or "unknown"}


def _refresh_cache(api: _Api, cache: dict, used: list, offset: int, reserve: int) -> int:
    cut = offset % len(used) if used else 0
    pending = used[cut:] + used[:cut]
    take = max((api.left() - reserve) // 2, 0)
    done = 0
    for f in pending:
        if done >= take:
            break
        done += 1
        fid = str(f.meta["id"])
        work = api.get(f"works/doi:{quote(_ident(f.meta), safe='/')}", select=WORK_FIELDS)
        if not work:
            continue
        entry = _work_entry(work)
        listing = api.get("works", filter=f"cites:{entry['id']}", sort="cited_by_count:desc",
                          **{"per-page": CITED_PER_FICHE, "select": LIST_FIELDS})
        entry["cited"] = [_slim(w) for w in (listing or {}).get("results", [])]
        cache[fid] = entry
    return done


def _author_candidates(cache: dict, stats: dict) -> list[dict]:
    fiches_of: dict[str, set] = {}
    names: dict[str, str] = {}
    langs: dict[str, Counter] = {}
    for fid, e in cache.items():
        for aid, name in e["authors"]:
            fiches_of.setdefault(aid, set()).add(fid)
            names[aid] = name
            langs.setdefault(aid, Counter())[e["language"]] += 1
    out = []
    for aid, fids in fiches_of.items():
        if len(fids) < MIN_AUTHOR_FICHES or not names[aid]:
            continue
        language = langs[aid].most_common(1)[0][0]
        out.append(_channel("author", names[aid], len(fids), f"author in {len(fids)} used fiches",
                            "onderzoek", language, stats))
    return out


def _channel(kind: str, name: str, base: int, reason: str, type_: str, language: str, stats: dict) -> dict:
    bonus, notes = 0, []
    if type_ in spread.underrepresented_types(stats):
        bonus += 2
        notes.append(f"type {type_} is underrepresented")
    dominant = spread.dominant_language(stats)
    if language != "unknown" and dominant and language != dominant:
        bonus += 1
        notes.append(f"language {language} is underrepresented")
    if notes:
        reason += f"; bonus: {', '.join(notes)}"
    return {"kind": kind, "name": name, "reason": reason, "score": base + bonus}


def covered_domains(sources) -> set:
    out = set()
    for s in sources:
        if s.kind == "arxiv-query":
            out |= {"arxiv.org", "export.arxiv.org"}
        out |= {spread.domain_of(u) for u in s.urls}
    return out


def domain_candidates(fiches, sources, stats: dict) -> list[dict]:
    covered = covered_domains(sources)
    groups: dict[str, list] = {}
    for f in fiches:
        origin = str(f.meta.get("origin") or "")
        if origin.startswith("kb add") and f.meta.get("url"):
            groups.setdefault(spread.domain_of(f.meta["url"]), []).append(f.meta)
    out = []
    for domain, metas in groups.items():
        if len(metas) < MIN_DOMAIN_FICHES or any(domain == c or domain.endswith("." + c) for c in covered):
            continue
        type_ = Counter(spread.type_of(m.get("quality")) for m in metas).most_common(1)[0][0]
        language = Counter(str(m.get("language") or "unknown") for m in metas).most_common(1)[0][0]
        out.append(_channel("domain", domain, len(metas), f"{len(metas)} fiches added by hand, no line in sources.yaml",
                            type_, language, stats))
    return out


def _item_candidates(api: _Api, cache: dict, library) -> list[dict]:
    found: dict[str, dict] = {}
    for fid, e in cache.items():
        for w in e["cited"]:
            if w["id"] and not _in_library(w, library):
                found.setdefault(w["id"], {**w, "reason": f"cites {fid}"})
    refs = Counter(r for e in cache.values() for r in set(e["refs"]) if r)
    top = [r for r, _ in sorted(refs.items(), key=lambda kv: (-kv[1], kv[0]))[:RESOLVE_REFS]]
    if top:
        data = api.get("works", filter="openalex:" + "|".join(top), **{"per-page": RESOLVE_REFS, "select": LIST_FIELDS})
        for w in (data or {}).get("results", []):
            slim = _slim(w)
            if slim["id"] and not _in_library(slim, library):
                found[slim["id"]] = {**slim, "reason": f"referenced by {refs[slim['id']]} used fiches"}
    ranked = sorted(found.values(), key=lambda w: (-int(w["reason"].startswith("referenced")), -w["cited_by"]))
    return ranked[:KEEP_ITEMS]


def _counter_items(api: _Api, cfg: dict, now: datetime, library) -> list[dict]:
    terms = " OR ".join(cfg["counter_terms"])
    since = (now - timedelta(days=730)).date().isoformat()
    out = []
    for topic in cfg["topics"]:
        data = api.get("works", search=f"{topic} ({terms})", filter=f"from_publication_date:{since}",
                       **{"per-page": COUNTER_PER_TOPIC * 3, "select": LIST_FIELDS})
        picked = [w for w in (_slim(w) for w in (data or {}).get("results", [])) if not _in_library(w, library)]
        out += [{**w, "reason": f"counter-voice on '{topic}'"} for w in picked[:COUNTER_PER_TOPIC]]
    return out


def _adjacent(cache: dict, cfg: dict) -> list[dict]:
    known = [t.lower() for t in cfg["topics"]]
    counts = Counter(t for e in cache.values() for t in set(e["topics"]))
    out = []
    for name, n in counts.most_common():
        low = name.lower()
        if n < MIN_TOPIC_FICHES or any(k in low or low in k for k in known):
            continue
        out.append({"name": name, "reason": f"topic of {n} used fiches, not in discovery.topics"})
    return out[:5]


def explore(root: Path, net, now: datetime, cfg: dict, sources, fiches) -> dict:
    stats = spread.stats(fiches, now)
    library = _library(fiches)
    result = {"date": now.date().isoformat(), "errors": [], "channels": domain_candidates(fiches, sources, stats),
              "items": [], "counter": [], "adjacent": [], "calls": 0}
    cache_all = _read_cache(root)
    cache = cache_all.get("works", {})
    api = _Api(net, cfg["max_calls"])
    used = sorted((f for f in fiches if f.meta.get("status") == "used" and f.meta.get("confidential") != "yes"
                   and _ident(f.meta)), key=lambda f: str(f.meta["id"]))
    offset = int(cache_all.get("offset", 0))
    try:
        done = _refresh_cache(api, cache, used, offset, len(cfg["topics"]) + 1)
        ids = {str(f.meta["id"]) for f in used}
        live = {k: v for k, v in cache.items() if k in ids}
        result["channels"] += _author_candidates(live, stats)
        result["items"] = _item_candidates(api, live, library)
        result["counter"] = _counter_items(api, cfg, now, library)
        result["adjacent"] = _adjacent(live, cfg)
        cache = live
        offset += done
    except NetError as error:
        api.errors.append(f"network: {error}")
    result["channels"] = sorted(result["channels"], key=lambda c: (-c["score"], c["name"]))[:KEEP_CHANNELS]
    result["calls"] = api.calls
    result["errors"] = api.errors[:MAX_ERRORS]
    write_atomic(_cache_path(root), json.dumps({"works": cache, "offset": offset, "result": result},
                                              ensure_ascii=False))
    return result


def step(root: Path, net, now: datetime, cfg: dict | None, sources, fiches, state: dict, log) -> dict | None:
    if cfg is None:
        return None
    last = state.get("discover", {}).get("last_run")
    if last and now - datetime.fromisoformat(last) < timedelta(days=cfg["cadence_days"]):
        return _read_cache(root).get("result")
    result = explore(root, net, now, cfg, sources, fiches)
    for message in result["errors"]:
        log(f"warning: discover: {message}")
    log(f"discover calls={result['calls']} channels={len(result['channels'])} items={len(result['items'])} "
        f"counter={len(result['counter'])} adjacent={len(result['adjacent'])} errors={len(result['errors'])}")
    if not result["errors"]:
        state["discover"] = {"last_run": now.isoformat(timespec="seconds")}
    return result


def render(result: dict | None) -> list[str]:
    if not result:
        return []
    lines = []
    if result["errors"]:
        lines += [f"## Exploration failed ({result['date']})", ""]
        lines += [f"- {e}" for e in result["errors"]] + [""]
    picks = [f"- {w['title']} (doi:{w['doi'] or '?'}): {w['reason']}" for w in result["counter"][:2]]
    picks += [f"- topic: {a['name']}: {a['reason']}" for a in result["adjacent"][:1]]
    spare = [f"- {w['title']} (doi:{w['doi'] or '?'}): {w['reason']}" for w in result["counter"][2:]]
    spare += [f"- topic: {a['name']}: {a['reason']}" for a in result["adjacent"][1:]]
    picks = (picks + spare)[:3]
    if picks:
        lines += [f"## Exploration ({result['date']})", "", *picks, ""]
    proposals = [f"- channel ({c['kind']}) {c['name']}: {c['reason']}" for c in result["channels"]]
    proposals += [f"- item {w['title']} (doi:{w['doi'] or '?'}): {w['reason']}" for w in result["items"]]
    if proposals:
        lines += [f"## Suggested sources ({result['date']})", "",
                  "Proposals only: add a channel to `sources.yaml` yourself.", "", *proposals, ""]
    return lines
