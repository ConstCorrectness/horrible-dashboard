"""Generate the cross-language flashbang curve fixture for HorribleAssault.

    PYTHONPATH=. uv run python scripts/gen_hassault_flash_vectors.py

The server sends a flash as three remaining durations and each client draws the
white itself, at its own frame rate, from `grenades.flash_alpha`. That function
therefore exists three times — `grenades.py`, `flashbang.ts` and
`apps/native-fps/src/flashbang.rs` — and this fixture is what keeps the three
drawing the same screen. It also pins the hold-then-fade countdown
(`MatchPlayer.step_flash`), because a client that spilled the end of the hold
into the fade differently would drift from the server between snapshots.

Expectations come from the Python side, so this pins *agreement*, not
correctness; `test_hassault_grenades.py` is the argument that the curve is right.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.modules.hassault import grenades

OUT = Path("packages/core/src/modules/hassault/__tests__/flash-vectors.json")

ALPHA_CASES = [
    # (peak, hold_left, fade_left, fade_total)
    (1.0, 2.0, 3.0, 3.0),
    (1.0, 0.0, 3.0, 3.0),
    (1.0, 0.0, 2.25, 3.0),
    (1.0, 0.0, 1.5, 3.0),
    (1.0, 0.0, 0.75, 3.0),
    (1.0, 0.0, 0.1, 3.0),
    (1.0, 0.0, 0.0, 3.0),
    (0.9, 0.05, 1.35, 1.35),
    (0.9, 0.0, 0.4, 1.35),
    (0.5, 0.0, 0.75, 0.75),
    (0.5, 0.0, 0.2, 0.75),
    (0.0, 1.0, 1.0, 1.0),
    (1.0, 0.0, 1.0, 0.0),
    (1.4, 0.3, 1.0, 1.0),
]

# (peak, hold, fade, dt per step, steps)
STEP_CASES = [
    (1.0, 0.5, 1.0, 0.05, 40),
    (1.0, 2.2, 3.0, 1 / 60, 360),
    (0.9, 0.088, 1.35, 0.05, 32),
    (0.5, 0.0, 0.75, 0.2, 6),
    (1.0, 0.3, 0.6, 0.5, 4),
]


class _Flash:
    """`MatchPlayer`'s flash fields and countdown, without a room around them."""

    def __init__(self, peak: float, hold: float, fade: float) -> None:
        self.flash_peak = peak
        self.flash_hold = hold
        self.flash_fade = fade
        self.flash_fade_total = fade

    def step(self, dt: float) -> None:
        from backend.modules.hassault.match import MatchPlayer

        MatchPlayer.step_flash(self, dt)  # type: ignore[arg-type]

    @property
    def alpha(self) -> float:
        return grenades.flash_alpha(
            self.flash_peak, self.flash_hold, self.flash_fade, self.flash_fade_total
        )


def main() -> None:
    alpha = [
        {
            "peak": p,
            "hold": h,
            "fade": f,
            "fadeTotal": t,
            "alpha": grenades.flash_alpha(p, h, f, t),
        }
        for p, h, f, t in ALPHA_CASES
    ]
    steps = []
    for peak, hold, fade, dt, count in STEP_CASES:
        state = _Flash(peak, hold, fade)
        series = []
        for _ in range(count):
            state.step(dt)
            series.append(
                {
                    "hold": state.flash_hold,
                    "fade": state.flash_fade,
                    "alpha": state.alpha,
                }
            )
        steps.append(
            {"peak": peak, "hold": hold, "fade": fade, "dt": dt, "series": series}
        )
    OUT.write_text(
        json.dumps(
            {
                "generator": "scripts/gen_hassault_flash_vectors.py",
                "tolerance": 1e-9,
                "blind": grenades.FLASH_BLIND,
                "alpha": alpha,
                "steps": steps,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {OUT} ({len(alpha)} alpha cases, {len(steps)} step series)")


if __name__ == "__main__":
    main()
