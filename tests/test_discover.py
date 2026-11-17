import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from sourceshelf import discover, spread
from sourceshelf.fiche import Fiche, write_fiche
from sourceshelf.net import NetError, Response
from sourceshelf.run import Source, SourcesError, load_discovery, load_sources, run
from tests.conftest import FakeNet
from tests.test_run import NOW, make_repo, read_file

CFG = {"cadence_days": 30, "max_calls": 60, "topics": ["agentic software engineering", "EU AI Act"],
       "counter_terms": ["limitations", "risk"]}


def fiche(fid, **meta):
    base = {"id": fid, "title": f"T {fid}", "status": "new", "origin": "migration", "quality": "preprint",
            "fetched": NOW.date().isoformat()}
    return Fiche(None, {**base, **meta}, "")


def work(wid, doi, title, **extra):
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}" if doi else None, "title": title,
            "cited_by_count": 5, "language": "en", **extra}


class OpenAlexNet(FakeNet):
    def __init__(self, status=200, fail=None, bad_json=False):
        super().__init__({})
        self.status, self.fail, self.bad_json = status, fail, bad_json

    def __call__(self, url):
        self.requests_made.append(url)
        if self.fail:
            raise NetError(self.fail)
        if self.status != 200:
            return Response(self.status, "", b"")
        if self.bad_json:
            return Response(200, "application/json", b"not json")
        if "/works/doi:" in url:
            doi = url.split("doi:")[1].split("?")[0]
            n = 1 if doi.endswith(("one", "1")) else 2
            body = work(f"W{n}", doi, "x", referenced_works=["https://openalex.org/W90", "https://openalex.org/W91"],
                        topics=[{"display_name": "Software testing"}, {"display_name": "EU AI Act"}],
                        authorships=[{"author": {"id": "https://openalex.org/A1", "display_name": "Ann Author"}},
                                     {"author": {"id": f"https://openalex.org/A{n + 5}", "display_name": f"Solo {n}"}},
                                     {"author": {}}])
        elif "filter=cites" in url:
            body = {"results": [work("W50", "10.1/new", "New citing"), work("W51", "10.1/have", "T have"),
                                work("W52", None, "T have")]}
        elif "filter=openalex" in url:
            body = {"results": [work("W90", "10.1/ref", "Referenced work"), work("", "10.1/skip", "No id")]}
        else:
            body = {"results": [work("W70", "10.1/c1", "Counter one"), work("W71", "10.1/have", "dup"),
                                work("W72", "10.1/c2", "Counter two"), work("W73", "10.1/c3", "Counter three")]}
        return Response(200, "application/json", json.dumps(body).encode())


def used_fiches():
    return [fiche("a-1", status="used", doi="https://doi.org/10.1/one"),
            fiche("a-2", status="used", url="https://arxiv.org/abs/2501.00002"),
            fiche("a-3", status="used", confidential="yes", doi="10.1/secret"),
            fiche("a-4", status="used"),
            fiche("have", doi="10.1/have")]


def test_spread_stats_and_render():
    fiches = [fiche("a", url="https://www.x.org/1", language="nl", quality="official"),
              fiche("b", url="https://x.org/2", quality="vendor"),
              fiche("c", quality="peer-reviewed", fetched=datetime(2026, 10, 1, tzinfo=UTC)),
              fiche("d", fetched=(NOW - timedelta(days=40)).date()),
              fiche("e", fetched=NOW.date()), fiche("f", fetched="garbage"), fiche("g", fetched=None)]
    s = spread.stats(fiches, NOW)
    assert s["n"] == 4 and s["domain"]["x.org"] == 2 and s["domain"]["local"] == 2
    assert s["type"]["officieel"] == 1 and s["language"]["nl"] == 1
    text = "\n".join(spread.render(s))
    assert "WAARSCHUWING: one domain supplies 50%" in text and "x.org" in text
    assert spread.underrepresented_types(s) == set()
    assert spread.dominant_language(s) == "nl"


