"""Duels: two forks of one round, judged blind, ranked by Elo.

A fork asks "what does this change do". A duel asks the question people actually
have when choosing a model or a prompt: *which of these two would I rather have,
on my own tasks*. Each side is an ordinary fork (`fork.run`), so it goes through
the real agent loop with tools simulated, and its turn opens in the stepper like
any other.

* **Blind.** The two contestants are assigned to A and B at random, and the pane
  shows them unlabelled until the vote. A person who knows which answer came from
  the bigger model reads it more kindly.
* **Never live.** A duel is two replays at once; neither may act. `live` is not
  offered.
* **Ratings are replayed, never stored.** The leaderboard is Elo over every voted
  duel in vote order from a fresh start, so it is a pure function of the votes:
  deleting a duel or changing a vote cannot leave a rating behind.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Callable

from backend.modules.agentpedia import fork, store
from backend.modules.agentpedia.models import (
    Contestant,
    Duel,
    DuelRequest,
    DuelSide,
    DuelVote,
    ForkEdit,
    ForkRecord,
    ForkRequest,
    Leaderboard,
)

logger = logging.getLogger(__name__)

ELO_K = 32.0
ELO_START = 1000.0


class DuelError(Exception):
    """A request that cannot be run or found; the route turns it into a 4xx."""


def _fmt_temp(value: float) -> str:
    return f"{value:g}"


def contestant_label(model: str, edits: list[ForkEdit]) -> str:
    """A contestant's name on the leaderboard: the model it ran on, plus each edit
    that makes it a different configuration.

    Two duels whose sides carry the same label are pooled into one rating, which
    is the point — "qwen3:8b at temperature 0.7" earns its rating across every
    task it was tried on. Edits that change the task itself rather than the
    configuration (`edit_message`, `truncate_history`) are named too: a rating
    earned on a shortened history is not the same contestant's.
    """
    parts = [model or "default model"]
    for edit in edits:
        if edit.op == "set_provider" and edit.name:
            parts[0] = f"{edit.name}/{parts[0]}"
        elif edit.op == "set_temperature" and edit.value is not None:
            parts.append(f"T={_fmt_temp(edit.value)}")
        elif edit.op == "set_system":
            parts.append("custom system prompt")
        elif edit.op == "drop_tool" and edit.name:
            parts.append(f"−{edit.name}")
        elif edit.op == "drop_group" and edit.name:
            parts.append(f"−{edit.name}.*")
        elif edit.op == "edit_message" and edit.index is not None:
            parts.append(f"message {edit.index} edited")
        elif edit.op == "truncate_history" and edit.keep is not None:
            parts.append(f"last {edit.keep} messages")
    return " · ".join(parts)


def _side(record: ForkRecord) -> DuelSide:
    """What the duel shows of one fork: its answer, and what it reached for."""
    side = DuelSide(
        fork_turn_id=record.fork_turn_id,
        label=contestant_label(record.model, record.edits),
        edits=record.edits,
        status=record.status,
        error=record.error,
        model=record.model,
        provider=record.provider,
        answer=record.answer,
        calls=record.calls,
    )
    if record.status != "complete":
        return side
    try:
        turn = fork.load_turn(record.fork_turn_id)
        side.decision = fork.decision_at(turn, 0)
        side.rounds = len(turn.rounds)
        side.total_tokens = turn.rounds[-1].totalTokens if turn.rounds else 0
    except Exception:
        # The fork ran; its snapshot is what is missing. The answer still stands.
        logger.debug("agentpedia: no snapshot for duel side %s", record.fork_turn_id)
    return side


async def run(req: DuelRequest, *, rng: Callable[[], float] = random.random) -> Duel:
    """Run both contestants on the same round, at once, and record the pairing."""
    first, second = req.contestants
    if [e.model_dump() for e in first] == [e.model_dump() for e in second]:
        raise DuelError(
            "Both contestants have the same edits; a duel needs two different "
            "configurations (a different model, temperature, prompt or tools)."
        )
    # Blind: which contestant is shown as A is a coin flip.
    if rng() < 0.5:
        first, second = second, first

    def request(edits: list[ForkEdit]) -> ForkRequest:
        return ForkRequest(
            turn_id=req.turn_id,
            from_round=req.from_round,
            edits=edits,
            live=False,
            fixtures=req.fixtures,
        )

    # Validate both before spending a model turn on either: a bad edit on B
    # discovered after A has run would waste A. Stricter than a single fork, which
    # runs and reports an edit that matched nothing: a contestant whose edit
    # silently did nothing would earn a rating under a name it did not deserve.
    try:
        for side in (first, second):
            rejected = fork.preview(request(side)).rebuild.rejected
            if rejected:
                raise DuelError("An edit matched nothing: " + "; ".join(rejected))
    except fork.ForkError as exc:
        raise DuelError(str(exc)) from exc

    a_record, b_record = await asyncio.gather(
        fork.run(request(first)), fork.run(request(second))
    )
    duel = Duel(
        id=f"duel-{uuid.uuid4().hex[:10]}",
        turn_id=req.turn_id,
        from_round=req.from_round,
        created_at=time.time(),
        a=_side(a_record),
        b=_side(b_record),
    )
    store.save_duel(duel)
    return duel


def vote(duel_id: str, verdict: DuelVote) -> Duel:
    duel = store.get_duel(duel_id)
    if duel is None:
        raise DuelError(f"No duel {duel_id!r}")
    duel.vote = verdict
    duel.voted_at = time.time()
    store.save_duel(duel)
    return duel


def _expected(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def leaderboard(duels: list[Duel]) -> Leaderboard:
    """Elo over `duels` (already in vote order), from a fresh start.

    A win scores 1, a tie 0.5 each. `both_bad` is counted on each contestant's
    row but moves no rating: "neither was good" says nothing about which was
    better. A duel whose two sides carry the same label (the same configuration
    on two draws) is skipped for the same reason.
    """
    board: dict[str, Contestant] = {}

    def row(label: str) -> Contestant:
        if label not in board:
            board[label] = Contestant(label=label, rating=ELO_START)
        return board[label]

    voted = 0
    for duel in duels:
        if duel.vote is None:
            continue
        voted += 1
        a, b = row(duel.a.label), row(duel.b.label)
        if a is b:
            continue
        a.games += 1
        b.games += 1
        if duel.vote == "both_bad":
            a.both_bad += 1
            b.both_bad += 1
            continue
        score = {"a": 1.0, "b": 0.0, "tie": 0.5}[duel.vote]
        if score == 1.0:
            a.wins += 1
            b.losses += 1
        elif score == 0.0:
            b.wins += 1
            a.losses += 1
        else:
            a.ties += 1
            b.ties += 1
        expected = _expected(a.rating, b.rating)
        delta = ELO_K * (score - expected)
        a.rating += delta
        b.rating -= delta

    contestants = sorted(board.values(), key=lambda c: (-c.rating, c.label))
    for c in contestants:
        c.rating = round(c.rating, 1)
    return Leaderboard(contestants=contestants, voted=voted, k=ELO_K, start=ELO_START)
