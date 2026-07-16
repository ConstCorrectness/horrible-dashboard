"""ViZDoom Duel: a **real networked deathmatch** between two agents.

Where `vizdoom_toy` runs two *independent* marines on separate maps and calls the
higher score the winner (a race), this game connects two headless `vizdoom.DoomGame`
instances over ZDoom's own netcode so they share **one map** and actually shoot each
other — a face-to-face 1v1 on the bundled `cig` arena. Score is **frags** (kills of
the opponent, minus suicides); whoever has more when the clock runs out wins.

Why the design looks the way it does — hard-won from the native engine:

- **ASYNC, free-running, one thread per instance.** ViZDoom's *synchronous*
  multiplayer (`Mode.PLAYER`) requires every player to submit an action for the same
  tic before any instance advances; driving both from one process in lockstep
  deadlocks intermittently. `Mode.ASYNC_PLAYER` runs the engine in real time and
  `make_action` never blocks on the other player, so each instance gets its **own**
  free-running worker thread. A `DoomGame` is not thread-safe, so each instance is
  touched *only* by its worker; the worker publishes a plain-dict snapshot
  (frame + HUD + frags) under a lock and the referee/observation reads *that*, never
  the live `DoomGame`.
- **Respawn happens inside the worker** (`sv_forcerespawn` + `respawn_player`), so a
  dead marine coming back never stalls the other's real-time loop.
- **Degraded fallback.** If the netcode can't connect (blocked UDP, missing native
  bits, a busy host), the engine falls back to two *independent* `cig` instances so
  the table still completes and the board still renders — `public_state().mode`
  reports `"duel"` vs `"degraded"`.

The wire shape matches `vizdoom_toy` on purpose (`frames`/`hud`/`tick`/`winner`), so
`VizDoomBoard.tsx` and the fast `vizdoom_duel.bot` loadout path are reused unchanged.

Requires the `vizdoom` and `pillow` packages; both are imported lazily so importing
this module (to register the spec) never hard-fails on a node without the native
wheel — only *starting a table* does.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

from backend.games_engine.base import (
    TERMINAL,
    Action,
    GameSpec,
    GameState,
    register_game,
)
from backend.games_engine.vizdoom_toy import _encode_frame

logger = logging.getLogger(__name__)

# ---- config ----------------------------------------------------------------

SCENARIO = "cig"  # bundled competition arena (map01) with a clean binary button set
IDLE = "idle"

TICS_PER_LOOP = 2  # engine tics each worker advances per make_action (async cadence)
STEP_DWELL_S = 0.06  # real time a committed action is held before the next prompt
MAX_TICKS = 150  # hard cap so every started table terminates quickly (~9s of play)
CONNECT_TIMEOUT_S = 20.0  # ZDoom `viz_connect_timeout`; init blocks up to this long

# The host binds a UDP port for the joiner. Many tables may run in one game-server
# process, so each duel grabs a random high port (a collision just trips the
# degraded fallback, never a wrong-opponent connect).
_PORT_LO, _PORT_HI = 20000, 60000


def _button_action_ids(buttons: list[Any]) -> list[str]:
    """`idle` + one id per **binary** button (delta buttons — continuous aim/turn —
    are dropped: an enumerated agent can't press a float)."""
    return [IDLE] + [
        b.name.lower() for b in buttons if not b.name.upper().endswith("_DELTA")
    ]


def _configure(g: Any, vzd: Any, visible: bool) -> None:
    import os

    g.load_config(os.path.join(vzd.scenarios_path, f"{SCENARIO}.cfg"))
    g.set_screen_resolution(vzd.ScreenResolution.RES_160X120)
    g.set_screen_format(vzd.ScreenFormat.RGB24)
    g.set_window_visible(visible)
    g.set_console_enabled(False)  # suppress engine logging to stdout/stderr
    g.set_mode(vzd.Mode.ASYNC_PLAYER)  # real-time: make_action never blocks on the peer


def open_duel_pair(
    vzd: Any,
    *,
    port: int | None = None,
    connect_timeout: float = CONNECT_TIMEOUT_S,
    visible: bool = False,
) -> list[Any]:
    """Create + init two networked `DoomGame` instances (host at seat 0, joiner at
    seat 1) sharing one deathmatch map. `init()` blocks until both connect, and the
    host's blocks until the joiner arrives — so the two inits run concurrently on
    their own threads. Returns `[host, join]`; raises on any connect failure.

    Shared by the engine and the local duel eval harness."""
    port = port or random.randint(_PORT_LO, _PORT_HI)
    host, join = vzd.DoomGame(), vzd.DoomGame()
    _configure(host, vzd, visible)
    _configure(join, vzd, visible)
    host.add_game_args(
        f"-host 2 -port {port} -deathmatch "
        "+timelimit 10.0 +sv_forcerespawn 1 +sv_noautoaim 1 "
        f"+sv_respawnprotect 1 +viz_respawn_delay 1 +viz_connect_timeout {int(connect_timeout)}"
    )
    host.add_game_args("+name Marine0 +colorset 0")
    join.add_game_args(
        f"-join 127.0.0.1:{port} +viz_connect_timeout {int(connect_timeout)}"
    )
    join.add_game_args("+name Marine1 +colorset 3")

    games = [host, join]
    errs: dict[int, str] = {}

    def _init(i: int) -> None:
        try:
            games[i].init()
        except Exception as exc:  # noqa: BLE001 — report, the caller decides fallback
            errs[i] = repr(exc)

    threads = [threading.Thread(target=_init, args=(i,), daemon=True) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(connect_timeout + 10.0)
    if errs or any(t.is_alive() for t in threads):
        for g in games:
            try:
                g.close()
            except Exception:
                pass
        raise RuntimeError(f"vizdoom duel connect failed: {errs or 'timeout'}")
    return games


def _open_solo_pair(vzd: Any, *, visible: bool = False) -> list[Any]:
    """Degraded fallback: two *independent* (non-networked) `cig` instances. Same
    worker loop drives them, so the table completes and the board renders — it just
    isn't a real face-to-face duel."""
    games = [vzd.DoomGame(), vzd.DoomGame()]
    for g in games:
        _configure(g, vzd, visible)
        g.init()
        g.new_episode()
    return games


class VizDoomDuel(GameState):
    """Two networked marines on one map, each stepped by its own free-running worker
    thread; the referee sets held actions and reads published snapshots."""

    def __init__(self) -> None:
        import vizdoom  # lazy — see module docstring

        self._vzd = vizdoom
        self._closed = False
        self._stop = threading.Event()
        self._lock = threading.Lock()

        try:
            self._games = open_duel_pair(vizdoom)
            self.mode = "duel"
        except Exception:
            logger.warning("vizdoom duel netcode unavailable; degraded", exc_info=True)
            self._games = _open_solo_pair(vizdoom)
            self.mode = "degraded"

        buttons = list(self._games[0].get_available_buttons())
        self._n_buttons = len(buttons)
        self._action_ids = _button_action_ids(buttons)
        # id -> button vector (1.0 in that button's slot; idle = all 0.0).
        self._vecs: dict[str, list[float]] = {IDLE: [0.0] * self._n_buttons}
        for i, b in enumerate(buttons):
            name = b.name.lower()
            if name in self._action_ids:
                vec = [0.0] * self._n_buttons
                vec[i] = 1.0
                self._vecs[name] = vec

        self.tick = 0
        self.done_flags = [False, False]
        self._held: list[list[float]] = [self._vecs[IDLE], self._vecs[IDLE]]
        self._snap: list[dict[str, Any]] = [self._blank_snap(), self._blank_snap()]
        self._buffer: dict[int, str] = {}

        # Start the free-running workers, then wait (bounded) until both have
        # published a first frame, so the opening observation/public_state already
        # carries viewports — like vizdoom_toy grabbing frames in its __init__.
        self._workers = [
            threading.Thread(target=self._worker, args=(i,), daemon=True)
            for i in (0, 1)
        ]
        for w in self._workers:
            w.start()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            with self._lock:
                if all(self._snap[s]["frame"] for s in (0, 1)) or all(self.done_flags):
                    break
            time.sleep(0.03)

    # ---- workers (own their DoomGame exclusively) --------------------------

    def _blank_snap(self) -> dict[str, Any]:
        return {"frame": "", "health": 0.0, "ammo": 0.0, "frags": 0.0}

    def _worker(self, seat: int) -> None:
        vzd = self._vzd
        g = self._games[seat]
        while not self._stop.is_set():
            try:
                if g.is_episode_finished():
                    with self._lock:
                        self.done_flags[seat] = True
                    break
                if g.is_player_dead():
                    g.respawn_player()  # respawn on the worker so the peer keeps ticking
                with self._lock:
                    action = list(self._held[seat])
                g.make_action(action, TICS_PER_LOOP)
                snap = self._read_snapshot(g, vzd)
                with self._lock:
                    self._snap[seat] = snap
            except Exception:
                logger.debug("vizdoom duel worker %s errored", seat, exc_info=True)
                with self._lock:
                    self.done_flags[seat] = True
                break

    def _read_snapshot(self, g: Any, vzd: Any) -> dict[str, Any]:
        state = g.get_state()
        frame = (
            _encode_frame(state.screen_buffer)
            if state is not None and state.screen_buffer is not None
            else ""
        )
        return {
            "frame": frame,
            "health": float(g.get_game_variable(vzd.GameVariable.HEALTH)),
            "ammo": float(g.get_game_variable(vzd.GameVariable.AMMO2)),
            "frags": float(g.get_game_variable(vzd.GameVariable.FRAGCOUNT)),
        }

    # ---- turn structure (simultaneous, buffered like vizdoom_toy) ----------

    def current_player(self) -> int:
        players = self.current_players()
        return players[0] if players else TERMINAL

    def current_players(self) -> list[int]:
        if self._done():
            return []
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
        with self._lock:
            self._held[player] = self._vecs[action_id]
        self._buffer[player] = action_id
        if len(self._buffer) == 2:
            self._step()

    def _step(self) -> None:
        self._buffer = {}
        self.tick += 1
        # Let both committed actions actually play for a beat of real time — the
        # workers are free-running, so this just paces the referee's prompts.
        time.sleep(STEP_DWELL_S)

    def _done(self) -> bool:
        return self.tick >= MAX_TICKS or all(self.done_flags)

    # ---- views -------------------------------------------------------------

    def _hud(self, seat: int) -> dict[str, Any]:
        with self._lock:
            snap = self._snap[seat]
            return {
                "health": snap["health"],
                "ammo": snap["ammo"],
                "score": snap["frags"],
            }

    def _frame(self, seat: int) -> str:
        with self._lock:
            return self._snap[seat]["frame"]

    def observation(self, player: int) -> dict[str, Any]:
        return {
            "game": "vizdoom_duel",
            "seat": player,
            "frame": self._frame(player),
            "hud": self._hud(player),
            "tick": self.tick,
            "max_ticks": MAX_TICKS,
            "mode": self.mode,
            "legal_actions": [a.to_wire() for a in self.legal_actions(player)],
        }

    def public_state(self) -> dict[str, Any]:
        return {
            "game": "vizdoom_duel",
            "tick": self.tick,
            "max_ticks": MAX_TICKS,
            "mode": self.mode,
            "frames": [self._frame(0), self._frame(1)],
            "hud": [self._hud(0), self._hud(1)],
            "turn": None,
            "winner": self._winner() if self._done() else None,
        }

    # ---- outcome -----------------------------------------------------------

    def _frags(self, seat: int) -> float:
        with self._lock:
            return self._snap[seat]["frags"]

    def _winner(self) -> int | None:
        a, b = self._frags(0), self._frags(1)
        if a > b:
            return 0
        if b > a:
            return 1
        return None

    def returns(self) -> dict[int, float]:
        w = self._winner()
        if w is None:
            return {0: 0.0, 1: 0.0}
        return {w: 1.0, 1 - w: -1.0}

    # ---- cleanup -----------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        for w in getattr(self, "_workers", []):
            w.join(timeout=5.0)
        for g in getattr(self, "_games", []):
            try:
                g.close()
            except Exception:
                pass

    def __del__(self) -> None:
        self.close()


SPEC = register_game(
    GameSpec(
        id="vizdoom_duel",
        name="ViZDoom Duel",
        min_players=2,
        max_players=2,
        factory=VizDoomDuel,
        move_timeout_s=1.2,  # fast simultaneous tick rate, like the toy
    )
)
