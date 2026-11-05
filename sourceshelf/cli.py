import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from sourceshelf.add import UnknownType, add_local, add_source
from sourceshelf.describe import ModelError, describe, model_name, openrouter_model
from sourceshelf.extract import MissingTool
from sourceshelf.fiche import FicheError, load_fiches, validate_dir
from sourceshelf.index import index_drift, write_index
from sourceshelf.integrity import check_data
from sourceshelf.inventory import EXTENSIONS, build_inventory, read_manifest, write_manifest
from sourceshelf.lock import Busy, write_lock
from sourceshelf.net import VERSION, Net
from sourceshelf.run import LOCKED, SourcesError, due, run
from sourceshelf.search import resolve, search, search_exact
from sourceshelf.web import serve

try:
    from sourceshelf import private_cmds
except ImportError:
    private_cmds = None


def default_root() -> Path:
    env = os.environ.get("KB_ROOT")
    return Path(env).expanduser() if env else Path.cwd()


def make_net() -> Net:
    return Net()


def cmd_add(root: Path, url: str) -> int:
    date = datetime.now().astimezone().date().isoformat()
    if "://" not in url:
        local = Path(url).expanduser()
        if not local.is_file():
            print(f"file does not exist: {url}")
            return 1
        try:
            res = add_local(local, root, date)
        except UnknownType as error:
            print(error)
            return 1
    else:
        res = add_source(url, root, make_net(), date)
    if not res.new:
        print(f"already exists: {res.id}")
        return 0
    cmd_index(root)
    if res.error:
        print(f"error for {res.id}: {res.error}")
        return 1
    print(f"added: {res.id}")
    return 0


def cmd_add_from(root: Path, manifest: Path) -> int:
    try:
        items = read_manifest(manifest)
    except (OSError, ValueError) as error:
        print(f"manifest unreadable: {error}")
        return 1
    date = datetime.now().astimezone().date().isoformat()
    new = existed = skipped = errors = 0
    for item in items:
        if item.get("skip") or item.get("confidential"):
            skipped += 1
            print(f"skipped: {item['path']}")
            continue
        source = str(item["path"])
        local = Path(source).expanduser()
        if "://" not in source and not local.is_file():
            errors += 1
            print(f"error for {item['path']}: file does not exist")
            continue
        try:
            res = add_source(source, root, make_net(), date) if "://" in source else add_local(local, root, date)
        except UnknownType as error:
            errors += 1
            print(f"error for {item['path']}: {error}")
            continue
        except Exception as error:  # noqa: BLE001
            errors += 1
            print(f"error for {item['path']}: {type(error).__name__}: {error}")
            continue
        if not res.new:
            existed += 1
        elif res.error:
            new += 1
            errors += 1
            print(f"error for {res.id}: {res.error}")
        else:
            new += 1
    if new:
        cmd_index(root)
    print(f"{new} new, {existed} already existed, {skipped} skipped, {errors} error(s)")
    return 1 if errors else 0


def cmd_inventory(root: Path, args) -> int:
    dir_ = args.dir.expanduser()
    if not dir_.is_dir():
        print(f"not a directory: {args.dir}")
        return 1
    ext = tuple(e if e.startswith(".") else f".{e}" for e in args.ext) if args.ext else EXTENSIONS
    items = build_inventory(dir_, root, ext)
    new = [i for i in items if not i["exists_already"]]
    write_manifest(new if args.only_new else items, args.out)
    print(f"{len(items)} file(s), {len(new)} new, {len(items) - len(new)} already exist; manifest: {args.out}")
    return 0


def make_model():
    return openrouter_model(), model_name()


def cmd_describe(root: Path, args) -> int:
    fiches = load_fiches(root / "fiches")
    if args.all:
        ids = [str(f.meta["id"]) for f in fiches]
    elif args.id:
        ids = [args.id]
    else:
        print("give an id or --all")
        return 1
    model, name = None, model_name()
    if not args.dry_run:
        try:
            model, name = make_model()
        except ModelError as error:
            print(f"describe failed: {error}")
            return 1
    date = datetime.now().astimezone().date().isoformat()
    done = errors = 0
    for fid in ids:
        try:
            res = describe(root, fid, model, date, name, again=args.again, dry_run=args.dry_run)
        except Exception as error:  # noqa: BLE001
            errors += 1
            print(f"error for {fid}: {type(error).__name__}: {error}")
            continue
        if res.error:
            errors += 1
            print(f"error for {fid}: {res.error}")
        elif res.done:
            done += 1
            print(f"described: {fid}")
        elif args.dry_run or not args.all:
            print(f"{fid}: {res.reason}")
    if done:
        cmd_index(root)
    print(f"{done} described, {errors} error(s)")
    return 1 if errors else 0


