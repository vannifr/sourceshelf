import pytest

from sourceshelf import cli
from sourceshelf.describe import ModelError, describe, filter_noise
from sourceshelf.fiche import load_fiche
from sourceshelf.search import search

NOISE = (
    "Hier is een samenvatting van het rapport:\n\n# Samenvatting\n\n"
    "Het rapport beschrijft drie experimenten. Het meet 12 procent winst.\n\n"
    "Laat me weten als je meer detail wilt."
)


class FakeModel:
    def __init__(self, answer=NOISE):
        self.answer = answer
        self.calls_made = []

    def __call__(self, system, text):
        self.calls_made.append((system, text))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def make_fiche(root, fid="e1", text="inhoud met kwartel", extra=""):
    (root / "fiches").mkdir(exist_ok=True)
    (root / "data" / "text").mkdir(parents=True, exist_ok=True)
    (root / "data" / "text" / f"{fid}.txt").write_text(text, encoding="utf-8")
    (root / "fiches" / f"{fid}.md").write_text(
        f"---\nid: {fid}\ntitle: Title\nstatus: new\norigin: kb add\ntext: text/{fid}.txt\n{extra}---\n\nmachine:\n  x: y\n"
    )


def test_filter_removes_preamble_heading_and_closing():
    assert filter_noise(NOISE) == "Het rapport beschrijft drie experimenten. Het meet 12 procent winst."


def test_describe_writes_marked_summary(tmp_path):
    make_fiche(tmp_path)
    model = FakeModel()
    res = describe(tmp_path, "e1", model, "2026-10-22", "test-model")
    assert res.done and not res.error
    assert "inhoud met kwartel" in model.calls_made[0][1]
    m = load_fiche(tmp_path / "fiches" / "e1.md").meta["machine-summary"]
    assert m["text"].startswith("Het rapport beschrijft")
    assert m["model"] == "test-model" and m["date"] == "2026-10-22" and m["prompt"]
    assert "Hier is" not in m["text"]


def test_second_time_skipped_unless_again(tmp_path):
    make_fiche(tmp_path)
    model = FakeModel()
    describe(tmp_path, "e1", model, "2026-10-22", "m")
    res = describe(tmp_path, "e1", model, "2026-10-23", "m")
    assert not res.done and len(model.calls_made) == 1
    describe(tmp_path, "e1", model, "2026-10-23", "m", again=True)
    assert len(model.calls_made) == 2


def test_confidential_never_goes_to_model(tmp_path):
    make_fiche(tmp_path, extra="confidential: yes\n")
    model = FakeModel()
    res = describe(tmp_path, "e1", model, "2026-10-22", "m")
    assert not res.done and "confidential" in res.reason and model.calls_made == []


def test_model_error_gives_error_line_and_fiche_stays_valid(tmp_path):
    make_fiche(tmp_path)
    res = describe(tmp_path, "e1", FakeModel(ModelError("HTTP 500")), "2026-10-22", "m")
    assert res.error == "HTTP 500"
    f = load_fiche(tmp_path / "fiches" / "e1.md")
    assert f.meta["error"] == "summary: HTTP 500" and "machine-summary" not in f.meta
    ok = describe(tmp_path, "e1", FakeModel(), "2026-10-23", "m")
    assert ok.done and "error" not in load_fiche(tmp_path / "fiches" / "e1.md").meta


def test_empty_text_or_empty_answer_is_error(tmp_path):
    make_fiche(tmp_path, text="  \n")
    assert describe(tmp_path, "e1", FakeModel(), "d", "m").error
    make_fiche(tmp_path, fid="e2")
    assert describe(tmp_path, "e2", FakeModel("# Alleen kop"), "d", "m").error


def test_fiche_without_text_is_error(tmp_path):
    (tmp_path / "fiches").mkdir()
    (tmp_path / "fiches" / "e3.md").write_text("---\nid: e3\ntitle: T\nstatus: new\norigin: x\n---\n")
    assert "no text file" in describe(tmp_path, "e3", FakeModel(), "d", "m").error


def test_long_text_is_truncated_and_marked(tmp_path):
    make_fiche(tmp_path, text="a" * 50)
    model = FakeModel()
    describe(tmp_path, "e1", model, "d", "m", max_chars=20)
    assert len(model.calls_made[0][1]) == 20
    assert load_fiche(tmp_path / "fiches" / "e1.md").meta["machine-summary"]["truncated"] == "yes"


def test_summary_is_searchable(tmp_path):
    make_fiche(tmp_path)
    describe(tmp_path, "e1", FakeModel(), "d", "m")
    assert search(tmp_path, "experimenten")


def test_cli_all_and_dry_run(tmp_path, monkeypatch, capsys):
    make_fiche(tmp_path, "e1")
    make_fiche(tmp_path, "e2")
    model = FakeModel()
    monkeypatch.setattr(cli, "make_model", lambda: (model, "m"))
    assert cli.main(["--root", str(tmp_path), "describe", "--all", "--dry-run"]) == 0
    assert model.calls_made == [] and "e1" in capsys.readouterr().out
    assert cli.main(["--root", str(tmp_path), "describe", "--all"]) == 0
    assert len(model.calls_made) == 2
    assert cli.main(["--root", str(tmp_path), "describe", "unknown"]) == 1


def test_cli_error_gives_exit_1(tmp_path, monkeypatch):
    make_fiche(tmp_path)
    monkeypatch.setattr(cli, "make_model", lambda: (FakeModel(ModelError("broken")), "m"))
    assert cli.main(["--root", str(tmp_path), "describe", "e1"]) == 1


