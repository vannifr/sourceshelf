from sourceshelf.search import search
from tests.test_search import make


def long_text(word):
    return " ".join(["vulwoord"] * 400) + f" {word}"


def test_fields_lift_summary_above_long_text(tmp_path):
    make(tmp_path, "url-short", "Short", "", {"machine-summary": {"text": "gaat over kwaliteitsborging"}}, "weinig")
    make(tmp_path, "url-long", "Long", "", None, long_text("kwaliteitsborging") + " kwaliteitsborging")
    make(tmp_path, "url-zero", "Zero", "", None, "niets")
    assert search(tmp_path, "kwaliteitsborging", fields=True)[0].id == "url-short"


def test_fields_confidential_uses_no_summary(tmp_path):
    make(tmp_path, "url-secret", "Secret", "", {"confidential": "yes", "machine-summary": {"text": "zeldzaamwoord"}}, "x")
    assert search(tmp_path, "zeldzaamwoord", fields=True) == []


def test_kind_filter_own_and_external(tmp_path):
    make(tmp_path, "url-ext", "External", "", {"origin": "kb add"}, "quokka")
    make(tmp_path, "local-own", "Own", "", {"origin": "kb add (local)"}, "quokka")
    assert [t.id for t in search(tmp_path, "quokka", kind="external")] == ["url-ext"]
    assert [t.id for t in search(tmp_path, "quokka", kind="own")] == ["local-own"]
    assert len(search(tmp_path, "quokka")) == 2


def test_check_reports_duplicate_title_and_unverified(tmp_path, capsys):
    from sourceshelf import cli
    make(tmp_path, "url-a", "Zelfde", "", {"machine-summary": {"text": "x"}}, "one")
    make(tmp_path, "url-b", "zelfde", "", {"machine-summary": {"text": "y", "verified": "yes"}}, "two")
    cli.cmd_check(tmp_path, None, True)
    out = capsys.readouterr().out
    assert "same title 'zelfde' in multiple fiches: url-a, url-b" in out
    assert "1 machine summaries without" in out and "\nurl-a\n" in out


def test_cli_fields_and_kind(tmp_path, capsys):
    from sourceshelf import cli
    make(tmp_path, "url-ext", "External", "", {"origin": "kb add"}, "quokka")
    make(tmp_path, "local-bm", "Source", "", {"origin": "migration-source-material"}, "quokka")
    assert cli.main(["--root", str(tmp_path), "search", "quokka", "--fields", "--kind", "own"]) == 0
    out = capsys.readouterr().out
    assert "local-bm" in out and "url-ext" not in out
