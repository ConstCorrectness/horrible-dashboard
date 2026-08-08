"""The episode runner behind the Train section: play a script bot N times and
report what happened.

This is the fast inner loop the ladder can't be — a rated match tells you one bit
every couple of minutes, where two hundred headless episodes tell you a win rate in
under a second. It runs the **same** `HorribleEnv` a user trains against in a
notebook and the **same** `bot_sdk` shapes a ranked seat runs, so a number measured
here means the thing it appears to mean.

Two decisions worth stating:

**Seats alternate every episode.** Going first is worth a great deal in these games,
so a win rate measured only as X is not a win rate. Odd episodes play seat 1.

**The bot is compiled once for the whole run, not once per episode.** A `class Agent`
therefore keeps its state across episodes, which is exactly what makes an in-pane
tabular learner possible — `reset()` marks an episode boundary, `observe()` delivers
the terminal reward, and anything the agent accumulates in between is its own. A
per-episode recompile would have quietly made learning impossible while looking
correct.

The bot is the player's own code running in-process, the same as a live `bot` seat
(see policy.py). The guard here is wall-clock: a runaway loop is bounded by
`deadline_s` between episodes and by the Env's own `MAX_PLIES` within one.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from backend.games_engine.env import HorribleEnv, random_opponent
from backend.games_engine.env_adapter import adapter_for
from backend.modules.games.bot_sdk import CompiledBot, coerce_action, compile_bot

logger = logging.getLogger(__name__)

# A whole run may not outlast this, however many episodes were asked for. The UI
# offers bounded choices, but the route is reachable directly.
DEFAULT_DEADLINE_S = 20.0
# How many points the reward curve is reported at, regardless of episode count —
# the UI draws a fixed-width sparkline.
CURVE_BUCKETS = 20


@dataclass
class RunResult:
    ok: bool = True
    error: str | None = None
    shape: str = ""
    episodes: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    illegal: int = 0
    truncated: int = 0
    mean_reward: float = 0.0
    curve: list[float] = field(default_factory=list)
    elapsed_ms: int = 0
    stopped_early: bool = False
    # One episode worth replaying — a loss if there was one, else the last episode.
    sample: dict[str, Any] | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "shape": self.shape,
            "episodes": self.episodes,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
            "illegal": self.illegal,
            "truncated": self.truncated,
            "mean_reward": round(self.mean_reward, 4),
            "curve": [round(v, 4) for v in self.curve],
            "elapsed_ms": self.elapsed_ms,
            "stopped_early": self.stopped_early,
            "sample": self.sample,
        }


def _opponent_fn(game_id: str, spec: str, rng: random.Random):
    """Resolve the opponent selector into a move function.

    `bot:<tier>` reuses the **game server's own practice bots**, so "beat Rusty" in
    Training and "beat Rusty" in a practice match are the same claim rather than two
    different bots that happen to share a name.
    """
    if spec.startswith("bot:"):
        from backend.games_server import bots

        tier = spec.split(":", 1)[1] or "bronze"

        def play_bot(
            obs: dict[str, Any], legal: list[dict[str, Any]], _seat: int
        ) -> str:
            action_id, _payload = bots.choose_action(game_id, tier, obs, legal, rng)
            return action_id

        return play_bot
    return random_opponent(rng)


def _act(
    bot: CompiledBot, env: HorribleEnv, info: dict[str, Any]
) -> tuple[int | None, Any]:
    """One decision: run the bot and turn its answer into an action index.

    Returns `(None, raw)` when the answer isn't a legal move — the caller counts
    that as an illegal action rather than substituting something playable, for the
    same reason the Env doesn't (see its module docstring).

    `legal_actions` is injected into the observation exactly as `BotPolicy` does in
    a live match: the legacy `run(args, obs)` shape reads its moves off the *obs*,
    having no `info`, so a legacy bot that plays fine on the ladder would otherwise
    `KeyError` the moment you tried to train it — the one place the two paths must
    not differ.
    """
    obs_for_bot = dict(info["raw_obs"])
    obs_for_bot.setdefault("legal_actions", info["legal_actions"])
    raw = bot.act(obs_for_bot, info)
    legal_ids = [str(a["id"]) for a in info["legal_actions"]]
    chosen = coerce_action(raw, legal_ids, env.adapter)
    if chosen is None:
        return None, raw
    return env.adapter.to_index(chosen), raw


def run_episodes(
    game_id: str,
    code: str,
    *,
    opponent: str = "bot:bronze",
    episodes: int = 100,
    seed: int = 0,
    deadline_s: float = DEFAULT_DEADLINE_S,
) -> RunResult:
    """Play `episodes` games and summarise. Never raises for a bad bot — a broken
    policy is a *result*, and the Train section's job is to show you which kind."""
    adapter = adapter_for(game_id)
    if adapter is None:
        return RunResult(
            ok=False,
            error=(
                f"{game_id} has no RL environment — it is a reasoner game whose "
                "actions are payloads (a patch, an answer), not points in a space. "
                "Use the single-turn dry run instead."
            ),
        )
    if not adapter.training.self_play:
        return RunResult(
            ok=False,
            error=f"{game_id} does not support headless self-play runs.",
        )

    try:
        bot = compile_bot(code, f"<train:{game_id}>")
    except Exception as exc:
        return RunResult(ok=False, error=f"bot failed to load: {exc}")

    episodes = max(1, min(int(episodes), adapter.training.max_episodes))
    rng = random.Random(seed)
    result = RunResult(shape=bot.shape)
    rewards: list[float] = []
    started = time.monotonic()
    loss_sample: dict[str, Any] | None = None
    last_sample: dict[str, Any] | None = None

    for episode in range(episodes):
        if time.monotonic() - started > deadline_s:
            result.stopped_early = True
            break

        # Alternate seats: see the module docstring.
        seat = episode % 2
        env = HorribleEnv(
            game_id,
            seat=seat,
            opponent=_opponent_fn(game_id, opponent, rng),
            seed=seed + episode,
        )
        obs, info = env.reset(seed=seed + episode)
        bot.reset(info["raw_obs"], info)

        trace: list[dict[str, Any]] = []
        reward, terminated, truncated, illegal = 0.0, False, False, False

        while not (terminated or truncated):
            index, raw = _act(bot, env, info)
            if index is None:
                illegal, terminated, reward = True, True, -1.0
                trace.append(
                    {"seat": seat, "illegal": True, "returned": repr(raw)[:120]}
                )
                break
            trace.append({"seat": seat, "action": env.adapter.to_action_id(index)})
            obs, reward, terminated, truncated, info = env.step(int(index))
            if info.get("illegal"):
                illegal = True

        bot.observe(float(reward), bool(terminated), info)
        rewards.append(float(reward))

        if illegal:
            result.illegal += 1
        if truncated:
            result.truncated += 1
        if reward > 0:
            result.wins += 1
        elif reward < 0:
            result.losses += 1
        else:
            result.draws += 1

        snapshot = {
            "episode": episode,
            "seat": seat,
            "reward": float(reward),
            "illegal": illegal,
            "moves": trace,
            "final": info.get("raw_obs"),
        }
        last_sample = snapshot
        if reward < 0 and loss_sample is None:
            loss_sample = snapshot

    result.episodes = len(rewards)
    result.mean_reward = sum(rewards) / len(rewards) if rewards else 0.0
    result.curve = _curve(rewards)
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    # A loss is the interesting episode; fall back to the last one when unbeaten.
    result.sample = loss_sample or last_sample
    return result


def _curve(rewards: list[float]) -> list[float]:
    """Mean reward per bucket, at a fixed width so the sparkline is comparable
    between a 50-episode run and a 2000-episode one."""
    if not rewards:
        return []
    buckets = min(CURVE_BUCKETS, len(rewards))
    size = len(rewards) / buckets
    out: list[float] = []
    for i in range(buckets):
        chunk = rewards[int(i * size) : int((i + 1) * size)] or [0.0]
        out.append(sum(chunk) / len(chunk))
    return out
