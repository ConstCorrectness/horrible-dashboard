"""Which localtrack project an eval sweep reports into.

The mirroring is the only thing that makes "did this fine-tune help?" answerable
after the fact, and it had a hole: `run_sweep` took `localtrack_project` from its
caller and there were two callers. The browser derived one; `evals.run` — the agent
tool, and the one an agent uses to answer exactly that question — passed nothing, so
`_LocalTrack("")` built a `RunMirror("")`, which is inactive by construction and
swallows every metric without error. The sweep ran, the scoreboard filled in, and
nothing was charted anywhere.

The derivation lives in `sweep` now so both callers get it. These pin the two
properties that make it worth having: it is keyed on the **id** (a rename must not
strand a suite's history in a second project), and an explicit value still wins.
"""

from __future__ import annotations

from backend.modules.evals import sweep


def test_derives_a_project_per_suite() -> None:
    assert sweep.localtrack_project_for("tool-calling") == "evals-tool-calling"
    assert sweep.localtrack_project_for("MMLU_Stem") == "evals-mmlu-stem"


def test_slugs_are_stable_and_collapse_punctuation() -> None:
    # localtrack uses the string as both the project id and its display name, so it
    # has to survive being one.
    assert sweep.localtrack_project_for("a b/c..d") == "evals-a-b-c-d"
    assert sweep.localtrack_project_for("--edges--") == "evals-edges"


def test_falls_back_rather_than_producing_a_bare_prefix() -> None:
    # "evals-" is a project name that reads like a truncation bug. There is exactly
    # one case with nothing to derive from, and it gets the plain bucket.
    assert sweep.localtrack_project_for("") == "evals"
    assert sweep.localtrack_project_for("///") == "evals"
