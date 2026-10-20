"""Weapon cases: what is in each, the odds, and the roll.

Content, like `skins.py`: served by the node and the game server alike
(`GET /api/hassault/cases`), so the odds a player reads before opening are the
odds the server rolls with. The *opening* is the game server's alone
(`games_server/hassault_wallet.open_case`): a case and a key are consumed and a
skin is made in one transaction, rolled with the server's RNG.

**Odds are per rarity, then uniform inside it.** `CASE_WEIGHTS` is the weight
of each rarity; a case's odds are those weights renormalised over the rarities
it actually contains, so a case without a Classified skin does not quietly keep
a Classified share that pays out nothing. Every case also holds the Special
items (the knives), at the rare-special weight, as CS cases do.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass
from typing import Any

from backend.modules.hassault.skins import (
    RARITY_ORDER,
    SKIN_CATALOG,
    Rarity,
    SkinDefinition,
    SkinInstance,
)

#: The weight of each rarity in a case, before renormalising over what it holds.
CASE_WEIGHTS: dict[Rarity, float] = {
    Rarity.CONSUMER: 45.0,
    Rarity.INDUSTRIAL: 25.0,
    Rarity.MIL_SPEC: 20.0,
    Rarity.RESTRICTED: 7.0,
    Rarity.CLASSIFIED: 2.2,
    Rarity.COVERT: 0.6,
    Rarity.SPECIAL: 0.2,
}

#: The chance an opened skin carries a StatTrak counter.
STATTRAK_CHANCE = 0.10

#: Float range per rarity, `(low, high)`: rarer finishes come out cleaner.
_WEAR: dict[Rarity, tuple[float, float]] = {
    Rarity.SPECIAL: (0.005, 0.08),
    Rarity.COVERT: (0.01, 0.15),
    Rarity.CLASSIFIED: (0.03, 0.25),
    Rarity.RESTRICTED: (0.06, 0.38),
    Rarity.MIL_SPEC: (0.10, 0.55),
    Rarity.INDUSTRIAL: (0.20, 0.85),
    Rarity.CONSUMER: (0.20, 0.85),
}


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    name: str
    #: The weapons whose non-Special skins are in it. Specials are in every case.
    weapons: tuple[str, ...]

    def contents(self) -> list[SkinDefinition]:
        return [
            s
            for s in SKIN_CATALOG
            if s.rarity == Rarity.SPECIAL or s.weapon_id in self.weapons
        ]

    def odds(self) -> dict[Rarity, float]:
        """Rarity → probability, over the rarities this case holds."""
        held = {s.rarity for s in self.contents()}
        total = sum(w for r, w in CASE_WEIGHTS.items() if r in held)
        return {r: CASE_WEIGHTS[r] / total for r in RARITY_ORDER if r in held}

    def to_dict(self) -> dict[str, Any]:
        contents = self.contents()
        return {
            "id": self.id,
            "name": self.name,
            "odds": [
                {"rarity": r.value, "chance": round(p, 6)}
                for r, p in self.odds().items()
            ],
            "contents": [s.to_dict() for s in contents],
        }


CASES: tuple[Case, ...] = (
    Case("field_case", "Field Case", ("assault", "pistol")),
    Case("breach_case", "Breach Case", ("shotgun", "sniper")),
)
CASE_BY_ID = {c.id: c for c in CASES}

#: The one kind of key: it opens any case.
KEY_ID = "case_key"


def roll(case: Case, rng: random.Random) -> SkinInstance:
    """One skin out of `case`: a rarity by the case's odds, a skin uniformly
    within it, a float in that rarity's range, a seed, maybe StatTrak."""
    odds = case.odds()
    rarities = list(odds)
    rarity = rng.choices(rarities, weights=[odds[r] for r in rarities], k=1)[0]
    pool = [s for s in case.contents() if s.rarity == rarity]
    skin = rng.choice(pool)
    low, high = _WEAR[rarity]
    wear = round(low + (high - low) * rng.betavariate(2.0, 3.0), 5)
    return SkinInstance(
        instance_id=str(uuid.uuid4()),
        skin_id=skin.id,
        float_value=max(0.00001, min(0.99999, wear)),
        pattern_seed=rng.randint(1, 1000),
        acquired_at=time.time(),
        stat_tracker_kills=0 if rng.random() < STATTRAK_CHANCE else None,
    )
