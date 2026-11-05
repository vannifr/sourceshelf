import json
from pathlib import Path

from sourceshelf.fiche import load_fiches, validate_dir, write_atomic


def _rows(root: Path) -> list[dict]:
    rows = [
        {
            "id": f.meta["id"],
            "title": f.meta["title"],
            "year": f.meta.get("year"),
            "kind": f.meta.get("kind"),
            "status": f.meta["status"],
            "sha256": f.meta.get("sha256"),
        }
        for f in load_fiches(root / "fiches")
    ]
    rows.sort(key=lambda r: r["id"])
    return rows


def _text(rows: list[dict]) -> str:
    return json.dumps(rows, ensure_ascii=False, indent=1) + "\n"


def index_drift(root: Path) -> list[str]:
    target = root / "index" / "index.json"
    if not target.is_file():
        return []
    if target.read_text(encoding="utf-8") != _text(_rows(root)):
        return ["index/index.json differs from the fiches: run `sourceshelf index` and commit the result"]
    return []


def write_index(root: Path) -> list[str]:
    errors = validate_dir(root / "fiches")
    if errors:
        return errors
    target = root / "index" / "index.json"
    write_atomic(target, _text(_rows(root)))
    return []
