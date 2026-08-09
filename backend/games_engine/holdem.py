"""Heads-up No-Limit Texas Hold'em — the engine's first imperfect-information game.

This is the game the OpenSpiel-shaped contract was designed for: **chance nodes**
(the server shuffles and deals with its own RNG), **hidden state** (`observation`
carries only *your* hole cards; `public_state` carries none until showdown), and
**betting**. One table = one hand: blinds are posted, streets are dealt, and
`returns()` is each seat's chip delta — so the ladder's win/draw/loss mapping works
unchanged.

Betting is discretized so the agent still only *chooses among enumerated legal
moves* (the engine contract): `fold` / `check` / `call` plus three sized raises —
`raise_min`, `raise_pot`, `all_in` — each carrying its exact chip amount in
`params`. Seat 0 is the button/small blind (acts first preflop, last postflop);
seat 1 is the big blind. Stacks start at 100 chips, blinds 1/2.

Hand evaluation is a self-contained best-5-of-7 ranker (21 combinations per hand)
rather than a pokerkit dependency — the discrete action set meant we needed our own
betting state machine anyway, and this keeps the engine pure-Python and fully
unit-tested.
"""

from __future__ import annotations

import random
from collections import Counter
from itertools import combinations
from typing import Any

from backend.games_engine.base import (
    CHANCE,
    TERMINAL,
    Action,
    GameSpec,
    GameState,
    register_game,
)

RANKS = "23456789TJQKA"
SUITS = "shdc"  # spades, hearts, diamonds, clubs

STACK = 100
SB = 1
BB = 2

_RANK_VAL = {r: i for i, r in enumerate(RANKS, start=2)}
_STREETS = ("preflop", "flop", "turn", "river")
_NEXT_STREET = {"preflop": "flop", "flop": "turn", "turn": "river"}
_DEAL_COUNT = {"flop": 3, "turn": 1, "river": 1}

HAND_NAMES = (
    "high card",
    "pair",
    "two pair",
    "three of a kind",
    "straight",
    "flush",
    "full house",
    "four of a kind",
    "straight flush",
)


# ---- hand evaluation ---------------------------------------------------------


def _rank5(cards: tuple[str, ...]) -> tuple[int, ...]:
    """Rank a 5-card hand as a comparable tuple: (category, tiebreakers...).

    Tuples are only length-compared within a category (the first element), so
    mixed-length tuples across categories are safe to `max()` over.
    """
    vals = sorted((_RANK_VAL[c[0]] for c in cards), reverse=True)
    is_flush = len({c[1] for c in cards}) == 1
    counts = Counter(vals)
    # Groups sorted by (count, rank) desc: e.g. full house -> [(trip, 3), (pair, 2)].
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    # Kickers ordered by group size first, then rank (two pair: pair, pair, kicker).
    kick = tuple(v for v, n in groups for _ in range(n))

    uniq = sorted(set(vals), reverse=True)
    straight_high = None
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_high = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:  # the wheel: A-2-3-4-5
            straight_high = 5

    if straight_high is not None and is_flush:
        return (8, straight_high)
    if groups[0][1] == 4:
        return (7, *kick)
    if groups[0][1] == 3 and groups[1][1] == 2:
        return (6, *kick)
    if is_flush:
        return (5, *vals)
    if straight_high is not None:
        return (4, straight_high)
    if groups[0][1] == 3:
        return (3, *kick)
    if groups[0][1] == 2 and groups[1][1] == 2:
        return (2, *kick)
    if groups[0][1] == 2:
        return (1, *kick)
    return (0, *vals)


def best_rank(cards: list[str]) -> tuple[int, ...]:
    """The best 5-card rank from 5-7 cards (hole + board)."""
    return max(_rank5(combo) for combo in combinations(cards, 5))


# ---- game state ---------------------------------------------------------------


