"""The trainer agent's workflow has to fit in its tool budget.

This was arithmetically impossible and nothing said so. The `training` group held
**34** tools — 14 project verbs, 10 recipe/sweep verbs, and 10 notebook cell verbs
registered by the *frontend*, which is why every count written in a comment said 14
and was wrong. Core is ~9. The agent's own system prompt prescribes
`training.list_checkpoints -> training.convert -> llamacpp.serve -> evals.run` and
tells it to compare with localtrack; that is far past a budget of 38. The overflow
was truncated, and the only trace was one ERROR line in a log nobody reads while
chatting.

The remedy was a split (`training` -> `training` + `recipe` + `wandb` + `cells`)
plus a budget resolved per agent. These assert the sizes rather than trusting a
number in a comment, because group sizes drift every time somebody adds a tool —
which is exactly how it reached 34.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from backend.modules.agent import orchestrator, roster

#: The groups this agent's loop is made of.
LOOP_GROUPS = (
    "training",
    "recipe",
    "cells",
    "evals",
    "localtrack",
    "datasets",
    "llamacpp",
)

#: `_core_tools` for a scoped, non-delegating agent: 6 layout reads + 2 meta tools +
#: `use_skill`. Written out rather than computed because the point of the test is the
#: headroom left over, and a core that grows should fail this, not absorb itself.
CORE = 9


def _frontend_tool_names() -> list[str]:
    """Tool names the BROWSER registers, scanned from source.

    These count against a group exactly like a backend tool — `_group_of` keys on the
    name prefix and does not care which side declared it — and they are the ones
    everybody forgets. Ten notebook cell verbs sat in the `training` group for its
    whole life while every count in the comments said 14.

    Source-scanned because the backend cannot ask: a frontend tool exists only in a
    live browser's catalog, so there is nothing to import.
    """
    repo = pathlib.Path(__file__).resolve().parents[2]
    root = repo / "packages" / "core" / "src" / "modules"
    pattern = re.compile(r"name: '([a-z][a-z0-9]*\.[a-zA-Z_]+)'")
    names: list[str] = []
    for path in root.rglob("*.ts"):
        if "__tests__" in str(path):
            continue
        names += pattern.findall(path.read_text(encoding="utf-8"))
    return names


def _group_sizes() -> dict[str, int]:
    """Tools per group, keyed the way the orchestrator keys them: by name prefix."""
    from backend.modules.datasets import agent_tools as datasets_tools
    from backend.modules.evals import agent_tools as evals_tools
    from backend.modules.llamacpp import agent_tools as llamacpp_tools
    from backend.modules.localtrack import agent_tools as localtrack_tools
    from backend.modules.training import agent_tools as training_tools
    from backend.modules.training import recipe_tools

    sizes: dict[str, int] = dict.fromkeys(LOOP_GROUPS, 0)
    sizes["wandb"] = 0
    for tools in (
        training_tools._TOOLS,
        recipe_tools.TOOLS,
        evals_tools._TOOLS,
        localtrack_tools._TOOLS,
        datasets_tools.TOOLS,
        llamacpp_tools.LLAMACPP_TOOLS,
    ):
        for tool in tools:
            group = orchestrator._group_of(tool.name)
            sizes[group] = sizes.get(group, 0) + 1
    for name in _frontend_tool_names():
        group = orchestrator._group_of(name)
        if group in sizes:
            sizes[group] += 1
    return sizes


def test_the_frontend_scan_actually_finds_something() -> None:
    # A guard on the guard. If the regex stops matching, `_group_sizes` silently
    # under-counts every group by whatever the browser registers — which is the exact
    # blindness that let `training` reach 34 while the comments said 14, and it would
    # make every assertion below pass for the wrong reason.
    names = _frontend_tool_names()
    assert any(n.startswith("cells.") for n in names), names[:20]


def test_the_training_group_was_split() -> None:
    sizes = _group_sizes()
    # Authoring a recipe is a coherent job of its own, editing notebook cells is
    # another, and importing someone else's W&B runs is not part of any local loop.
    assert sizes["training"] <= 16, sizes
    assert sizes["recipe"] >= 5, sizes
    assert sizes["wandb"] >= 2, sizes
    assert sizes["cells"] >= 8, sizes


def test_the_trainer_can_hold_each_phase_on_a_small_local_model() -> None:
    """At the 38 floor, each phase of the loop fits over the core.

    Not all seven groups at once — that is 60+ tools and no small model should be
    handed it. What must hold is that each *phase* fits: pick data, write the recipe,
    run the job, edit the notebook, convert and serve, judge the result. Those are
    sequential in the prompt and sequential in real use, so one `load_tools` between
    them is the intended cost rather than a failure.
    """
    sizes = _group_sizes()
    phases = {
        "pick data": sizes["datasets"] + sizes["recipe"],
        "run a job": sizes["training"] + sizes["recipe"],
        "edit the notebook": sizes["training"] + sizes["cells"],
        "convert and serve": sizes["training"] + sizes["llamacpp"],
        "judge it": sizes["evals"] + sizes["localtrack"],
    }
    over = {
        name: CORE + total
        for name, total in phases.items()
        if CORE + total > orchestrator.TOOL_BUDGET
    }
    assert over == {}, f"phases over the {orchestrator.TOOL_BUDGET}-tool floor: {over}"


def test_a_hosted_model_can_hold_the_whole_loop_at_once() -> None:
    """The point of the raised ceiling: no `load_tools` thrash on a capable model."""
    sizes = _group_sizes()
    everything = CORE + sum(sizes[g] for g in LOOP_GROUPS)
    assert everything <= orchestrator.TOOL_BUDGET_HOSTED, everything


def test_the_trainer_is_permitted_every_group_its_prompt_names() -> None:
    spec = roster.get_agent("trainer")
    assert spec is not None
    groups = set(spec.tool_groups or [])
    # `datasets` was missing entirely: the agent that fills in a recipe's
    # `dataset_id` could not look at a dataset, and `can_delegate` is False, so it
    # had no way out.
    for needed in LOOP_GROUPS:
        assert needed in groups, f"trainer cannot load {needed}"
    assert spec.preload_groups == ["training"]


def test_the_budget_falls_back_to_the_floor_when_it_cannot_tell() -> None:
    # Being wrong low costs a `load_tools` round; being wrong high costs a model that
    # silently stops reasoning, which is indistinguishable from one that chose not to
    # call anything. The fallback must be the floor.
    assert orchestrator.tool_budget_for("no-such-agent-id") == orchestrator.TOOL_BUDGET


@pytest.mark.parametrize(
    "hosted, expected_attr",
    [(True, "TOOL_BUDGET_HOSTED"), (False, "TOOL_BUDGET")],
)
def test_the_budget_follows_the_provider(monkeypatch, hosted, expected_attr) -> None:
    class _Info:
        pass

    info = _Info()
    info.hosted = hosted
    monkeypatch.setattr(roster, "resolve_provider", lambda *a, **k: (info, ""))
    assert orchestrator.tool_budget_for("trainer") == getattr(
        orchestrator, expected_attr
    )
