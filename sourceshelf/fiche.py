import re
from dataclasses import dataclass
from pathlib import Path

import yaml

STATUSES = ("new", "seen", "ignored", "used", "removed")
KINDS = ("peer-reviewed", "preprint", "report", "news", "blog", "standard", "other")
YESNO = ("confidential", "open-access", "text-layer")
REQUIRED = ("id", "title", "status", "origin")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SUMMARY_FLAGS = ("verified", "truncated")
YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class FicheError(Exception):
    pass


def _flag(value):
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if isinstance(value, str):
        low = value.strip().lower()
        return {"y": "yes", "1": "yes", "on": "yes", "true": "yes", "n": "no", "0": "no", "off": "no", "false": "no"}.get(low, low if low in ("yes", "no") else value)
    return value


@dataclass
class Fiche:
    path: Path
    meta: dict
    text: str


def split_fiche(content: str, path: Path | str = "") -> tuple[dict, str]:
    if not content.startswith("---\n"):
        raise FicheError(f"{path}: no frontmatter")
    head, separator, rest = content[4:].partition("\n---")
    if not separator:
        raise FicheError(f"{path}: frontmatter not closed")
    try:
        meta = yaml.load(head, Loader=YAML_LOADER) or {}
    except yaml.YAMLError as error:
        raise FicheError(f"{path}: invalid YAML ({error})") from error
    if not isinstance(meta, dict):
        raise FicheError(f"{path}: frontmatter is not a mapping")
    summary = meta.get("machine-summary")
    if isinstance(summary, dict):
        meta["machine-summary"] = {k: (_flag(v) if k in SUMMARY_FLAGS else v) for k, v in summary.items()}
    for key in YESNO:
        if isinstance(meta.get(key), (bool, str)):
            meta[key] = _flag(meta[key])
    return meta, rest.lstrip("\n")


def load_fiche(path: Path) -> Fiche:
    meta, text = split_fiche(path.read_text(encoding="utf-8"), path)
    return Fiche(path, meta, text)


def load_fiches(dir_: Path) -> list[Fiche]:
    return [load_fiche(p) for p in sorted(dir_.rglob("*.md"))]


def validate(fiche: Fiche) -> list[str]:
    m = fiche.meta
    name = fiche.path.name
    errors = [f"{name}: required field missing: {v}" for v in REQUIRED if m.get(v) in (None, "")]
    fid = m.get("id")
    if fid not in (None, ""):
        if not ID_PATTERN.match(str(fid)):
            errors.append(f"{name}: invalid id {fid!r}")
        if fiche.path.stem != str(fid):
            errors.append(f"{name}: file name must be {fid}.md")
    if m.get("status") not in (None, "") and m["status"] not in STATUSES:
        errors.append(f"{name}: invalid status {m['status']!r}")
    if m.get("kind") not in (None, "") and m["kind"] not in KINDS:
        errors.append(f"{name}: invalid kind {m['kind']!r}")
    if m.get("confidential") not in (None, "", "yes", "no", "true", "false"):
        errors.append(f"{name}: confidential must be yes or no, got {m['confidential']!r}")
    sha = m.get("sha256")
    if sha not in (None, "") and not SHA_PATTERN.match(str(sha)):
        errors.append(f"{name}: invalid sha256")
    if m.get("file") and not sha:
        errors.append(f"{name}: file requires sha256")
    return errors


def validate_dir(dir_: Path) -> list[str]:
    try:
        fiches = load_fiches(dir_)
    except FicheError as error:
        return [str(error)]
    errors: list[str] = []
    seen: dict[str, Path] = {}
    for f in fiches:
        errors += validate(f)
        fid = str(f.meta.get("id"))
        if fid in seen:
            errors.append(f"{f.path.name}: duplicate id {fid} (also in {seen[fid]})")
        seen[fid] = f.path
    return errors


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_fiche(path: Path, meta: dict, text: str = "") -> None:
    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000).rstrip("\n")
    write_atomic(path, f"---\n{head}\n---\n\n{text}".rstrip("\n") + "\n")
