"""Generate `economy-vectors.json`: the buy catalogue and its menu, pinned three ways.

    PYTHONPATH=. uv run python scripts/gen_hassault_economy_vectors.py

`modes/defuse.py` builds the catalogue from the weapon table, so its rows and
indexes move whenever a gun is added. Both clients group it into the CS menu
(`buy-menu.ts`, `buy_menu.rs`): a key opens a group, a key buys a row in it, and
a side sees only what it may buy. This fixture carries the served catalogue and
the groups each side must see, so a client that grouped differently, or listed
a row its side would be refused, fails against the server's own table.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.modules.hassault import weapons
from backend.modules.hassault.modes import defuse

OUT = Path("packages/core/src/modules/hassault/__tests__/economy-vectors.json")

#: The menu's groups, in order, with the labels both clients show.
GROUPS = (
    ("secondary", "PISTOLS"),
    ("primary", "PRIMARY"),
    ("gear", "GEAR"),
    ("grenade", "GRENADES"),
)


def menu_for(side: str) -> list[dict[str, Any]]:
    out = []
    for category, label in GROUPS:
        rows = [
            index
            for index, item in enumerate(defuse.CATALOG)
            if item.category == category and item.side in ("any", side)
        ]
        if rows:
            out.append({"category": category, "label": label, "rows": rows})
    return out


def payload() -> dict[str, Any]:
    return {
        "catalog": [item.to_dict() for item in defuse.CATALOG],
        "buyTime": defuse.BUY_TIME,
        "buyZoneRadius": defuse.BUY_ZONE_RADIUS,
        "killRewards": {w.id: w.kill_reward for w in weapons.WEAPONS},
        "menus": {side: menu_for(side) for side in weapons.SIDES},
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(payload(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