def test_spread_warns_missing_type_and_empty():
    s = spread.stats([fiche("a", quality="blog")], NOW)
    assert spread.warnings(s) == ["no new fiches of type onderzoek", "no new fiches of type officieel"]
    assert spread.underrepresented_types(s) == {"onderzoek", "officieel"}
    empty = spread.stats([], NOW)
    assert spread.warnings(empty) == [] and spread.underrepresented_types(empty) == set()
    assert spread.dominant_language(empty) is None
    assert "Geen nieuwe fiches" in "\n".join(spread.render(empty))


def test_parse_discovery():
    assert discover.parse_discovery(None) is None
    cfg = discover.parse_discovery({"topics": [" a "]})
    assert cfg["cadence_days"] == 30 and cfg["topics"] == ["a"] and "risk" in cfg["counter_terms"]
    for bad in ("x", {"cadence_days": 0}, {"cadence_days": True}, {"max_calls": 3}, {"topics": "a"},
                {"topics": [""]}, {"topics": list("abcdefghi")}, {"counter_terms": []}):
        with pytest.raises(discover.DiscoveryError):
            discover.parse_discovery(bad)


def test_load_sources_tolerates_and_validates_discovery(tmp_path):
    make_repo(tmp_path)
    doc = yaml.safe_load((tmp_path / "sources.yaml").read_text())
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump({**doc, "discovery": {"topics": ["x"]}}))
    assert len(load_sources(tmp_path)) == 1 and load_discovery(tmp_path)["topics"] == ["x"]
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump({**doc, "discovery": {"cadence_days": -1}}))
    with pytest.raises(SourcesError):
        load_sources(tmp_path)
    (tmp_path / "sources.yaml").write_text("- a\n")
    with pytest.raises(SourcesError):
        load_sources(tmp_path)


def test_429_stops_after_one_request(tmp_path):
    net = OpenAlexNet(status=429)
    r = discover.explore(tmp_path, net, NOW, CFG, [], used_fiches())
    assert len(net.requests_made) == 1 and len(r["errors"]) == 1 and "budget exhausted" in r["errors"][0]


def test_api_key_in_urls_not_in_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "k1")
    net = OpenAlexNet()
    discover.explore(tmp_path, net, NOW, CFG, [], used_fiches())
    assert net.requests_made and all("api_key=k1" in u for u in net.requests_made)
    net = OpenAlexNet(status=429)
    r = discover.explore(tmp_path, net, NOW, CFG, [], used_fiches())
    assert "k1" not in " ".join(r["errors"])


def test_redact_key_covers_encoded_and_redirect_forms(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "a b&c")
    msg = "redirect to https://x/y?api_key=a+b%26c and https://z/?q=1&api_key=other, raw a b&c, enc a%20b%26c"
    out = discover.redact_key(msg)
    assert "a+b" not in out and "other" not in out and "a b&c" not in out and "a%20b%26c" not in out
    monkeypatch.delenv("OPENALEX_API_KEY")
    assert discover.redact_key("https://x/?api_key=zzz&a=1") == "https://x/?api_key=***&a=1"


def test_no_api_key_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    net = OpenAlexNet()
    discover.explore(tmp_path, net, NOW, CFG, [], used_fiches())
    assert net.requests_made and not any("api_key" in u for u in net.requests_made)
    assert discover.openalex_url("https://x/y?a=1") == "https://x/y?a=1"
    monkeypatch.setenv("OPENALEX_API_KEY", "a b")
    assert discover.openalex_url("https://x/y?a=1") == "https://x/y?a=1&api_key=a+b"
    assert discover.openalex_url("https://x/y") == "https://x/y?api_key=a+b"


