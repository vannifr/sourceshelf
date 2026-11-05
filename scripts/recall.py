import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sourceshelf.cli import default_root
from sourceshelf.search import search

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = next((p for p in (ROOT / "tests" / "recall" / "vragen-nl.tsv", ROOT / "examples" / "recall.tsv") if p.is_file()),
               ROOT / "examples" / "recall.tsv")


def read_file(path: Path) -> list[list[str]]:
    return [r.split("\t") for r in path.read_text(encoding="utf-8").splitlines()[1:] if r.strip()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", type=Path, default=DEFAULT)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--fields", action="store_true")
    ap.add_argument("--min-top", type=int, default=0, help="exit 1 if top-k has fewer hits")
    ap.add_argument("--root", type=Path, default=None, help="library folder (default: $KB_ROOT, else cwd)")
    args = ap.parse_args(argv)
    args.root = args.root or default_root()
    questions = read_file(args.questions)
    top1 = topk = 0
    for qid, type_, query, expected in questions:
        ex = expected.split("|")
        ids = [t.id for t in search(args.root, query, args.k, fields=args.fields)]
        pos = next((i + 1 for i, fid in enumerate(ids) if fid in ex), 0)
        top1 += pos == 1
        topk += pos > 0
        print(f"{qid}\t{type_}\t{pos or '-'}\t{query[:70]}")
    n = len(questions)
    print(f"questions={n} top1={top1} top{args.k}={topk} ({100 * topk // n}%)")
    return 1 if topk < args.min_top else 0


if __name__ == "__main__":
    raise SystemExit(main())
