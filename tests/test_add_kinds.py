import io
import zipfile

import pytest

from sourceshelf import cli
from sourceshelf.add import UnknownType, add_local
from sourceshelf.fiche import load_fiches
from sourceshelf.search import search
from tests.conftest import sha

DOCX_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>Kop van het document</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>Tweede alinea met</w:t></w:r><w:r><w:t xml:space=\"preserve\"> kwartelei</w:t></w:r></w:p></w:body></w:document>"
)


def make_docx(content: str = DOCX_XML) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", content)
    return buf.getvalue()


def fiche_and_text(root):
    f = load_fiches(root / "fiches")
    assert len(f) == 1
    m = f[0].meta
    text = (root / "data" / m["text"]).read_text(encoding="utf-8") if m.get("text") else None
    return f[0], m, text


def test_docx_becomes_text_with_machine_marking(tmp_path):
    content = make_docx()
    source = tmp_path / "Mijn_Notitie.docx"
    source.write_bytes(content)
    root = tmp_path / "library"
    res = add_local(source, root, "2026-10-21")
    assert res.new and res.error is None and res.id == f"local-{sha(content)[:12]}"
    f, m, text = fiche_and_text(root)
    assert m["title"] == "Mijn Notitie" and m["kind"] == "other" and m["status"] == "new"
    assert m["origin"] == "kb add (local)" and m["source-file"] == "Mijn_Notitie.docx" and "url" not in m
    assert m["file"].startswith("source-material/kb-add/") and m["sha256"] == sha(content)
    assert (root / "data" / m["file"]).read_bytes() == content
    assert "Kop van het document" in text and "Tweede alinea met kwartelei" in text
    assert "machine:" in f.text and "citation-value: only after verification against the source" in f.text
    assert search(root, "kwartelei")[0].id == res.id


def test_docx_same_content_no_duplicate(tmp_path):
    a, b = tmp_path / "a.docx", tmp_path / "b.docx"
    a.write_bytes(make_docx())
    b.write_bytes(make_docx())
    root = tmp_path / "library"
    assert add_local(a, root, "2026-10-21").new
    assert not add_local(b, root, "2026-10-21").new
    assert len(load_fiches(root / "fiches")) == 1


def test_broken_docx_gives_error_line_and_status_new(tmp_path):
    source = tmp_path / "broken.docx"
    source.write_bytes(b"not a zip")
    root = tmp_path / "library"
    res = add_local(source, root, "2026-10-21")
    assert res.new and res.error
    _, m, _ = fiche_and_text(root)
    assert m["status"] == "new" and m["error"] == res.error and "text" not in m


@pytest.mark.parametrize("name", ["gesprek.md", "gesprek.txt"])
def test_transcript_text_stays_source_and_is_searchable(tmp_path, name):
    content = b"Spreker 1: we bespreken de ooievaar.\nSpreker 2: akkoord.\n"
    source = tmp_path / name
    source.write_bytes(content)
    root = tmp_path / "library"
    res = add_local(source, root, "2026-10-21")
    f, m, text = fiche_and_text(root)
    assert text == content.decode() and m["file"] == m["text"] and m["kind"] == "other"
    assert "machine:" in f.text and "citation-value" in f.text
    assert search(root, "ooievaar")[0].id == res.id


def test_vtt_timestamps_removed_from_text_original_kept(tmp_path):
    content = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\nhallo flamingo\n\n2\n00:00:04.000 --> 00:00:05.000\ntot ziens\n"
    source = tmp_path / "ondertitel.vtt"
    source.write_text(content)
    root = tmp_path / "library"
    add_local(source, root, "2026-10-21")
    _, m, text = fiche_and_text(root)
    assert text.split() == ["hallo", "flamingo", "tot", "ziens"]
    assert (root / "data" / m["file"]).read_text() == content


def test_audio_transcription_marked_as_machine(tmp_path):
    content = b"ID3 nep audio"
    source = tmp_path / "Aflevering_1.mp3"
    source.write_bytes(content)
    root = tmp_path / "library"
    res = add_local(source, root, "2026-10-21", transcribe=lambda path: "dit is de leeuwerik")
    assert res.error is None
    f, m, text = fiche_and_text(root)
    assert text == "dit is de leeuwerik" and m["file"].endswith(".mp3") and m["text"].endswith(".txt")
    assert "transcription: faster-whisper small int8" in f.text and "citation-value: only after verification" in f.text
    assert search(root, "leeuwerik")[0].id == res.id


def test_audio_transcription_failed_gives_error_line(tmp_path):
    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFF")
    root = tmp_path / "library"

    def fail(path):
        raise RuntimeError("model missing")

    res = add_local(source, root, "2026-10-21", transcribe=fail)
    assert res.new and "model missing" in res.error
    _, m, _ = fiche_and_text(root)
    assert m["status"] == "new" and "model missing" in m["error"] and "text" not in m
    assert (root / "data" / m["file"]).read_bytes() == b"RIFF"


def test_unknown_type_rejected_without_fiche(tmp_path):
    source = tmp_path / "x.xyz"
    source.write_bytes(b"data")
    root = tmp_path / "library"
    with pytest.raises(UnknownType, match=r"\.xyz"):
        add_local(source, root, "2026-10-21")
    assert not list((root / "fiches").glob("*.md")) if (root / "fiches").exists() else True


def test_cli_unknown_type_and_error_exit_1(tmp_path, capsys):
    source = tmp_path / "x.xyz"
    source.write_bytes(b"data")
    root = tmp_path / "library"
    assert cli.main(["--root", str(root), "add", str(source)]) == 1
    assert "unknown file type" in capsys.readouterr().out
    broken = tmp_path / "k.docx"
    broken.write_bytes(b"not a zip")
    assert cli.main(["--root", str(root), "add", str(broken)]) == 1
    assert "error for local-" in capsys.readouterr().out


@pytest.mark.parametrize("name,content", [("empty.txt", b"  \n"), ("empty.vtt", b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n")])
def test_empty_text_gives_error_line(tmp_path, name, content):
    source = tmp_path / name
    source.write_bytes(content)
    root = tmp_path / "library"
    res = add_local(source, root, "2026-10-21")
    assert res.new and res.error
    _, m, _ = fiche_and_text(root)
    assert m["status"] == "new" and m["error"] and "text" not in m


def test_unexpected_transcription_error_gives_error_line_and_retry_possible(tmp_path):
    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFF")
    root = tmp_path / "library"

    def fail(path):
        raise KeyError("av")

    res = add_local(source, root, "2026-10-21", transcribe=fail)
    assert res.new and res.error
    res2 = add_local(source, root, "2026-10-21", transcribe=lambda p: "hallo wereld")
    assert res2.new and not res2.error and res2.id == res.id
    _, m, _ = fiche_and_text(root)
    assert "error" not in m and m["text"]
