import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from sourceshelf import cli, extract
from tests.test_search import corpus, make

ROOT = Path(__file__).resolve().parent.parent


def run_cli(root, *args):
    return cli.main(["--root", str(root), *args])


def test_main_module_subprocess_exitcode_and_stderr(tmp_path):
    ok = subprocess.run([sys.executable, "-m", "sourceshelf", "--root", str(tmp_path), "check"], cwd=ROOT, check=False,
                        capture_output=True, text=True)
    assert ok.returncode == 0 and "0 error(s)" in ok.stdout
    bad = subprocess.run([sys.executable, "-m", "sourceshelf", "doesnotexist"], cwd=ROOT, check=False, capture_output=True, text=True)
    assert bad.returncode == 2 and "invalid choice" in bad.stderr
    none = subprocess.run([sys.executable, "-m", "sourceshelf"], cwd=ROOT, check=False, capture_output=True, text=True)
    assert none.returncode == 2 and "required" in none.stderr


def test_make_net_returns_net():
    from sourceshelf.net import Net
    assert isinstance(cli.make_net(), Net)


def test_add_local_missing_unknown_type_and_empty(tmp_path, capsys):
    assert run_cli(tmp_path, "add", str(tmp_path / "gone.pdf")) == 1
    assert "file does not exist" in capsys.readouterr().out
    unknown = tmp_path / "x.xyz"
    unknown.write_text("x")
    assert run_cli(tmp_path, "add", str(unknown)) == 1
    assert run_cli(tmp_path, "add") == 1
    assert "give a url" in capsys.readouterr().out


def test_add_from_manifest_paths(tmp_path, capsys):
    man = tmp_path / "m.jsonl"
    assert run_cli(tmp_path, "add", "--from", str(man)) == 1
    assert "manifest unreadable" in capsys.readouterr().out
    ok = tmp_path / "ok.txt"
    ok.write_text("transcription text", encoding="utf-8")
    unknown = tmp_path / "x.xyz"
    unknown.write_text("x")
    man.write_text("\n".join(json.dumps(i) for i in [
        {"path": str(tmp_path / "gone.txt")}, {"path": str(unknown)}, {"path": str(ok)},
        {"path": str(ok), "skip": True}, {"path": str(ok), "confidential": True}]), encoding="utf-8")
    assert run_cli(tmp_path, "add", "--from", str(man)) == 1
    out = capsys.readouterr().out
    assert "file does not exist" in out and "skipped" in out and "2 error(s)" in out
    assert run_cli(tmp_path, "add", "--from", str(man)) == 1
    assert "already existed" in capsys.readouterr().out