def test_explore_collects_candidates(tmp_path):
    fiches = used_fiches()
    net = OpenAlexNet()
    r = discover.explore(tmp_path, net, NOW, CFG, [], fiches)
    assert r["errors"] == [] and r["calls"] == len(net.requests_made)
    assert not any("secret" in u or "a-4" in u for u in net.requests_made)
    assert any("10.48550/arxiv.2501.00002" in u for u in net.requests_made)
    assert not any("api.openalex.org" not in u or "mailto" in u or "@" in u for u in net.requests_made)
    names = [c["name"] for c in r["channels"]]
    assert names == ["Ann Author"] and "2 used fiches" in r["channels"][0]["reason"]
    titles = [w["title"] for w in r["items"]]
    assert "Referenced work" in titles and "New citing" in titles and "T have" not in titles
    assert [w["title"] for w in r["counter"]][:2] == ["Counter one", "Counter two"] and len(r["counter"]) == 4
    assert r["adjacent"][0]["name"] == "Software testing" and len(r["adjacent"]) == 1
    cache = json.loads((tmp_path / "data" / "discover.json").read_text())
    assert set(cache["works"]) == {"a-1", "a-2"} and cache["offset"] == 2


def test_explore_budget_and_offset(tmp_path):
    fiches = [fiche(f"u{i}", status="used", doi=f"10.1/{i}") for i in range(5)]
    cfg = {**CFG, "max_calls": 10}
    net = OpenAlexNet()
    r = discover.explore(tmp_path, net, NOW, cfg, [], fiches)
    assert r["calls"] <= 10
    cache = json.loads((tmp_path / "data" / "discover.json").read_text())
    assert len(cache["works"]) == 3 and cache["offset"] == 3
    discover.explore(tmp_path, OpenAlexNet(), NOW, cfg, [], fiches)
    cache = json.loads((tmp_path / "data" / "discover.json").read_text())
    assert set(cache["works"]) == {"u0", "u1", "u2", "u3", "u4"} and cache["offset"] == 6


@pytest.mark.parametrize("kwargs,text", [({"status": 503}, "503"), ({"bad_json": True}, "invalid JSON"),
                                         ({"fail": "down"}, "network: down")])
def test_explore_errors_are_reported(tmp_path, kwargs, text):
    r = discover.explore(tmp_path, OpenAlexNet(**kwargs), NOW, CFG, [], used_fiches())
    assert any(text in e for e in r["errors"]) and len(r["errors"]) <= discover.MAX_ERRORS


def test_not_found_is_not_an_error(tmp_path):
    r = discover.explore(tmp_path, OpenAlexNet(status=404), NOW, CFG, [], used_fiches())
    assert r["errors"] == [] and r["items"] == []


def test_non_dict_json_and_corrupt_cache(tmp_path):
    class ListNet(OpenAlexNet):
        def __call__(self, url):
            return Response(200, "", b"[1]")

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "discover.json").write_text("{broken")
    r = discover.explore(tmp_path, ListNet(), NOW, CFG, [], used_fiches())
    assert r["items"] == [] and r["errors"] == []
    (tmp_path / "data" / "discover.json").write_text("[1]")
    assert discover._read_cache(tmp_path) == {}


def test_domain_candidates_and_mix_bonus():
    sources = [Source("s", "url-list", 7, urls=["https://covered.org/a"]), Source("x", "arxiv-query", 7, query="q")]
    fiches = [fiche(f"k{i}", origin="kb add", url=f"https://blog.example.nl/{i}", quality="blog", language="nl")
              for i in range(3)]
    fiches += [fiche(f"c{i}", origin="kb add", url=f"https://sub.covered.org/{i}") for i in range(3)]
    fiches += [fiche(f"t{i}", origin="kb add", url=f"https://two.example/{i}") for i in range(2)]
    fiches += [fiche("m", origin="migration", url="https://mig.example/1")] * 3
    fiches += [fiche("e", language="en", quality="official")] * 4
    stats = spread.stats(fiches, NOW)
    out = discover.domain_candidates(fiches, sources, stats)
    assert [c["name"] for c in out] == ["blog.example.nl"]
    assert out[0]["score"] == 3 + 1 and "language nl is underrepresented" in out[0]["reason"]
    assert discover.covered_domains(sources) >= {"covered.org", "arxiv.org"}


