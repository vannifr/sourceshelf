import hashlib
import json
from pathlib import Path

from sourceshelf.fiche import load_fiches

EXTENSIONS = (".pdf", ".docx", ".md")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cache", ".claude", ".codegraph"}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_inventory(dir_: Path, root: Path, extensions: tuple[str, ...] = EXTENSIONS) -> list[dict]:
    known = {str(f.meta.get("sha256")): str(f.meta["id"]) for f in load_fiches(root / "fiches") if f.meta.get("sha256")}
    out = []
    for path in sorted(dir_.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        rel = path.relative_to(dir_)
        if any(d in SKIP_DIRS or d.startswith(".") for d in rel.parts[:-1]):
            continue
        digest = _sha(path)
        out.append({"path": str(path), "name": path.name, "ext": path.suffix.lower(), "size": path.stat().st_size,
                    "sha256": digest, "exists_already": known.get(digest) or known.get(f"sha256:{digest}")})
    return out


def write_manifest(items: list[dict], path: Path) -> None:
    path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")


def read_manifest(path: Path) -> list[dict]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.lstrip().startswith("{"):
            out.append({"path": line.strip()})
            continue
        try:
            item = json.loads(line)
        except ValueError as e:
            raise ValueError(f"{path}:{n}: invalid JSON: {e}") from e
        if not isinstance(item, dict) or not item.get("path"):
            raise ValueError(f"{path}:{n}: line without `path`")
        out.append(item)
    return out