def cmd_get(root: Path, fid: str) -> int:
    for f in load_fiches(root / "fiches"):
        if f.meta.get("id") == fid:
            for key, value in f.meta.items():
                print(f"{key}: {value}")
            for key, label in (("file", "file"), ("text", "text")):
                if f.meta.get(key):
                    print(f"{label} path: data/{f.meta[key]}")
            return 0
    print(f"unknown id: {fid}")
    return 1


def doi_warnings(root: Path) -> list[str]:
    by_doi: dict[str, list[str]] = {}
    for f in load_fiches(root / "fiches"):
        if f.meta.get("doi"):
            by_doi.setdefault(str(f.meta["doi"]).lower(), []).append(str(f.meta["id"]))
    return [f"warning: DOI {d} in multiple fiches: {', '.join(i)}" for d, i in sorted(by_doi.items()) if len(i) > 1]


def duplicate_warnings(root: Path) -> list[str]:
    by_title: dict[str, list[str]] = {}
    for f in load_fiches(root / "fiches"):
        t = " ".join(str(f.meta.get("title", "")).lower().split())
        if t:
            by_title.setdefault(t, []).append(str(f.meta["id"]))
    return [f"warning: same title '{t}' in multiple fiches: {', '.join(i)}" for t, i in sorted(by_title.items()) if len(i) > 1]


def unverified(root: Path) -> list[str]:
    out = []
    for f in load_fiches(root / "fiches"):
        summ = f.meta.get("machine-summary")
        if isinstance(summ, dict) and str(summ.get("verified", "")) != "yes":
            out.append(str(f.meta["id"]))
    return out


def cmd_check(root: Path, data: Path | None = None, list_unverified: bool = False) -> int:
    errors = validate_dir(root / "fiches")
    if not errors:
        errors = check_data(root, data) + ([] if data else index_drift(root))
    for f in errors:
        print(f)
    if not errors:
        for w in doi_warnings(root) + duplicate_warnings(root):
            print(w)
        ids = unverified(root)
        if ids:
            print(f"warning: {len(ids)} machine summaries without `verified: yes` (sourceshelf check --unverified lists the ids)")
        if list_unverified:
            for i in ids:
                print(i)
    print(f"{len(errors)} error(s)")
    return 1 if errors else 0


def cmd_index(root: Path) -> int:
    errors = write_index(root)
    for f in errors:
        print(f)
    return 1 if errors else 0


def cmd_search(root: Path, args) -> int:
    query = " ".join(args.query)
    if args.exact:
        out = search_exact(root, query)
        print(out, end="")
        if not out:
            print("no results", file=sys.stderr)
        return 0 if out else 1
    res = search(root, query, args.limit, fields=args.fields, kind=args.kind)
    if args.json:
        print(json.dumps([{"id": t.id, "title": t.title, "score": round(t.score, 4), "path": t.path, "page": t.page,
                           "line": t.line, "snippet": t.snippet} for t in res], ensure_ascii=False))
        return 0 if res else 1
    if not res:
        print("no results", file=sys.stderr)
    for t in res:
        place = f"p. {t.page}" if t.page else (f"r. {t.line}" if t.line else "")
        print(f"{t.id}  {t.score:.2f}  {t.title}")
        print(f"    {t.path} {place}  {t.snippet}".rstrip())
    return 0 if res else 1


def cmd_resolve(root: Path, key: str) -> int:
    ids = resolve(root, key)
    for i in ids:
        print(i)
    return 0 if ids else 1


def cmd_due(root: Path) -> int:
    now = datetime.now().astimezone()
    try:
        items_due = due(root, now)
    except SourcesError as error:
        print(error)
        return 1
    for b, reason in items_due:
        print(f"{b.id}: {reason}")
    if not items_due:
        print("nothing due")
    for crash in sorted((root / "digest").glob("FOUT*.md")):
        print(f"note: digest/{crash.name} exists, an earlier run or backup failed")
    return 0


def cmd_run(root: Path, source: str | None) -> int:
    try:
        res = run(root, make_net(), datetime.now().astimezone(), source_id=source)
    except SourcesError as error:
        print(error)
        return 1
    if res.code == LOCKED:
        print("skipped: another run is busy (lock)")
        return res.code
    print(f"{res.new} new, {res.failed} failed, {len(res.failed_sources)} source(s) failed, "
          f"remaining {res.remaining}")
    return 0


