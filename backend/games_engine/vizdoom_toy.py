"""ViZDoom: a real native-Doom duel, server-rendered.

Two seats each drive their **own** headless `vizdoom.DoomGame` instance running the
same bundled `defend_the_center` scenario (the marine is fixed in the center and
must gun down the imps closing in from every side). It's a simultaneous, tick-based
*score race*: each referee step advances both instances by a few Doom tics, and the
seat with the higher score (kills) when the clock runs out — or the last one still
alive — wins. The two marines never share a map, so this is a race, not a face-to-face
deathmatch; real PvP netcode is a later milestone.

Every tick the engine renders each instance's screen buffer to a small JPEG and ships
it as a base64 data URI in the observation / public state, so the browser board just
draws two `<img>` viewports — no client-side raycasting. Contrast with the old
pure-Python raycaster kept below as `VizDoomToyLegacy` (unregistered, for reference).

Requires the `vizdoom` and `pillow` packages; `vizdoom` is imported lazily inside the
engine so merely importing this module (to register the spec) never hard-fails on a
node without the native wheel — only *starting a table* does.
"""

from __future__ import annotations

import base64
import math
import os
from io import BytesIO
from typing import Any

from backend.games_engine.base import (
    TERMINAL,
    Action,
    GameSpec,
    GameState,
    register_game,
)

# ---- config ----------------------------------------------------------------

SCENARIO = "defend_the_center"  # bundled in the vizdoom wheel (vizdoom.scenarios_path)
# Small frames keep the WS payload tiny (~4 KB/JPEG); the board upscales pixelated.
FRAME_W, FRAME_H = 160, 120
JPEG_QUALITY = 55
TICS_PER_ACTION = 4  # Doom tics advanced per referee step (~0.11s of game time)
MAX_TICKS = 200  # hard cap so every started table terminates quickly

# `defend_the_center` exposes TURN_LEFT / TURN_RIGHT / ATTACK. We derive the action
# set from whatever buttons the loaded scenario actually offers (so swapping the
# scenario Just Works), always prepending `idle`. `idle` stays first so the referee's
# move-timeout auto-play (legal[0]) is a harmless no-op.
IDLE = "idle"


def _encode_frame(buf: Any) -> str:
    """Encode a HxWx3 uint8 RGB numpy array as a JPEG data URI."""
    from PIL import Image  # lazy: Pillow only needed where a table runs

    img = Image.fromarray(buf)
    out = BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")


