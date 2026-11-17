from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

WINDOW_DAYS = 30
DOMAIN_LIMIT = 0.4
UNDER_LIMIT = 0.2
TYPE_OF = {"peer-reviewed": "onderzoek", "preprint": "onderzoek", "official": "officieel", "vendor": "praktijk",
           "blog": "praktijk"}
MAIN_TYPES = ("onderzoek", "officieel", "praktijk")


def _date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def domain_of(url) -> str:
    host = (urlsplit(str(url)).hostname or "") if url else ""
    return host.removeprefix("www.") or "local"


def type_of(quality) -> str:
    return TYPE_OF.get(str(quality), "overig")


def stats(fiches, now: datetime) -> dict:
    cutoff = now.date() - timedelta(days=WINDOW_DAYS)
    recent = [f for f in fiches if (d := _date(f.meta.get("fetched"))) is not None and d >= cutoff]
    out = {"n": len(recent), "quality": Counter(), "domain": Counter(), "language": Counter(), "type": Counter()}
    for f in recent:
        m = f.meta
        out["quality"][str(m.get("quality") or "unknown")] += 1
        out["domain"][domain_of(m.get("url"))] += 1
        out["language"][str(m.get("language") or "unknown")] += 1
        out["type"][type_of(m.get("quality"))] += 1
    return out


def warnings(s: dict) -> list[str]:
    if not s["n"]:
        return []
    out = [f"one domain supplies {c / s['n']:.0%} of the new fiches: {d}"
           for d, c in s["domain"].most_common() if d != "local" and c / s["n"] > DOMAIN_LIMIT]
    out += [f"no new fiches of type {t}" for t in MAIN_TYPES if s["type"][t] == 0]
    return out


def underrepresented_types(s: dict) -> set:
    if not s["n"]:
        return set()
    return {t for t in MAIN_TYPES if s["type"][t] / s["n"] < UNDER_LIMIT}


def dominant_language(s: dict) -> str | None:
    known = [(c, k) for k, c in s["language"].items() if k != "unknown"]
    return max(known)[1] if known else None


def _share(counter: Counter, n: int, order=None) -> str:
    items = order or [k for k, _ in counter.most_common()]
    return ", ".join(f"{k} {counter[k] / n:.0%}" for k in items)


def render(s: dict) -> list[str]:
    lines = [f"## Spread (last {WINDOW_DAYS} days, {s['n']} fiches)", ""]
    if not s["n"]:
        return lines + ["Geen nieuwe fiches in deze periode.", ""]
    n = s["n"]
    lines += [f"- quality: {_share(s['quality'], n)}",
              f"- domein: {_share(Counter(dict(s['domain'].most_common(8))), n)}",
              f"- taal: {_share(s['language'], n)}",
              f"- type: {_share(s['type'], n, [*MAIN_TYPES, 'overig'])}"]
    lines += [f"- WAARSCHUWING: {w}" for w in warnings(s)]
    return lines + [""]
