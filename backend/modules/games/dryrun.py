"""Dry-run a whole harness outside a match — the editor's full-loop tester.

`test-tool` exercises ONE tool body in isolation; this module exercises the
loadout the way a real turn does: a sample observation from the actual engine,
the player's context in the system prompt, EVERY compiled tool advertised, and
the real model driving the `AgentPolicy` loop — but with no random fallback and
no exception swallowing, so failures are visible instead of papered over.
See docs/modules/games.mdx (testing your harness).
"""

from __future__ import annotations

import random
import time
from typing import Any

from backend.games_engine.base import CHANCE, WORK, get_game
from backend.modules.games.loadout import HarnessRuntime, Loadout, tool_name_error
from backend.modules.games.models import DryRunResponse, DryRunStep
from backend.modules.games.policy import AgentPolicy, ChatFn

# Chance/work resolutions allowed while finding the first acting seat (holdem's
# deal is 1; nothing today needs more than a handful).
_MAX_SETUP_STEPS = 64


def sample_observation(
    game_id: str, seed: int = 0
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A realistic opening position: instantiate the engine, resolve chance/work
    until a seat can act, and return that seat's (observation, legal actions in
    wire shape). Raises KeyError for an unknown game."""
    state = get_game(game_id).new()
    rng = random.Random(seed)
    for _ in range(_MAX_SETUP_STEPS):
        player = state.current_player()
        if player == CHANCE:
            state.resolve_chance(rng)
        elif player == WORK:
            state.run_work()
        else:
            break
    seats = state.current_players()
    seat = seats[0] if seats else 0
    return state.observation(seat), [a.to_wire() for a in state.legal_actions(seat)]


def _tool_problems(loadout: Loadout) -> dict[str, str]:
    """Name violations + compile errors, tool name → message."""
    problems: dict[str, str] = {}
    taken: set[str] = set()
    for tool in loadout.tools:
        err = tool_name_error(tool.name, taken)
        if err is not None:
            problems[tool.name or "(unnamed)"] = err
        taken.add(tool.name)
    problems.update(HarnessRuntime(loadout).compile_errors())
    return problems


async def run_dry(
    loadout: Loadout,
    game_id: str,
    seed: int = 0,
    chat_fn: ChatFn | None = None,
) -> DryRunResponse:
    """Drive the full agent loop once against a sample position. `chat_fn` is
    injectable for tests; without one the loadout's model (or the agent module's
    default) is used, exactly as in a live match."""
    observation, legal_actions = sample_observation(game_id, seed)
    compile_errors = _tool_problems(loadout)

    if chat_fn is None:
        from backend.modules.agent.routes import _load_config
        from backend.modules.games import model_config

        if model_config.parse_model(loadout.model) is None and _load_config() is None:
            return DryRunResponse(
                ok=False,
                error=(
                    "no model: the loadout has no model of its own and the agent "
                    "module isn't configured — set one in the Model section"
                ),
                observation=observation,
                legal_actions=legal_actions,
                compile_errors=compile_errors,
            )

    t0 = time.monotonic()
    steps: list[DryRunStep] = []

    def sink(step: dict[str, Any]) -> None:
        steps.append(DryRunStep(t_ms=(time.monotonic() - t0) * 1000.0, **step))

    policy = AgentPolicy(chat_fn=chat_fn, load_loadout=lambda _g: loadout, trace=sink)
    chosen: str | None = None
    error: str | None = None
    try:
        chosen = await policy.run_once(observation, legal_actions, game_id)
    except Exception as exc:  # surfaced to the author, unlike a live match
        error = f"{type(exc).__name__}: {exc}"

    return DryRunResponse(
        ok=error is None,
        error=error,
        observation=observation,
        legal_actions=legal_actions,
        compile_errors=compile_errors,
        steps=steps,
        chosen=chosen,
        rounds_used=sum(1 for s in steps if s.kind == "assistant"),
        total_ms=(time.monotonic() - t0) * 1000.0,
    )