class Holdem(GameState):
    def __init__(self) -> None:
        # Blinds are posted up front; the first chance node deals the hole cards.
        self.stacks: list[int] = [STACK - SB, STACK - BB]
        self.committed: list[int] = [SB, BB]  # whole-hand totals per seat
        self.bets: list[int] = [SB, BB]  # this street's totals per seat
        self.last_raise: int = BB  # size of the last raise (min-raise rule)
        self.street: str = "preflop"
        self.pending_deal: str | None = "hole"
        self.to_act: int | None = None
        self.acted: list[bool] = [False, False]  # blinds don't count as acting
        self.deck: list[str] = []
        self.hole: list[list[str]] | None = None
        self.board: list[str] = []
        self.folded: int | None = None
        self.winner: int | None = None  # None = tie/undecided
        self.done: bool = False
        self.all_in_runout: bool = False  # betting closed; deal out the board

    # ---- turn structure ----------------------------------------------------

    def current_player(self) -> int:
        if self.done:
            return TERMINAL
        if self.pending_deal is not None:
            return CHANCE
        assert self.to_act is not None
        return self.to_act

    def resolve_chance(self, rng: random.Random) -> None:
        if self.pending_deal == "hole":
            self.deck = [r + s for r in RANKS for s in SUITS]
            rng.shuffle(self.deck)
            self.hole = [
                [self.deck.pop(), self.deck.pop()],
                [self.deck.pop(), self.deck.pop()],
            ]
            self.pending_deal = None
            self.to_act = 0  # heads-up: the button acts first preflop
            return
        deal = self.pending_deal
        assert deal in _DEAL_COUNT, f"no chance event pending ({deal!r})"
        self.board.extend(self.deck.pop() for _ in range(_DEAL_COUNT[deal]))
        self.street = deal
        self.pending_deal = None
        if self.all_in_runout or min(self.stacks) == 0:
            # Nobody can bet — chain straight to the next deal (or showdown).
            self._end_street()
            return
        # Fresh betting round: the big blind acts first postflop (heads-up rule).
        self.bets = [0, 0]
        self.acted = [False, False]
        self.last_raise = BB  # min bet resets to one big blind
        self.to_act = 1

    def legal_actions(self, player: int) -> list[Action]:
        if player != self.current_player():
            return []
        opp = 1 - player
        facing = self.bets[opp] - self.bets[player]
        actions: list[Action] = []
        if facing > 0:
            actions.append(Action(id="fold", label="fold"))
            call_amount = min(facing, self.stacks[player])
            actions.append(
                Action(
                    id="call",
                    label=f"call {call_amount}",
                    params={"amount": call_amount},
                )
            )
        else:
            actions.append(Action(id="check", label="check"))
        for aid, to in self._raise_targets(player):
            verb = "bet" if facing == 0 else "raise to"
            label = f"all-in ({to})" if aid == "all_in" else f"{verb} {to}"
            actions.append(Action(id=aid, label=label, params={"to": to}))
        return actions

    def _raise_targets(self, player: int) -> list[tuple[str, int]]:
        """The discrete raise sizes as (action id, raise-to this-street total).
        Empty when the player can't put in more than a call (all-in call only)."""
        opp = 1 - player
        facing = self.bets[opp] - self.bets[player]
        all_in_to = self.bets[player] + self.stacks[player]
        if all_in_to <= self.bets[opp]:
            return []  # covering even the call takes the whole stack
        targets: list[tuple[str, int]] = []
        pot_now = self.committed[0] + self.committed[1]
        min_to = self.bets[opp] + max(self.last_raise, BB)
        pot_to = self.bets[opp] + pot_now + facing  # a pot-size raise after calling
        for aid, to in (("raise_min", min_to), ("raise_pot", pot_to)):
            if to < all_in_to and all(t[1] != to for t in targets):
                targets.append((aid, to))
        targets.append(("all_in", all_in_to))
        return targets

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        legal = {a.id: a for a in self.legal_actions(player)}
        action = legal.get(action_id)
        if action is None:
            raise ValueError(f"illegal action {action_id!r}")
        opp = 1 - player
        self.acted[player] = True

        if action_id == "fold":
            self.folded = player
            self.winner = opp
            self.done = True
            return

        if action_id == "check":
            if self.acted[opp] and self.bets[0] == self.bets[1]:
                self._end_street()
            else:
                self.to_act = opp
            return

        if action_id == "call":
            amount = int(action.params["amount"])
            self._put_chips(player, amount)
            if self.bets[player] < self.bets[opp]:
                # Capped call — the caller is all-in for less. Betting is over for
                # the hand; the excess is refunded at resolution.
                self.all_in_runout = True
                self._end_street()
            elif self.acted[opp]:
                self._end_street()
            else:
                self.to_act = opp  # preflop limp: the big blind still gets an option
            return

        # A sized raise/bet: put in chips up to the recomputed target.
        to = int(action.params["to"])
        self.last_raise = (
            to - self.bets[opp] if to > self.bets[opp] else self.last_raise
        )
        self._put_chips(player, to - self.bets[player])
        self.to_act = opp

    def _put_chips(self, player: int, amount: int) -> None:
        amount = min(amount, self.stacks[player])
        self.stacks[player] -= amount
        self.bets[player] += amount
        self.committed[player] += amount

    def _end_street(self) -> None:
        """Close the current betting round: showdown after the river, else queue
        the next deal (a chance node)."""
        self.bets = [0, 0]
        self.to_act = None
        if self.street == "river":
            self._showdown()
        else:
            self.pending_deal = _NEXT_STREET[self.street]

    def _showdown(self) -> None:
        assert self.hole is not None
        self.done = True
        ranks = [best_rank(self.hole[p] + self.board) for p in (0, 1)]
        if ranks[0] != ranks[1]:
            self.winner = 0 if ranks[0] > ranks[1] else 1
        # equal ranks -> winner stays None (split pot)

    # ---- views -------------------------------------------------------------

    def observation(self, player: int) -> dict[str, Any]:
        obs = self.public_state()
        assert self.hole is not None, "observation before the deal"
        opp = 1 - player
        obs["seat"] = player
        obs["hole"] = list(self.hole[player])
        obs["to_call"] = max(self.bets[opp] - self.bets[player], 0)
        return obs

    def public_state(self) -> dict[str, Any]:
        showdown = self.done and self.folded is None and self.hole is not None
        return {
            "game": "holdem",
            "board": list(self.board),
            "pot": self.committed[0] + self.committed[1],
            "stacks": list(self.stacks),
            "bets": list(self.bets),
            "committed": list(self.committed),
            "street": self.street,
            "turn": self.to_act
            if not self.done and self.pending_deal is None
            else None,
            "winner": self.winner if self.done else None,
            "folded": self.folded,
            # Hole cards stay hidden unless the hand reaches showdown.
            "revealed": [list(h) for h in self.hole] if showdown else [None, None],
            "hand_names": (
                [HAND_NAMES[best_rank(self.hole[p] + self.board)[0]] for p in (0, 1)]
                if showdown
                else [None, None]
            ),
        }

    # ---- outcome -----------------------------------------------------------

    def returns(self) -> dict[int, float]:
        if self.folded is not None:
            # The folder forfeits what they committed; the winner nets exactly that.
            loser = self.folded
            return {
                1 - loser: float(self.committed[loser]),
                loser: -float(self.committed[loser]),
            }
        # Showdown: the effective pot is twice the smaller commitment (an all-in
        # for less refunds the excess), so the winner nets the smaller commitment.
        eff = min(self.committed)
        if self.winner is None:
            return {0: 0.0, 1: 0.0}  # split pot
        return {self.winner: float(eff), 1 - self.winner: -float(eff)}


SPEC = register_game(
    GameSpec(
        id="holdem",
        name="Texas Hold'em",
        min_players=2,
        max_players=2,
        factory=Holdem,
        # Turn-based coded-agent game on the escape hatch — see tictactoe. Betting
        # under imperfect information reads well as a reasoning demo, but the seat
        # is still an obs → action mapping and a scripted bot plays it perfectly well.
        decision_class="policy",
        declared_policies=("agent", "bot", "random", "manual"),
        default_policy="agent",
    )
)
