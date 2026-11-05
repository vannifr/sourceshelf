import json
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "examples" / "library"


def run(*args):
    return subprocess.run([sys.executable, "-m", "sourceshelf", "--root", str(LIB), *args], capture_output=True, check=False,
                          text=True, cwd=LIB.parent.parent)


def test_example_library_checks_clean():
    r = run("check")
    assert r.returncode == 0, r.stdout + r.stderr


def test_example_search_finds_fiche():
    r = run("search", "carbon nitrogen", "--json")
    assert r.returncode == 0
    assert "ex-compost-guide" in {x["id"] for x in json.loads(r.stdout)}


def test_example_confidential_text_not_searchable():
    r = run("search", "memo", "--json")
    assert r.returncode == 0
    for hit in json.loads(r.stdout):
        assert hit["snippet"] == ""