def test_key_missing_gives_model_error(monkeypatch):
    from sourceshelf.describe import openrouter_model

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ModelError, match="OPENROUTER_API_KEY"):
        openrouter_model()


def test_confidential_case_and_all(tmp_path, monkeypatch):
    make_fiche(tmp_path, "e1", extra="confidential: Yes\n")
    make_fiche(tmp_path, "e2")
    model = FakeModel()
    monkeypatch.setattr(cli, "make_model", lambda: (model, "m"))
    assert cli.main(["--root", str(tmp_path), "describe", "--all"]) == 0
    assert len(model.calls_made) == 1 and "kwartel" in model.calls_made[0][1]
    assert "machine-summary" not in load_fiche(tmp_path / "fiches" / "e1.md").meta


def test_kb_check_accepts_summary_and_error(tmp_path):
    make_fiche(tmp_path)
    describe(tmp_path, "e1", FakeModel(), "d", "m")
    assert cli.main(["--root", str(tmp_path), "check"]) == 0
    describe(tmp_path, "e1", FakeModel(ModelError("x")), "d", "m", again=True)
    assert cli.main(["--root", str(tmp_path), "check"]) == 0


def test_unexpected_error_does_not_stop_all(tmp_path, monkeypatch):
    make_fiche(tmp_path, "e1")
    make_fiche(tmp_path, "e2")
    model = FakeModel(RuntimeError("boom"))
    monkeypatch.setattr(cli, "make_model", lambda: (model, "m"))
    assert cli.main(["--root", str(tmp_path), "describe", "--all"]) == 1
    assert len(model.calls_made) == 2


def test_bold_content_stays():
    assert "belangrijke" in filter_noise("Dit is een belangrijke bevinding over agents, zegt de auteur nu.")
    assert filter_noise("**Een lange vetgedrukte zin met inhoud die meer dan zestig tekens beslaat in totaal ja**") != ""


class FakeResponse:
    def __init__(self, content, finish="stop"):
        self.data = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        import json

        return json.dumps(self.data).encode()


def test_truncated_answer_is_model_error(monkeypatch):
    import urllib.request

    from sourceshelf.describe import openrouter_model

    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResponse("half antw", "length"))
    with pytest.raises(ModelError, match="truncated"):
        openrouter_model()("s", "t")


def test_enough_token_room_for_reasoning(monkeypatch):
    import json
    import urllib.request

    from sourceshelf.describe import openrouter_model

    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    seen = {}

    def fake_urlopen(req, timeout=0):
        seen.update(json.loads(req.data))
        return FakeResponse("klaar")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert openrouter_model()("s", "t") == "klaar"
    assert seen["max_tokens"] >= 4000


def test_summary_without_sentence_end_is_error(tmp_path):
    make_fiche(tmp_path)
    res = describe(tmp_path, "e1", FakeModel("Het rapport meet 12 procent winst omdat"), "d", "m")
    assert res.error and "truncated" in res.error
    assert "machine-summary" not in load_fiche(tmp_path / "fiches" / "e1.md").meta


def test_no_text_with_reason_is_not_an_error(tmp_path):
    (tmp_path / "fiches").mkdir()
    (tmp_path / "fiches" / "e2.md").write_text(
        "---\nid: e2\ntitle: T\nstatus: new\norigin: migration\n"
        "no-text: betaalmuur, geen open versie\nerror: 'summary: no text file to summarize'\n---\n\nnotitie\n"
    )
    model = FakeModel()
    res = describe(tmp_path, "e2", model, "2026-10-23", "m")
    assert not res.done and not res.error and "betaalmuur" in res.reason
    assert model.calls_made == []
    assert "error" not in load_fiche(tmp_path / "fiches" / "e2.md").meta


def test_no_text_leaves_other_error(tmp_path):
    (tmp_path / "fiches").mkdir()
    (tmp_path / "fiches" / "e3.md").write_text(
        "---\nid: e3\ntitle: T\nstatus: new\norigin: migration\nno-text: x\nerror: robots.txt verbiedt\n---\n\nn\n"
    )
    describe(tmp_path, "e3", FakeModel(), "2026-10-23", "m")
    assert load_fiche(tmp_path / "fiches" / "e3.md").meta["error"] == "robots.txt verbiedt"


def test_model_language_and_endpoint_are_configurable(monkeypatch):
    from sourceshelf import describe as d

    for k in ("KB_MODEL", "KB_SUMMARY_LANG", "KB_ENDPOINT"):
        monkeypatch.delenv(k, raising=False)
    assert d.model_name() == d.DEFAULT_MODEL
    assert "in English" in d.system_prompt()
    assert d.endpoint() == d.DEFAULT_ENDPOINT
    monkeypatch.setenv("KB_MODEL", "x/y")
    monkeypatch.setenv("KB_SUMMARY_LANG", "Dutch")
    monkeypatch.setenv("KB_ENDPOINT", "http://localhost:1/v1/chat/completions")
    assert d.model_name() == "x/y"
    assert "in Dutch" in d.system_prompt()
    assert d.endpoint().startswith("http://localhost")


def test_filter_noise_removes_english_preamble_and_closing():
    from sourceshelf.describe import filter_noise

    assert filter_noise("Here is the summary:\n\nThe report finds a gain.\n\nLet me know if you want more.") == "The report finds a gain."


def test_local_http_endpoint_end_to_end(monkeypatch):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from sourceshelf import describe as d

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["model"] = body["model"]
            reply = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "A local summary."}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("KB_ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
        monkeypatch.setenv("OPENROUTER_API" + "_KEY", "local")
        assert d.openrouter_model("llama3")("system", "text") == "A local summary."
    finally:
        server.shutdown()
        server.server_close()
    assert seen["model"] == "llama3"
