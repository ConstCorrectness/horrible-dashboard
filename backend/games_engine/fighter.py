"""2D fighter: a deterministic, tick-based fighting game.

Both seats act **every tick** (simultaneous): each submits one enumerated action
(no payload). When the second seat's action for a tick arrives, the world steps
one frame — pure fixed-point integer physics (positions, velocities, hitboxes,
hp, meter, stun), so the same action streams always produce the same frames
(which is what makes replays exact). The server is the only simulator.

- **Actions:** `idle`, `left`, `right`, `jump`, `crouch_block`, `light`, `heavy`,
  `special`. `idle` is listed **first** so the referee's timeout auto-play (which
  picks `legal[0]`) does the harmless thing when a seat is slow.
- **Match:** best-of-3 KO rounds inside one game state; first to 2 rounds wins.
  A round ends on a KO or when the round clock (`ROUND_TICKS`) runs out (higher hp
  wins the round).

Ranked play drives this with a fast per-tick **bot script** (no model call per
tick — see the node's `fighter.bot` loadout tool); the Plaza arcade mode drives
it from held keyboard input. Either way the engine is identical.
"""

from __future__ import annotations

from typing import Any

from backend.games_engine.base import (
    TERMINAL,
    Action,
    GameSpec,
    GameState,
    register_game,
)

# Fixed-point world (integer units). The frontend scales to pixels.
STAGE_W = 400
FLOOR = 0
START_X = (150, 250)  # ~17 ticks of walking to reach striking range
MAX_HP = 100
ROUND_TICKS = 300  # ~ a round; at a few ticks/sec this is generous
ROUNDS_TO_WIN = 2
MOVE_SPEED = 6
JUMP_V = 22
GRAVITY = 3
LIGHT_RANGE, LIGHT_DMG, LIGHT_STUN = 40, 6, 4
HEAVY_RANGE, HEAVY_DMG, HEAVY_STUN = 30, 14, 9
SPECIAL_RANGE, SPECIAL_DMG, SPECIAL_STUN, SPECIAL_COST = 90, 22, 14, 50
METER_ON_HIT = 12
BLOCK_MULT = 0.25  # chip damage while crouch-blocking

ACTIONS = (
    "idle",  # FIRST: the safe timeout auto-play
    "left",
    "right",
    "jump",
    "crouch_block",
    "light",
    "heavy",
    "special",
)


class _Fighter:
    def __init__(self, x: int, facing: int) -> None:
        self.x = x
        self.y = FLOOR
        self.vy = 0
        self.hp = MAX_HP
        self.meter = 0
        self.facing = facing  # +1 right, -1 left
        self.stun = 0
        self.anim = "idle"


