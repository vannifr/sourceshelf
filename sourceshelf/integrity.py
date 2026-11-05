import hashlib
import json
from pathlib import Path

from sourceshelf.fiche import FicheError, load_fiches


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _expected(root: Path, data: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for f in load_fiches(root / "fiches"):
        if f.meta.get("file") and f.meta.get("sha256"):
            expected.setdefault(str(f.meta["file"]), str(f.meta["sha256"]))
    for manifest in sorted(data.glob("migration*.jsonl")):
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            m = json.loads(line)
            target = m.get("target")
            if not target:
                continue
            if m.get("sha256"):
                expected.setdefault(str(target), str(m["sha256"]))
            if m.get("txt_rel") and m.get("sha256_txt"):
                expected.setdefault((Path(target).parent / m["txt_rel"]).as_posix(), str(m["sha256_txt"]))
    return expected


def check_data(root: Path, data: Path | None = None) -> list[str]:
    explicit = data is not None
    data = data if explicit else root / "data"
    if not data.is_dir():
        return [f"data directory missing: {data}"] if explicit else []
    try:
        expected = _expected(root, data)
    except (FicheError, ValueError, OSError) as error:
        return [f"integrity check cannot run: {error}"]
    errors = []
    for rel, sha in sorted(expected.items()):
        path = data / rel
        if not path.is_file():
            errors.append(f"data/{rel}: file missing")
        elif _sha(path) != sha:
            errors.append(f"data/{rel}: sha256 differs from fiche or manifest")
    return errors
