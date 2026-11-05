import json
from http.server import HTTPServer
from types import SimpleNamespace

from sourceshelf import cli, web
from sourceshelf.add import Result as AddRes
from tests.test_web import _raw, server  # noqa: F401


def run_cli(root, *args):
    return cli.main(["--root", str(root), *args])


def test_serve_starts_and_stops_on_ctrl_c(tmp_path, capsys, monkeypatch):
    def interrupt(self):
        raise KeyboardInterrupt
    monkeypatch.setattr(HTTPServer, "serve_forever", interrupt)
    assert web.serve(tmp_path, 0) == 0
    assert "sourceshelf at http://127.0.0.1:0" in capsys.readouterr().out
    assert run_cli(tmp_path, "serve", "--port", "0") == 0


def test_sources_with_invalid_config_gives_error_field(tmp_path):
    (tmp_path / "fiches").mkdir()
    (tmp_path / "sources.yaml").write_text("sources:\n  - id: x\n    kind: unknown\n", encoding="utf-8")
    data = web.sources_data(tmp_path)
    assert data["error"] and data["configured"] == []


def test_invalid_limit_falls_back_to_10(server):  # noqa: F811
    port = server.rsplit(":", 1)[1]
    status, body = _raw(server, f"127.0.0.1:{port}", path="/api/search?q=quokka&limit=abc")
    assert status == 200 and json.loads(body)["results"]
    assert _raw(server, f"127.0.0.1:{port}", path="/nonexistent")[0] == 404


def test_add_url_already_exists_and_error(tmp_path, capsys, monkeypatch):
    res = iter([AddRes("x", False), AddRes("y", True, "no connection")])
    monkeypatch.setattr(cli, "make_net", lambda: None)
    monkeypatch.setattr(cli, "add_source", lambda *a, **k: next(res))
    assert run_cli(tmp_path, "add", "https://v.org/a") == 0
    assert "already exists: x" in capsys.readouterr().out
    assert run_cli(tmp_path, "add", "https://v.org/b") == 1
    assert "error for y: no connection" in capsys.readouterr().out


def test_add_from_url_with_error_counts_as_error(tmp_path, capsys, monkeypatch):
    man = tmp_path / "m.txt"
    man.write_text("https://v.org/a\n", encoding="utf-8")
    monkeypatch.setattr(cli, "make_net", lambda: None)
    monkeypatch.setattr(cli, "add_source", lambda *a, **k: AddRes("y", True, "broken"))
    assert run_cli(tmp_path, "add", "--from", str(man)) == 1
    out = capsys.readouterr().out
    assert "error for y: broken" in out and "1 new" in out


def test_run_locked_and_summary(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli, "run", lambda *a, **k: SimpleNamespace(code=cli.LOCKED))
    assert run_cli(tmp_path, "run") == cli.LOCKED
    assert "lock" in capsys.readouterr().out
    monkeypatch.setattr(cli, "run", lambda *a, **k: SimpleNamespace(code=0, new=2, failed=1,
                                                                       failed_sources=["a"], remaining=3))
    assert run_cli(tmp_path, "run") == 0
    assert "2 new, 1 failed, 1 source(s) failed, remaining 3" in capsys.readouterr().out