class Fighter(GameState):
    def __init__(self) -> None:
        self.tick = 0
        self.round = 0
        self.round_wins = [0, 0]
        self.fighters = [_Fighter(START_X[0], 1), _Fighter(START_X[1], -1)]
        self._buffer: dict[int, str] = {}  # this tick's submitted actions
        self._done = False

    def _new_round(self) -> None:
        self.tick = 0
        self.fighters = [_Fighter(START_X[0], 1), _Fighter(START_X[1], -1)]
        self._buffer = {}

    # ---- turn structure ----------------------------------------------------

    def current_players(self) -> list[int]:
        if self._done:
            return []
        return [s for s in (0, 1) if s not in self._buffer]

    def current_player(self) -> int:
        if self._done:
            return TERMINAL
        pending = self.current_players()
        return pending[0] if pending else TERMINAL

    def legal_actions(self, player: int) -> list[Action]:
        if player not in self.current_players():
            return []
        return [Action(id=a, label=a) for a in ACTIONS]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player not in self.current_players():
            raise ValueError("already acted this tick")
        if action_id not in ACTIONS:
            raise ValueError(f"bad action {action_id!r}")
        self._buffer[player] = action_id
        # When both have acted, step the world one frame.
        if len(self._buffer) == 2:
            self._step()

    # ---- simulation --------------------------------------------------------

    def _step(self) -> None:
        actions = dict(self._buffer)
        self._buffer = {}
        self.tick += 1
        f0, f1 = self.fighters
        f0.facing = 1 if f1.x >= f0.x else -1
        f1.facing = 1 if f0.x >= f1.x else -1

        # Movement + intent first (stunned fighters can't act).
        for seat, fighter in enumerate(self.fighters):
            act = actions.get(seat, "idle")
            fighter.anim = act
            if fighter.stun > 0:
                fighter.stun -= 1
                fighter.anim = "stun"
                act = "idle"
            if act == "left":
                fighter.x = max(0, fighter.x - MOVE_SPEED)
            elif act == "right":
                fighter.x = min(STAGE_W, fighter.x + MOVE_SPEED)
            elif act == "jump" and fighter.y == FLOOR:
                fighter.vy = JUMP_V
            # gravity
            if fighter.y > FLOOR or fighter.vy != 0:
                fighter.y = max(FLOOR, fighter.y + fighter.vy)
                fighter.vy = 0 if fighter.y == FLOOR else fighter.vy - GRAVITY

        # Attacks resolve after movement, using post-move positions.
        for seat, fighter in enumerate(self.fighters):
            act = actions.get(seat, "idle")
            if fighter.stun > 0:
                continue
            target = self.fighters[1 - seat]
            dist = abs(fighter.x - target.x)
            blocking = actions.get(1 - seat) == "crouch_block" and target.y == FLOOR
            hit = None
            if act == "light" and dist <= LIGHT_RANGE:
                hit = (LIGHT_DMG, LIGHT_STUN)
            elif act == "heavy" and dist <= HEAVY_RANGE:
                hit = (HEAVY_DMG, HEAVY_STUN)
            elif (
                act == "special"
                and fighter.meter >= SPECIAL_COST
                and dist <= SPECIAL_RANGE
            ):
                fighter.meter -= SPECIAL_COST
                hit = (SPECIAL_DMG, SPECIAL_STUN)
            if hit is not None:
                dmg, stun = hit
                if blocking:
                    dmg = int(dmg * BLOCK_MULT)
                    stun = 0
                target.hp = max(0, target.hp - dmg)
                target.stun = max(target.stun, stun)
                fighter.meter = min(100, fighter.meter + METER_ON_HIT)

        # Round end?
        ko = any(f.hp <= 0 for f in self.fighters)
        timeout = self.tick >= ROUND_TICKS
        if ko or timeout:
            self._end_round()

    def _end_round(self) -> None:
        h0, h1 = self.fighters[0].hp, self.fighters[1].hp
        if h0 > h1:
            self.round_wins[0] += 1
        elif h1 > h0:
            self.round_wins[1] += 1
        # (a double-KO / equal-hp timeout awards nobody the round)
        self.round += 1
        if max(self.round_wins) >= ROUNDS_TO_WIN or self.round >= 3:
            self._done = True
        else:
            self._new_round()

    # ---- views -------------------------------------------------------------

    def _frame(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "round": self.round + 1,
            "round_wins": list(self.round_wins),
            "timer": max(0, ROUND_TICKS - self.tick),
            "p": [
                {
                    "x": f.x,
                    "y": f.y,
                    "hp": f.hp,
                    "meter": f.meter,
                    "facing": f.facing,
                    "anim": f.anim,
                    "stun": f.stun,
                }
                for f in self.fighters
            ],
        }

    def observation(self, player: int) -> dict[str, Any]:
        # Perfect information: both fighters' full state (it's a fighting game).
        obs = self._frame()
        obs.update(game="fighter", seat=player, actions=list(ACTIONS))
        return obs

    def public_state(self) -> dict[str, Any]:
        state = self._frame()
        state.update(
            game="fighter",
            stage_w=STAGE_W,
            max_hp=MAX_HP,
            turn=None,
            winner=self._winner() if self._done else None,
        )
        return state

    # ---- outcome -----------------------------------------------------------

    def _winner(self) -> int | None:
        if self.round_wins[0] == self.round_wins[1]:
            return None
        return 0 if self.round_wins[0] > self.round_wins[1] else 1

    def returns(self) -> dict[int, float]:
        w = self._winner()
        if w is None:
            return {0: 0.0, 1: 0.0}
        return {w: 1.0, 1 - w: -1.0}


SPEC = register_game(
    GameSpec(
        id="fighter",
        name="Fighter",
        min_players=2,
        max_players=2,
        factory=Fighter,
        move_timeout_s=1.0,  # a tick clock; timeout auto-plays `idle` (legal[0])
    )
)