class VizDoomGame(GameState):
    """Two headless DoomGame instances stepped in lockstep, one per seat."""

    def __init__(self) -> None:
        import vizdoom  # lazy — see module docstring

        self._vzd = vizdoom
        self._games = [self._make_instance(), self._make_instance()]
        for g in self._games:
            g.init()
            g.new_episode()

        # Action ids derived from the scenario's buttons: idle + one id per button.
        buttons = list(self._games[0].get_available_buttons())
        self._buttons = buttons
        self._n_buttons = len(buttons)
        self._action_ids = [IDLE] + [b.name.lower() for b in buttons]
        # id -> button vector (a 1 in that button's slot, 0 elsewhere; idle = all 0).
        self._vecs: dict[str, list[float]] = {IDLE: [0.0] * self._n_buttons}
        for i, b in enumerate(buttons):
            vec = [0.0] * self._n_buttons
            vec[i] = 1.0
            self._vecs[b.name.lower()] = vec

        self.tick = 0
        self.score = [0.0, 0.0]  # accumulated scenario reward (kills) per seat
        self.done_flags = [False, False]
        self._buffer: dict[int, str] = {}
        self._frames = [self._grab_frame(0), self._grab_frame(1)]
        self._hud = [self._grab_hud(0), self._grab_hud(1)]
        self._closed = False

    def _make_instance(self) -> Any:
        vzd = self._vzd
        g = vzd.DoomGame()
        g.load_config(os.path.join(vzd.scenarios_path, f"{SCENARIO}.cfg"))
        g.set_screen_resolution(vzd.ScreenResolution.RES_160X120)
        g.set_screen_format(vzd.ScreenFormat.RGB24)
        g.set_window_visible(False)  # headless: no X server / framebuffer needed
        g.set_mode(vzd.Mode.PLAYER)
        return g

    # ---- turn structure ----------------------------------------------------

    def current_player(self) -> int:
        players = self.current_players()
        return players[0] if players else TERMINAL

    def current_players(self) -> list[int]:
        if self._done():
            return []
        # simultaneous: every seat that hasn't acted this tick
        return [s for s in (0, 1) if s not in self._buffer]

    def legal_actions(self, player: int) -> list[Action]:
        if player not in self.current_players():
            return []
        return [Action(id=a, label=a) for a in self._action_ids]

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player not in self.current_players():
            raise ValueError("already acted this tick")
        if action_id not in self._vecs:
            raise ValueError(f"bad action {action_id!r}")
        self._buffer[player] = action_id
        if len(self._buffer) == 2:
            self._step()

    def _step(self) -> None:
        actions = dict(self._buffer)
        self._buffer = {}
        self.tick += 1
        for seat in (0, 1):
            g = self._games[seat]
            if self.done_flags[seat] or g.is_episode_finished():
                self.done_flags[seat] = True
                continue
            vec = self._vecs.get(actions.get(seat, IDLE), self._vecs[IDLE])
            reward = g.make_action(vec, TICS_PER_ACTION)
            self.score[seat] += float(reward)
            if g.is_episode_finished():
                self.done_flags[seat] = True
            else:
                self._frames[seat] = self._grab_frame(seat)
                self._hud[seat] = self._grab_hud(seat)

    def _grab_frame(self, seat: int) -> str:
        st = self._games[seat].get_state()
        if st is None or st.screen_buffer is None:
            return self._frames[seat] if getattr(self, "_frames", None) else ""
        return _encode_frame(st.screen_buffer)

    def _grab_hud(self, seat: int) -> dict[str, Any]:
        vzd = self._vzd
        g = self._games[seat]
        return {
            "health": g.get_game_variable(vzd.GameVariable.HEALTH),
            "ammo": g.get_game_variable(vzd.GameVariable.AMMO2),
            "score": self.score[seat],
        }

    def _done(self) -> bool:
        return self.tick >= MAX_TICKS or all(self.done_flags)

    # ---- views -------------------------------------------------------------

    def observation(self, player: int) -> dict[str, Any]:
        return {
            "game": "vizdoom_toy",
            "seat": player,
            "frame": self._frames[player],
            "hud": self._hud[player],
            "tick": self.tick,
            "max_ticks": MAX_TICKS,
            "legal_actions": [a.to_wire() for a in self.legal_actions(player)],
        }

    def public_state(self) -> dict[str, Any]:
        return {
            "game": "vizdoom_toy",
            "tick": self.tick,
            "max_ticks": MAX_TICKS,
            "frames": [self._frames[0], self._frames[1]],
            "hud": [self._hud[0], self._hud[1]],
            "turn": None,
            "winner": self._winner() if self._done() else None,
        }

    # ---- outcome -----------------------------------------------------------

    def _winner(self) -> int | None:
        # Higher score wins; a dead marine can't out-score a live one already handled
        # by score. Ties (equal score at the cap) are draws.
        if self.score[0] > self.score[1]:
            return 0
        if self.score[1] > self.score[0]:
            return 1
        return None

    def returns(self) -> dict[int, float]:
        w = self._winner()
        if w is None:
            return {0: 0.0, 1: 0.0}
        return {w: 1.0, 1 - w: -1.0}

    # ---- cleanup -----------------------------------------------------------
    #
    # Each DoomGame owns a native child process, and the referee has no explicit
    # close hook — it only calls is_terminal()/returns(). MAX_TICKS bounds every
    # game so nothing runs forever, and close()/__del__ release the processes.

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        for g in getattr(self, "_games", []):
            try:
                g.close()
            except Exception:
                pass

    def __del__(self) -> None:
        self.close()


