"""Gymnasium environments as an environment provider.

The backend env deliberately does not install gymnasium (it's heavy and only the
project venv runs it), so search/resolve work off a bundled static catalog of the
standard registry rather than importing the package. The catalog covers the envs
shipped with gymnasium's classic-control/box2d/toy-text/mujoco/atari namespaces;
anything missing still resolves as an uncurated ref with a warning in `meta`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.modules.training.models import EnvironmentRefModel
from backend.modules.training.providers.base import (
    FetchResult,
    ProgressFn,
    ScaffoldResult,
    code_cell,
    md_cell,
)

# env id → (namespace, extra pip spec). Kept to the well-known registry entries;
# resolve() accepts unknown ids too (custom/third-party envs registered at runtime).
CATALOG: dict[str, tuple[str, str | None]] = {
    # classic control
    "CartPole-v1": ("classic-control", None),
    "MountainCar-v0": ("classic-control", None),
    "MountainCarContinuous-v0": ("classic-control", None),
    "Pendulum-v1": ("classic-control", None),
    "Acrobot-v1": ("classic-control", None),
    # box2d
    "LunarLander-v3": ("box2d", "gymnasium[box2d]"),
    "LunarLanderContinuous-v3": ("box2d", "gymnasium[box2d]"),
    "BipedalWalker-v3": ("box2d", "gymnasium[box2d]"),
    "BipedalWalkerHardcore-v3": ("box2d", "gymnasium[box2d]"),
    "CarRacing-v3": ("box2d", "gymnasium[box2d]"),
    # toy text
    "Blackjack-v1": ("toy-text", None),
    "FrozenLake-v1": ("toy-text", None),
    "FrozenLake8x8-v1": ("toy-text", None),
    "CliffWalking-v0": ("toy-text", None),
    "Taxi-v3": ("toy-text", None),
    # mujoco
    "Ant-v5": ("mujoco", "gymnasium[mujoco]"),
    "HalfCheetah-v5": ("mujoco", "gymnasium[mujoco]"),
    "Hopper-v5": ("mujoco", "gymnasium[mujoco]"),
    "Humanoid-v5": ("mujoco", "gymnasium[mujoco]"),
    "HumanoidStandup-v5": ("mujoco", "gymnasium[mujoco]"),
    "InvertedDoublePendulum-v5": ("mujoco", "gymnasium[mujoco]"),
    "InvertedPendulum-v5": ("mujoco", "gymnasium[mujoco]"),
    "Pusher-v5": ("mujoco", "gymnasium[mujoco]"),
    "Reacher-v5": ("mujoco", "gymnasium[mujoco]"),
    "Swimmer-v5": ("mujoco", "gymnasium[mujoco]"),
    "Walker2d-v5": ("mujoco", "gymnasium[mujoco]"),
    # atari (ALE)
    "ALE/Breakout-v5": ("atari", "gymnasium[atari]"),
    "ALE/Pong-v5": ("atari", "gymnasium[atari]"),
    "ALE/SpaceInvaders-v5": ("atari", "gymnasium[atari]"),
    "ALE/MsPacman-v5": ("atari", "gymnasium[atari]"),
    "ALE/Seaquest-v5": ("atari", "gymnasium[atari]"),
    "ALE/Asteroids-v5": ("atari", "gymnasium[atari]"),
}


def _ref(env_id: str, namespace: str, curated: bool = True) -> EnvironmentRefModel:
    return EnvironmentRefModel(
        provider="gymnasium",
        kind="env",
        id=env_id,
        title=env_id,
        url="https://gymnasium.farama.org/environments/"
        + (f"{namespace}/" if curated else ""),
        meta={"namespace": namespace, "curated": curated},
    )


class GymnasiumProvider:
    provider = "gymnasium"
    label = "Gymnasium"
    kinds = ("env",)

    def search(
        self, query: str, kind: str | None, limit: int
    ) -> list[EnvironmentRefModel]:
        q = query.lower()
        hits = [
            _ref(env_id, ns)
            for env_id, (ns, _extra) in CATALOG.items()
            if q in env_id.lower() or q in ns
        ]
        return hits[:limit]

    def resolve(self, ref_id: str, kind: str | None) -> EnvironmentRefModel:
        entry = CATALOG.get(ref_id)
        if entry is not None:
            return _ref(ref_id, entry[0])
        # Unknown id: allow it (custom envs exist), flagged as uncurated.
        return _ref(ref_id, "custom", curated=False)

    def fetch(
        self, ref: EnvironmentRefModel, dest: Path, progress: ProgressFn
    ) -> FetchResult:
        progress("gymnasium envs need no data download", 1.0)
        return FetchResult(note="nothing to fetch — env is constructed at runtime")

    def scaffold(self, ref: EnvironmentRefModel, project: Any) -> ScaffoldResult:
        entry = CATALOG.get(ref.id)
        extra = entry[1] if entry else None
        requirements = [extra or "gymnasium[classic-control]", "torch", "numpy"]
        return ScaffoldResult(
            cells=[
                md_cell(
                    f"# {ref.id}\n\nGymnasium environment. The rollout loop below "
                    "streams frames to the Training rollout pane and metrics to "
                    "the chart pane."
                ),
                code_cell(
                    "import gymnasium as gym\n"
                    "import horrible_train as ht\n\n"
                    f'env = gym.make("{ref.id}", render_mode="rgb_array")\n'
                    "obs, info = env.reset(seed=0)\n"
                    "print(env.observation_space, env.action_space)"
                ),
                code_cell(
                    "# Random rollout — replace the policy with your agent.\n"
                    "total = 0.0\n"
                    "for step in range(200):\n"
                    "    obs, reward, terminated, truncated, info = env.step("
                    "env.action_space.sample())\n"
                    "    total += reward\n"
                    "    ht.frame(env.render())\n"
                    "    if terminated or truncated:\n"
                    "        ht.log(step=step, episode_reward=total)\n"
                    "        total = 0.0\n"
                    "        obs, info = env.reset()\n"
                    "env.close()"
                ),
            ],
            requirements=requirements,
        )