def test_step_cadence_state_and_render(tmp_path):
    state = {}
    logs = []
    fiches = used_fiches()
    assert discover.step(tmp_path, OpenAlexNet(), NOW, None, [], fiches, state, logs.append) is None
    r = discover.step(tmp_path, OpenAlexNet(), NOW, CFG, [], fiches, state, logs.append)
    assert state["discover"]["last_run"] and logs[-1].startswith("discover calls=")
    net = OpenAlexNet()
    again = discover.step(tmp_path, net, NOW + timedelta(days=29), CFG, [], fiches, state, logs.append)
    assert net.requests_made == [] and again == r
    discover.step(tmp_path, net, NOW + timedelta(days=30), CFG, [], fiches, state, logs.append)
    assert net.requests_made
    text = "\n".join(discover.render(r))
    assert "## Exploration (" in text and "## Suggested sources" in text and "Ann Author" in text
    assert "topic: Software testing" in text and "Counter one" in text and "Counter three" not in text
    assert discover.render(None) == [] and discover.render({**r, "counter": [], "adjacent": [], "channels": [],
                                                              "items": [], "errors": []}) == []


def test_step_error_keeps_last_run_unset(tmp_path):
    state, logs = {}, []
    r = discover.step(tmp_path, OpenAlexNet(status=500), NOW, CFG, [], used_fiches(), state, logs.append)
    assert "discover" not in state and any(line.startswith("warning: discover:") for line in logs)
    assert "## Exploration failed" in "\n".join(discover.render(r))


def test_render_backfills_to_three():
    r = {"date": "d", "errors": [], "channels": [], "items": [], "counter": [], "adjacent": [
        {"name": f"n{i}", "reason": "r"} for i in range(5)]}
    assert "".join(discover.render(r)).count("topic:") == 3


def test_run_integration_digest_and_log(tmp_path, mini_pdf):
    make_repo(tmp_path)
    doc = yaml.safe_load((tmp_path / "sources.yaml").read_text())
    (tmp_path / "sources.yaml").write_text(yaml.safe_dump({**doc, "discovery": CFG}))
    (tmp_path / "fiches").mkdir()
    write_fiche(tmp_path / "fiches" / "a-1.md", {"id": "a-1", "title": "A", "status": "used", "origin": "x",
                                                  "kind": "preprint", "quality": "preprint",
                                                  "fetched": NOW.date().isoformat(), "doi": "10.1/one"})

    class Both(OpenAlexNet):
        def __call__(self, url):
            if "export.arxiv.org" in url:
                return Response(200, "", b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
            return super().__call__(url)

    r = run(tmp_path, Both(), NOW)
    assert r.code == 0
    digest = read_file(tmp_path, "digest/LATEST.md")
    assert "## Spread" in digest and "## Exploration (" in digest and "Counter one" in digest
    assert "discover calls=" in read_file(tmp_path, "logs/RUNS.md")
    assert json.loads(read_file(tmp_path, "data/state.json"))["discover"]["last_run"]
    failing = run(tmp_path, Both(status=500), NOW + timedelta(days=31), source_id=None)
    assert failing.code == 0
    assert "warning: discover:" in read_file(tmp_path, "logs/RUNS.md")
    assert "## Exploration failed" in read_file(tmp_path, "digest/LATEST.md")


def test_network_error_hides_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "secretkey9")

    class Leaky(OpenAlexNet):
        def __call__(self, url):
            self.requests_made.append(url)
            raise NetError(f"network error at {url}: boom")

    r = discover.explore(tmp_path, Leaky(), NOW, CFG, [], used_fiches())
    assert r["errors"] and "boom" in r["errors"][0] and "secretkey9" not in " ".join(r["errors"])
    with pytest.raises(NetError) as info:
        discover.openalex_get(Leaky(), "https://api.openalex.org/works?x=1")
    assert "secretkey9" not in str(info.value) and "x=1" in str(info.value)
