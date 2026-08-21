"""Games replays → trajectories.

The games module already records the richest step data in the app: a replay is a
list of `public_state` / `action` / `trace` / `game_over` events, which is an
obs → action → reward record in everything but name. `episode.ts`'s own docstring
calls a match a trajectory.

The mapping is nearly direct, with two things worth stating.

**One run per seat, not one per replay.** A match is two or more agents each making
their own decisions from their own observations; folding them into one run would
interleave two policies' actions into a single trajectory and make every
per-harness aggregate meaningless. So a two-seat replay imports as two runs sharing
an `external_id` prefix.

**The reward is terminal and belongs on the last step.** `returns` is a per-seat
final score; the games frontend already mirrors it onto each seat's last step
(`applyReturns`), and the same has to happen here or a run's outcome is unknowable
from its steps.

Pure: `replay_to_writes(replay, dataset_id)` takes the dict the games server
returns and touches nothing else, so it is testable against a fixture.
"""

from __future__ import annotations

from typing import Any

from backend.modules.trajectories.models import HarnessWrite, StepWrite, TrajectoryWrite


def _seat_label(replay: dict[str, Any], seat: int) -> str:
    seats = replay.get("seats") or []
    if isinstance(seats, list) and 0 <= seat < len(seats):
        return str(seats[seat])
    return f"seat{seat}"


def _outcome_for(
    seat: int, winner: Any, returns: dict[str, Any] | None
) -> tuple[str | None, float | None]:
    """A seat's verdict.

    `winner` is authoritative when present. Otherwise fall back to the sign of the
    seat's return — and when there is neither, return `None` rather than guessing:
    an unlabelled run is honest, a wrongly-labelled one is training data.
    """
    reward = None
    if isinstance(returns, dict):
        raw = returns.get(str(seat), returns.get(seat))
        if isinstance(raw, (int, float)):
            reward = float(raw)
    if isinstance(winner, int):
        return ("success" if winner == seat else "failure"), reward
    if reward is not None:
        if reward > 0:
            return "success", reward
        if reward < 0:
            return "failure", reward
        return "partial", reward
    return None, reward


def replay_to_writes(replay: dict[str, Any], dataset_id: str) -> list[TrajectoryWrite]:
    """One replay → one run per seat that acted."""
    events = replay.get("events") or []
    if not isinstance(events, list):
        return []

    replay_id = str(replay.get("id") or "")
    game_id = str(replay.get("game_id") or "")
    winner = replay.get("winner")
    returns = replay.get("returns") if isinstance(replay.get("returns"), dict) else {}

    # The most recent public state, so an action can carry the observation it was
    # actually taken from rather than whatever state the replay ends on.
    latest_state: Any = None
    per_seat: dict[int, list[StepWrite]] = {}

    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        if kind == "public_state":
            latest_state = event.get("state")
            continue
        if kind == "trace":
            seat = event.get("seat")
            if isinstance(seat, int):
                for entry in event.get("steps") or []:
                    per_seat.setdefault(seat, []).append(
                        StepWrite(kind="thought", content=str(entry)[:2000])
                    )
            continue
        if kind == "action":
            seat = event.get("seat")
            if not isinstance(seat, int):
                continue
            steps = per_seat.setdefault(seat, [])
            if latest_state is not None:
                steps.append(StepWrite(kind="observation", result=latest_state))
            steps.append(
                StepWrite(
                    kind="action",
                    name=str(event.get("action_id") or "move"),
                    args={"action_id": event.get("action_id")},
                    # A timeout is a real failure mode of an agent under a time
                    # budget, and flattening it to a normal move would hide the
                    # single most useful thing a replay can tell you.
                    ok=not bool(event.get("timeout")),
                    error="timeout" if event.get("timeout") else None,
                )
            )

    writes: list[TrajectoryWrite] = []
    for seat, steps in sorted(per_seat.items()):
        outcome, reward = _outcome_for(seat, winner, returns)
        if reward is not None:
            steps.append(StepWrite(kind="reward", result=reward))
        label = _seat_label(replay, seat)
        writes.append(
            TrajectoryWrite(
                dataset_id=dataset_id,
                source="games",
                # Seat-scoped, so re-importing a replay updates both runs rather
                # than filing a second copy of either.
                external_id=f"replay:{replay_id}:{seat}",
                goal=f"{game_id or 'game'} as {label}",
                agent_id=label,
                status="complete",
                outcome=outcome,
                reward=reward,
                step_list=steps,
                harness=HarnessWrite(agent_id=label, label=f"{game_id} · {label}"),
                meta={
                    "replay_id": replay_id,
                    "game_id": game_id,
                    "seat": seat,
                    "winner": winner,
                    "returns": returns,
                },
            )
        )
    return writes
