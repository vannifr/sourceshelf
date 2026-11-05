import hashlib
import json
from pathlib import Path

from sourceshelf.cli import cmd_check, main
from sourceshelf.fiche import write_fiche
from sourceshelf.integrity import check_data


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _setup(tmp: Path):
    root = tmp / "library"
    data = root / "data"
    (data / "pdf").mkdir(parents=True)
    content = b"content of the source file"
    (data / "pdf" / "a.pdf").write_bytes(content)
    write_fiche(root / "fiches" / "a.md", {"id": "a", "title": "A", "status": "new", "origin": "test",
                                             "file": "pdf/a.pdf", "sha256": _sha(content)}, "")
    return root, data


def test_clean_state_gives_no_errors(tmp_path):
    root, _ = _setup(tmp_path)
    assert check_data(root) == []
    assert cmd_check(root) == 0


def test_missing_file_reported(tmp_path):
    root, data = _setup(tmp_path)
    (data / "pdf" / "a.pdf").unlink()
    errors = check_data(root)
    assert len(errors) == 1 and "file missing" in errors[0] and "pdf/a.pdf" in errors[0]
    assert cmd_check(root) == 1


def test_changed_file_reported(tmp_path):
    root, data = _setup(tmp_path)
    (data / "pdf" / "a.pdf").write_bytes(b"different")
    errors = check_data(root)
    assert len(errors) == 1 and "sha256 differs" in errors[0] and "pdf/a.pdf" in errors[0]
    assert cmd_check(root) == 1


def test_manifest_target_and_transcript_checked(tmp_path):
    root, data = _setup(tmp_path)
    (data / "source-material" / "x" / "transcripts").mkdir(parents=True)
    (data / "source-material" / "x" / "ep.mp3").write_bytes(b"audio")
    (data / "source-material" / "x" / "transcripts" / "ep.txt").write_bytes(b"text")
    line = {"rel": "ep.mp3", "target": "source-material/x/ep.mp3", "sha256": _sha(b"audio"),
             "txt_rel": "transcripts/ep.txt", "sha256_txt": _sha(b"text")}
    (data / "migration-source-material.jsonl").write_text(json.dumps(line) + "\n" + json.dumps({"done": "ep.mp3"}) + "\n")
    assert check_data(root) == []
    (data / "source-material" / "x" / "transcripts" / "ep.txt").write_bytes(b"changed")
    (data / "source-material" / "x" / "ep.mp3").unlink()
    errors = check_data(root)
    assert len(errors) == 2


def test_skipped_without_data_dir(tmp_path):
    root = tmp_path / "library"
    write_fiche(root / "fiches" / "a.md", {"id": "a", "title": "A", "status": "new", "origin": "test"}, "")
    assert check_data(root) == []


def test_explicit_data_dir_and_cli(tmp_path, capsys):
    root, data = _setup(tmp_path)
    restored_dir = tmp_path / "restored"
    restored_dir.mkdir()
    assert main(["--root", str(root), "check", "--data", str(restored_dir)]) == 1
    assert "file missing" in capsys.readouterr().out
    assert main(["--root", str(root), "check", "--data", str(data)]) == 0