def test_add_from_unexpected_error_is_reported(tmp_path, capsys, monkeypatch):
    ok = tmp_path / "ok.txt"
    ok.write_text("text", encoding="utf-8")
    man = tmp_path / "m.jsonl"
    man.write_text(json.dumps({"path": str(ok)}), encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("boem")
    monkeypatch.setattr(cli, "add_local", boom)
    assert run_cli(tmp_path, "add", "--from", str(man)) == 1
    assert "RuntimeError: boem" in capsys.readouterr().out


def test_inventory_no_dir_and_ext(tmp_path, capsys):
    assert run_cli(tmp_path, "inventory", str(tmp_path / "gone")) == 1
    assert "not a directory" in capsys.readouterr().out
    source = tmp_path / "bron"
    source.mkdir()
    (source / "a.txt").write_text("a")
    out = tmp_path / "m.jsonl"
    assert run_cli(tmp_path, "inventory", str(source), "--ext", "txt", "--out", str(out)) == 0
    assert "1 file(s)" in capsys.readouterr().out


def test_describe_without_id_and_without_key(tmp_path, capsys, monkeypatch):
    assert run_cli(tmp_path, "describe") == 1
    assert "give an id or --all" in capsys.readouterr().out
    corpus(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert run_cli(tmp_path, "describe", "url-aaa") == 1
    assert "describe failed" in capsys.readouterr().out


def test_describe_error_paths_per_fiche(tmp_path, capsys, monkeypatch):
    corpus(tmp_path)
    monkeypatch.setattr(cli, "make_model", lambda: (lambda p: "Een samenvatting van drie zinnen. Twee. Drie.", "m"))

    def boom(*a, **k):
        raise RuntimeError("model gone")
    monkeypatch.setattr(cli, "describe", boom)
    assert run_cli(tmp_path, "describe", "url-aaa") == 1
    assert "RuntimeError: model gone" in capsys.readouterr().out


def test_get_unknown_and_known(tmp_path, capsys):
    corpus(tmp_path)
    assert run_cli(tmp_path, "get", "nonexistent") == 1
    assert run_cli(tmp_path, "get", "url-bbb") == 0
    out = capsys.readouterr().out
    assert "id: url-bbb" in out and "file path: data/papers/candidates/cats.pdf" in out


def test_check_warnings_and_unverified(tmp_path, capsys):
    make(tmp_path, "a", "Dubbel", "x", {"doi": "10.1/x", "machine-summary": {"text": "t", "model": "m"}})
    make(tmp_path, "b", "Dubbel", "y", {"doi": "10.1/X"})
    assert run_cli(tmp_path, "index") == 0
    capsys.readouterr()
    assert run_cli(tmp_path, "check", "--unverified") == 0
    out = capsys.readouterr().out
    assert "DOI 10.1/x in multiple" in out and "same title" in out and "without `verified: yes`" in out
    assert "\na\n" in out


def test_search_exact_json_and_no_result(tmp_path, capsys):
    corpus(tmp_path)
    assert run_cli(tmp_path, "search", "--json", "quokka") == 0
    data = json.loads(capsys.readouterr().out)
    assert data and set(data[0]) == {"id", "title", "score", "path", "page", "line", "snippet"}
    assert run_cli(tmp_path, "search", "--json", "zzznonexistent") == 1
    assert capsys.readouterr().out.strip() == "[]"
    assert run_cli(tmp_path, "search", "zzznonexistent") == 1
    assert run_cli(tmp_path, "search", "--exact", "zzznonexistent") == 1


def test_resolve_and_due_without_sources(tmp_path, capsys):
    corpus(tmp_path)
    assert run_cli(tmp_path, "resolve", "nonexistent") == 1
    assert run_cli(tmp_path, "due") == 1
    assert capsys.readouterr().out


def test_due_reports_crash_file_and_nothing_due(tmp_path, capsys):
    (tmp_path / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    (tmp_path / "digest").mkdir()
    (tmp_path / "digest" / "FOUT-backup.md").write_text("x", encoding="utf-8")
    assert run_cli(tmp_path, "due") == 0
    out = capsys.readouterr().out
    assert "nothing due" in out and "FOUT-backup.md" in out


def test_run_without_sources_is_error(tmp_path, capsys):
    assert run_cli(tmp_path, "run") == 1
    assert capsys.readouterr().out


def test_write_error_gives_message_and_exit_1(tmp_path, capsys, monkeypatch):
    def boom(root):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cli, "cmd_index", boom)
    assert run_cli(tmp_path, "index") == 1
    assert "write failed" in capsys.readouterr().out


def test_extract_pdf_fails_and_empty_text(tmp_path, monkeypatch):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"not a pdf")
    assert extract.pdf_to_text(pdf, tmp_path / "out" / "a.txt") is False

    class Proc:
        returncode = 0
    monkeypatch.setattr(extract.subprocess, "run", lambda *a, **k: (
        (tmp_path / "empty.txt").write_text("\f \n"), Proc())[1])
    assert extract.pdf_to_text(pdf, tmp_path / "empty.txt") is False


def test_extract_html_title_docx_subtitle(tmp_path):
    assert extract.html_to_text("<style>x{}</style><p>Hoi&amp;dag</p>\n\n\n<script>1</script>") == "Hoi&dag"
    assert extract.html_title("<title> A\n B </title>") == "A B"
    assert extract.html_title("<p>none</p>") is None
    assert extract.html_title("<title> </title>") is None
    broken = tmp_path / "k.docx"
    broken.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="not a readable docx"):
        extract.docx_to_text(broken)
    empty = tmp_path / "l.docx"
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   '<w:body><w:p/></w:body></w:document>')
    with pytest.raises(ValueError, match="contains no text"):
        extract.docx_to_text(empty)
    assert extract.subtitle_to_text("WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\n<b>Hallo</b>\nNOTE x\nSTYLE y\n") == "Hallo"


def test_extract_audio_without_model_and_empty_transcription(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(RuntimeError, match="not installed"):
        extract.audio_to_text(tmp_path / "a.mp3")

    class Seg:
        text = "  "

    class Model:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, *a, **k):
            return [Seg()], None

    module = type(sys)("faster_whisper")
    module.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    with pytest.raises(RuntimeError, match="empty"):
        extract.audio_to_text(tmp_path / "a.mp3")
    Seg.text = " hallo "
    assert extract.audio_to_text(tmp_path / "a.mp3") == "hallo"


def test_root_default_is_kb_root_then_cwd(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    (lib / "fiches").mkdir(parents=True)
    other = tmp_path / "other"
    (other / "fiches").mkdir(parents=True)
    (other / "fiches" / "broken.md").write_text("no frontmatter\n")
    env = {k: v for k, v in os.environ.items() if k != "KB_ROOT"}
    env["PYTHONPATH"] = str(ROOT)
    cmd = [sys.executable, "-m", "sourceshelf", "check"]
    via_env = subprocess.run(cmd, cwd=other, env={**env, "KB_ROOT": str(lib)}, check=False, capture_output=True, text=True)
    assert via_env.returncode == 0
    via_cwd = subprocess.run(cmd, cwd=other, env=env, check=False, capture_output=True, text=True)
    assert via_cwd.returncode == 1
    code_dir = subprocess.run(cmd, cwd=lib, env=env, check=False, capture_output=True, text=True)
    assert code_dir.returncode == 0
    monkeypatch.delenv("KB_ROOT", raising=False)
    assert cli.default_root() == Path.cwd()
    monkeypatch.setenv("KB_ROOT", str(lib))
    assert cli.default_root() == lib
