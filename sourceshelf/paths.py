from pathlib import Path


def data_path(root: Path, rel) -> Path | None:
    if not rel:
        return None
    base = (root / "data").resolve()
    path = (root / "data" / str(rel)).resolve()
    return path if path.is_relative_to(base) else None
