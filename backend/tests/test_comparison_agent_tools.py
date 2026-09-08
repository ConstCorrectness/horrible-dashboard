"""The comparison layer, reachable from a chat turn.

"Did this change help?" is the read the whole research loop exists to produce, and
all three functions that answer it properly — `localtrack.compare_runs`,
`evals.leaderboard`, `evals.leaderboard/diff` — were HTTP-only. An agent asked the
question had to pull raw metric series and re-derive the comparison itself.

`localtrack.compare_runs` was worse than absent: `training/recipe_tools.py` tells
the model, twice and by name, to call it after a sweep. Following the instruction
produced an unknown-tool error.

These assert the tool **names resolve**, because a name that did not exist is
precisely the bug. `_group_of` derives a tool's group from its name prefix, so the
name is also what decides whether `load_tools('localtrack')` brings it in at all.
"""

from __future__ import annotations

import pathlib

import pytest

from backend.modules.evals import agent_tools as evals_tools
from backend.modules.localtrack import agent_tools as localtrack_tools


def _names(module) -> set[str]:
    return {tool.name for tool in module._TOOLS}


def test_localtrack_registers_the_tool_its_own_instructions_name() -> None:
    assert "localtrack.compare_runs" in _names(localtrack_tools)


def test_evals_registers_the_leaderboard_reads() -> None:
    names = _names(evals_tools)
    assert "evals.leaderboard" in names
    assert "evals.leaderboard_diff" in names


def test_every_instruction_string_names_a_tool_that_exists() -> None:
    """The guard on the guard.

    Prompts, tool descriptions and the `note` fields handlers return all tell the
    model which tool to reach for next — and a name in one of those is checked by
    nothing. It becomes an unknown-tool error at the exact moment the model does
    what it was told, which is how `localtrack.compare_runs` came to be named twice
    by instructions and implemented nowhere.

    Source-scanned rather than read off the tool objects: two of the three mentions
    live inside handler return values (`result["note"] = "... then
    localtrack.compare_runs ..."`), which no amount of introspecting `_TOOLS` will
    reach.
    """
    import re

    from backend.modules.training import agent_tools as training_tools
    from backend.modules.training import recipe_tools

    known = _names(evals_tools) | _names(localtrack_tools)
    known |= {t.name for t in training_tools._TOOLS}
    known |= {t.name for t in recipe_tools.TOOLS}

    repo = pathlib.Path(__file__).resolve().parents[2]
    scanned = [
        repo / "backend/modules/training/recipe_tools.py",
        repo / "backend/modules/training/agent_tools.py",
        repo / "backend/modules/agent/roster.py",
    ]

    mentioned: set[str] = set()
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        mentioned |= set(re.findall(r"(?:localtrack|evals)\.[a-zA-Z_]+", text))

    # A guard on this guard: if the regex stops matching, the assertion below is
    # vacuous and the whole test is decoration.
    assert "localtrack.compare_runs" in mentioned

    missing = {name for name in mentioned if name not in known}
    assert missing == set(), f"instructions name tools that do not exist: {sorted(missing)}"


@pytest.mark.parametrize(
    "module, group",
    [(evals_tools, "evals"), (localtrack_tools, "localtrack")],
)
def test_every_tool_sits_in_its_name_s_group(module, group) -> None:
    # The group IS the name prefix (`_group_of`); `AgentTool.group` does not name
    # it. A tool whose declared group disagrees with its prefix is granted by a
    # `load_tools` call for a group it does not actually answer to.
    for tool in module._TOOLS:
        assert tool.name.startswith(f"{group}."), tool.name
        assert tool.group == group, tool.name
