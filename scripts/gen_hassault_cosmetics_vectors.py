"""Generate `cosmetics-vectors.json`: how a player looks, pinned three ways.

    PYTHONPATH=. uv run python scripts/gen_hassault_cosmetics_vectors.py

`cosmetics.palette` is the reference for where a skin's colours land on a weapon
made of boxes; `viewmodel.ts` `paletteFor` and `viewmodel.rs` `palette_for` are
its ports, and every built-in skin is a case, at three wears. The fixture also
carries the served agent list, the bundle wire as the server writes it, and the
per-side loadout rule (`weapons.loadouts_for`), which the training ranges port.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.modules.hassault import cosmetics, weapons
from backend.modules.hassault.skins import SKIN_CATALOG

OUT = Path("packages/core/src/modules/hassault/__tests__/cosmetics-vectors.json")

WEARS = (0.0, 0.45, 1.0)


def _rgb(c: tuple[float, float, float]) -> list[float]:
    return [round(v, 6) for v in c]


def _palette_cases() -> list[dict[str, Any]]:
    cases = [
        {
            "name": "no skin",
            "skin": None,
            "palette": {k: _rgb(v) for k, v in cosmetics.palette(None).items()},
        }
    ]
    for skin in SKIN_CATALOG:
        for wear in WEARS:
            entry = {
                "baseColor": skin.base_color,
                "accentColor": skin.accent_color,
                "patternType": skin.pattern_type,
                "floatValue": wear,
            }
            cases.append(
                {
                    "name": f"{skin.id} at {wear}",
                    "skin": entry,
                    "palette": {
                        k: _rgb(v) for k, v in cosmetics.palette(entry).items()
                    },
                }
            )
    return cases


def _item(instance: str, skin_id: str, wear: float) -> dict[str, Any]:
    skin = next(s for s in SKIN_CATALOG if s.id == skin_id)
    return {
        "instanceId": instance,
        "skinId": skin.id,
        "floatValue": wear,
        "patternSeed": 42,
        "statTrackerKills": 7,
        "definition": skin.to_dict(),
    }


def _wire() -> dict[str, Any]:
    rifle = next(s for s in SKIN_CATALOG if s.weapon_id == "assault")
    pistol = next(s for s in SKIN_CATALOG if s.weapon_id == "pistol")
    neutral = next(a for a in cosmetics.AGENTS if a.faction == "neutral")
    look = cosmetics.bundle(
        {"t": "scavenger_prime", "ct": neutral.id},
        {
            "t": [_item("i1", rifle.id, 0.07)],
            "ct": [_item("i1", rifle.id, 0.07), _item("i2", pistol.id, 0.62)],
        },
    )
    return {
        "playerId": "p1",
        "welcome": {"cosmetics": {"p1": look}},
        "joined": {"player": {"id": "p1", "team": 1}, "cosmetics": look},
        "cosmetics": {"playerId": "p1", "cosmetics": look},
    }


LOADOUT_CHOICES: list[tuple[str, Any]] = [
    ("never chosen", None),
    ("flat, both sides", {"primary": "shotgun", "secondary": "hand_cannon"}),
    ("flat, a T rifle", {"primary": "raider"}),
    ("per side", {"t": {"primary": "raider"}, "ct": {"primary": "lancer"}}),
    ("per side, crossed", {"t": {"primary": "lancer"}, "ct": {"primary": "raider"}}),
    ("wrong slot", {"t": {"primary": "pistol", "secondary": "sniper"}}),
    ("as the stored string", json.dumps({"ct": {"secondary": "hand_cannon"}})),
]


def _loadout_cases() -> list[dict[str, Any]]:
    out = []
    for name, choice in LOADOUT_CHOICES:
        sides = weapons.loadouts_for(choice)
        out.append(
            {
                "name": name,
                "choice": choice,
                "sides": {
                    side: {slot: weapons.WEAPONS[i].id for slot, i in chosen.items()}
                    for side, chosen in sides.items()
                },
            }
        )
    return out


def payload() -> dict[str, Any]:
    return {
        "minLuma": cosmetics.MIN_LUMA,
        "palettes": _palette_cases(),
        "agents": [a.to_dict() for a in cosmetics.AGENTS],
        "defaultAgents": cosmetics.DEFAULT_AGENTS,
        "wire": _wire(),
        "weapons": [
            {"id": w.id, "slot": w.slot, "side": w.side} for w in weapons.WEAPONS
        ],
        "loadouts": _loadout_cases(),
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(payload(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
