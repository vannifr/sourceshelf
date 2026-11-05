from scripts.recall import main
from tests.test_search import make

DOCS = {
    "arxiv-0001": ("Agents en codekwaliteit", "een codeeragent werkt sneller maar de codekwaliteit daalt op lange termijn"),
    "arxiv-0002": ("Diversiteit van antwoorden", "taalmodellen geven op open vragen bijna hetzelfde antwoord"),
    "arxiv-0003": ("Scaling agents", "adding more agents yields diminishing returns unless the agents differ"),
    "dora-2024": ("DORA report", "deployment frequency lead time change failure rate recovery time"),
    "blog-kanban": ("Kanban basics", "work in progress limits make flow visible in a kanban board"),
    "blog-scrum": ("Scrum guide", "sprint planning daily scrum sprint review and retrospective"),
    "rapport-ai": ("AI adoptie rapport", "adoptie van generatieve ai in softwareteams en productiviteit"),
    "paper-test": ("Testing study", "mutation testing finds weak unit tests in large codebases"),
}
QUESTIONS = [
    ("R01", "codekwaliteit codeeragent lange termijn", "arxiv-0001"),
    ("R02", "open vragen hetzelfde antwoord taalmodellen", "arxiv-0002"),
    ("R03", "diminishing returns more agents", "arxiv-0003"),
    ("R04", "deployment frequency change failure rate", "dora-2024"),
    ("R05", "work in progress limits kanban", "blog-kanban"),
    ("R06", "sprint retrospective scrum", "blog-scrum"),
    ("R07", "productiviteit softwareteams adoptie", "rapport-ai"),
    ("R08", "mutation testing unit tests", "paper-test"),
]


def test_recall_regression_synthetic_set(tmp_path, capsys):
    for fid, (title, text) in DOCS.items():
        make(tmp_path, fid, title, "", None, text)
    tsv = tmp_path / "questions.tsv"
    tsv.write_text("id\ttype\tquestion\texpected\n" + "".join(f"{q}\tx\t{v}\t{e}\n" for q, v, e in QUESTIONS), encoding="utf-8")
    assert main(["--root", str(tmp_path), "--questions", str(tsv), "--min-top", "8"]) == 0
    assert "top1=8 top5=8" in capsys.readouterr().out


def test_recall_threshold_gives_exit_1(tmp_path, capsys):
    make(tmp_path, "arxiv-0001", "Een", "", None, "alleen dit")
    tsv = tmp_path / "q.tsv"
    tsv.write_text("id\ttype\tquestion\texpected\nR1\tx\tonvindbaar woord\tarxiv-0001\n", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--questions", str(tsv), "--min-top", "1"]) == 1
    assert "top1=0" in capsys.readouterr().out