SPEC = register_game(
    GameSpec(
        id="vizdoom_toy",
        name="ViZDoom",
        min_players=2,
        max_players=2,
        factory=VizDoomGame,
        move_timeout_s=1.2,  # fast simultaneous tick rate
    )
)


# ---------------------------------------------------------------------------
# Legacy pure-Python raycaster (unregistered). Kept for reference / as a fallback
# renderer idea; it does NOT register a game and is never instantiated by the hub.
# ---------------------------------------------------------------------------

# 1 = Wall, 0 = Empty Space
MAZE = [
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1],
    [1, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 1],
    [1, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 1],
    [1, 1, 1, 0, 1, 0, 0, 1, 0, 1, 1, 1],
    [1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1],
    [1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1],
    [1, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 1],
    [1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
]

MAP_SIZE = 12
PLAYER_RADIUS = 0.28
MAX_HP = 100
START_AMMO = 20
DAMAGE = 20
LEGACY_MAX_TICKS = 150

LEGACY_ACTIONS = ("idle", "forward", "backward", "left", "right", "shoot")


def check_collision(x: float, y: float) -> bool:
    r = PLAYER_RADIUS
    for dx in [-r, r]:
        for dy in [-r, r]:
            gx = int(x + dx)
            gy = int(y + dy)
            if gx < 0 or gx >= MAP_SIZE or gy < 0 or gy >= MAP_SIZE:
                return True
            if MAZE[gy][gx] == 1:
                return True
    return False


