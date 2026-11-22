"""A change's risk from the heat map of its base (use_cases/assess_change.py), through an in-memory Git."""

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.assess_change import assess_change
from clean_eval.use_cases.interfaces import Clock, Git
from tests.use_cases.fakes import fake

P = Policy()
TODAY = "2026-11-16"
CLOCK = fake(Clock, today=lambda: TODAY)
CORE = "def g(con):\n    con.execute('INSERT INTO t VALUES (1)')\n    con.commit()\n"


def fake_git(tree, changed=()):
    """A Git whose revisions all hold `tree` ({path: text}) and differ by the `changed` paths."""
    return fake(
        Git,
        ls_tree=lambda rev: [(p, "blob:" + p, len(t)) for p, t in tree.items()],
        read_blobs=lambda ids: {i: tree[i[len("blob:") :]] for i in ids},
        rev_parse=lambda rev, short=False: "abc1234",
        repo_name=lambda: "demo",
        toplevel=lambda: "/r",
        diff_files=lambda base, head: list(changed),
    )


TREE = {
    "src/api/a.py": "a = 1\n" * 150,
    "src/api/b.py": "b = 1\n" * 150,
    "src/core/c.py": CORE,
    "tests/test_a.py": "x = 1\n",
}


def test_change_risk_from_the_base_heat_map():
    heat, risk = assess_change(fake_git(TREE, ["src/core/c.py", "README.md"]), CLOCK, "base", "head", P)
    assert [m.module for m in risk.modules] == ["src/core"]
    assert risk.max_band == risk.modules[0].band
    assert risk.ignored == ["README.md"]


def test_a_base_without_python_rates_everything_unrated():
    heat, risk = assess_change(fake_git({}, ["src/new.py"]), CLOCK, "base", "head", P)
    assert (heat.repo, heat.sha, heat.generated_at) == ("demo", "abc1234", TODAY)  # the base's snapshot
    assert (heat.modules, risk.max_band, risk.modules[0].rated) == ([], "Moderate", False)