WRITING = ("add", "describe", "index")
NEEDS_LIBRARY = ("check", "index", "get", "describe", "due", "search", "resolve", "serve")


def main(argv: list[str] | None = None) -> int:
    top = argparse.ArgumentParser(prog="sourceshelf")
    top.add_argument("--version", action="version", version=f"sourceshelf {VERSION}")
    top.add_argument("--root", type=Path, default=None, help="library folder (default: $KB_ROOT, else the current directory)")
    sub = top.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check", help="verify fiches, files and index (exit 1 on errors)")
    ck.add_argument("--data", type=Path)
    ck.add_argument("--unverified", action="store_true")
    sub.add_parser("index", help="rebuild index/index.json")
    ad = sub.add_parser("add", help="add a URL, arXiv id or local file as a source")
    ad.add_argument("url", nargs="?", help="URL or path to a local file")
    ad.add_argument("--from", dest="from_manifest", type=Path, help="jsonl manifest from `sourceshelf inventory`")
    iv = sub.add_parser("inventory", help="list files in a folder as a jsonl manifest for `add --from`")
    iv.add_argument("dir", type=Path)
    iv.add_argument("--out", type=Path, default=Path("inventory.jsonl"))
    iv.add_argument("--ext", nargs="+")
    iv.add_argument("--only-new", action="store_true")
    sub.add_parser("get", help="print one fiche by id").add_argument("id")
    de = sub.add_parser("describe", help="write an LLM summary into the fiche (external model; never for confidential)")
    de.add_argument("id", nargs="?")
    de.add_argument("--all", action="store_true")
    de.add_argument("--again", action="store_true")
    de.add_argument("--dry-run", action="store_true")
    sub.add_parser("due", help="list sources that are due for a fetch")
    sub.add_parser("run", help="fetch due sources, index and write the digest").add_argument("source", nargs="?")
    sr = sub.add_parser("search", help="BM25 search (exit 1 when nothing is found)")
    sr.add_argument("query", nargs="+")
    sr.add_argument("--exact", action="store_true")
    sr.add_argument("--limit", type=int, default=5)
    sr.add_argument("--fields", action="store_true")
    sr.add_argument("--kind", choices=["own", "external"])
    sr.add_argument("--json", action="store_true")
    sub.add_parser("resolve", help="map an id, e-id or legacy path to fiche ids").add_argument("key")
    if private_cmds:
        private_cmds.add_parsers(sub)
    sv = sub.add_parser("serve", help="read-only local web interface on 127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    args = top.parse_args(argv)
    if args.root is None:
        args.root = default_root()
    if args.cmd in NEEDS_LIBRARY and not args.root.is_dir():
        print(f"library folder not found: {args.root}; use --root or KB_ROOT", file=sys.stderr)
        return 2
    writes = args.cmd in WRITING and not (args.cmd == "describe" and args.dry_run)
    if private_cmds and private_cmds.writes(args):
        writes = True
    try:
        if writes:
            with write_lock(args.root):
                return _execute(args)
        return _execute(args)
    except Busy as error:
        print(error)
        return 1
    except FicheError as error:
        print(error)
        return 1
    except MissingTool as error:
        print(error, file=sys.stderr)
        return 1
    except OSError as error:
        print(f"write failed: {error}")
        return 1


def _execute(args) -> int:
    if args.cmd == "serve":
        return serve(args.root, args.port)
    if private_cmds and args.cmd in private_cmds.COMMANDS:
        return private_cmds.execute(args)
    if args.cmd == "due":
        return cmd_due(args.root)
    if args.cmd == "run":
        return cmd_run(args.root, args.source)
    if args.cmd == "search":
        return cmd_search(args.root, args)
    if args.cmd == "resolve":
        return cmd_resolve(args.root, args.key)
    if args.cmd == "add":
        if args.from_manifest:
            return cmd_add_from(args.root, args.from_manifest)
        if not args.url:
            print("give a url or path, or --from <manifest>")
            return 1
        return cmd_add(args.root, args.url)
    if args.cmd == "inventory":
        return cmd_inventory(args.root, args)
    if args.cmd == "describe":
        return cmd_describe(args.root, args)
    if args.cmd == "get":
        return cmd_get(args.root, args.id)
    if args.cmd == "check":
        return cmd_check(args.root, args.data, args.unverified)
    return {"check": cmd_check, "index": cmd_index}[args.cmd](args.root)