class VizDoomToyLegacy(GameState):
    def __init__(self) -> None:
        self.x = [1.5, 10.5]
        self.y = [10.5, 1.5]
        self.theta = [-0.75 * math.pi, 0.25 * math.pi]
        self.hp = [MAX_HP, MAX_HP]
        self.ammo = [START_AMMO, START_AMMO]
        self.ticks = 0
        self.tracers: list[dict[str, Any]] = []
        self._buffer: dict[int, str] = {}

    def current_player(self) -> int:
        if self._done():
            return TERMINAL
        players = self.current_players()
        return players[0] if players else TERMINAL

    def current_players(self) -> list[int]:
        if self._done():
            return []
        return [s for s in (0, 1) if s not in self._buffer]

    def legal_actions(self, player: int) -> list[Action]:
        if player not in self.current_players():
            return []
        legals = [Action(id=a, label=a) for a in LEGACY_ACTIONS]
        if self.ammo[player] <= 0:
            legals = [a for a in legals if a.id != "shoot"]
        return legals

    def apply_action(self, player: int, action_id: str, payload: Any = None) -> None:
        if player not in self.current_players():
            raise ValueError("already acted this tick")
        if action_id not in LEGACY_ACTIONS:
            raise ValueError(f"bad action {action_id!r}")
        self._buffer[player] = action_id
        if len(self._buffer) == 2:
            self._step()

    def _step(self) -> None:
        actions = dict(self._buffer)
        self._buffer = {}
        self.ticks += 1
        self.tracers = []

        for seat in (0, 1):
            act = actions.get(seat, "idle")
            opp = 1 - seat

            if act == "left":
                self.theta[seat] = (self.theta[seat] - 0.22) % (2 * math.pi)
            elif act == "right":
                self.theta[seat] = (self.theta[seat] + 0.22) % (2 * math.pi)
            elif act == "forward":
                dx = math.cos(self.theta[seat]) * 0.28
                dy = math.sin(self.theta[seat]) * 0.28
                if not check_collision(self.x[seat] + dx, self.y[seat]):
                    self.x[seat] += dx
                if not check_collision(self.x[seat], self.y[seat] + dy):
                    self.y[seat] += dy
            elif act == "backward":
                dx = -math.cos(self.theta[seat]) * 0.20
                dy = -math.sin(self.theta[seat]) * 0.20
                if not check_collision(self.x[seat] + dx, self.y[seat]):
                    self.x[seat] += dx
                if not check_collision(self.x[seat], self.y[seat] + dy):
                    self.y[seat] += dy
            elif act == "shoot" and self.ammo[seat] > 0:
                self.ammo[seat] -= 1
                self._cast_shoot_ray(seat, opp)

    def _cast_shoot_ray(self, shooter: int, target: int) -> None:
        sx, sy = self.x[shooter], self.y[shooter]
        angle = self.theta[shooter]
        tx, ty = self.x[target], self.y[target]

        hit = False
        hit_x, hit_y = sx, sy
        max_dist = 15.0
        step = 0.05
        dist = 0.0

        while dist < max_dist:
            dist += step
            rx = sx + math.cos(angle) * dist
            ry = sy + math.sin(angle) * dist

            gx, gy = int(rx), int(ry)
            if (
                gx < 0
                or gx >= MAP_SIZE
                or gy < 0
                or gy >= MAP_SIZE
                or MAZE[gy][gx] == 1
            ):
                hit_x, hit_y = rx, ry
                break

            dist_to_opp = math.hypot(rx - tx, ry - ty)
            if dist_to_opp <= PLAYER_RADIUS:
                hit = True
                hit_x, hit_y = tx, ty
                break

        if hit:
            self.hp[target] = max(0, self.hp[target] - DAMAGE)

        self.tracers.append(
            {"from": [sx, sy], "to": [hit_x, hit_y], "hit": hit, "shooter": shooter}
        )

    def _done(self) -> bool:
        return self.hp[0] <= 0 or self.hp[1] <= 0 or self.ticks >= LEGACY_MAX_TICKS

    def observation(self, player: int) -> dict[str, Any]:
        opp = 1 - player
        return {
            "game": "vizdoom_toy",
            "seat": player,
            "map": MAZE,
            "me": {
                "x": self.x[player],
                "y": self.y[player],
                "theta": self.theta[player],
                "hp": self.hp[player],
                "ammo": self.ammo[player],
            },
            "opponent": {
                "x": self.x[opp],
                "y": self.y[opp],
                "theta": self.theta[opp],
                "hp": self.hp[opp],
                "ammo": self.ammo[opp],
            },
            "tracers": self.tracers,
            "legal_actions": [a.to_wire() for a in self.legal_actions(player)],
        }

    def public_state(self) -> dict[str, Any]:
        return {
            "game": "vizdoom_toy",
            "map": MAZE,
            "ticks": self.ticks,
            "max_ticks": LEGACY_MAX_TICKS,
            "p": [
                {
                    "x": self.x[0],
                    "y": self.y[0],
                    "theta": self.theta[0],
                    "hp": self.hp[0],
                    "ammo": self.ammo[0],
                },
                {
                    "x": self.x[1],
                    "y": self.y[1],
                    "theta": self.theta[1],
                    "hp": self.hp[1],
                    "ammo": self.ammo[1],
                },
            ],
            "tracers": self.tracers,
            "turn": None,
            "winner": self._winner() if self._done() else None,
        }

    def _winner(self) -> int | None:
        if self.hp[0] <= 0 and self.hp[1] > 0:
            return 1
        if self.hp[1] <= 0 and self.hp[0] > 0:
            return 0
        if self.ticks >= LEGACY_MAX_TICKS:
            if self.hp[0] > self.hp[1]:
                return 0
            if self.hp[1] > self.hp[0]:
                return 1
        return None

    def returns(self) -> dict[int, float]:
        w = self._winner()
        if w is None:
            return {0: 0.0, 1: 0.0}
        return {w: 1.0, 1 - w: -1.0}
