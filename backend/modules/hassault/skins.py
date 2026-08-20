"""Counter-Strike-inspired Skin Economy, Rarity Tiers, Float Wear, and Level-Up Drop System.

Implements the CS / CS2 skin architecture:
1. Rarity Tiers: Consumer (White), Industrial (Light Blue), Mil-Spec (Blue),
   Restricted (Purple), Classified (Pink), Covert (Red), Exceedingly Rare (Gold ⭐).
2. Float Wear Values: Factory New (0.00-0.07), Minimal Wear (0.07-0.15),
   Field-Tested (0.15-0.38), Well-Worn (0.38-0.45), Battle-Scarred (0.45-1.00).
3. Pattern Seeds (1-1000) for texture shifting (e.g. Case Hardened / Fade).
4. Level-Up Drops & Care Packages: Weighted RNG drops awarded upon leveling up.
5. Trade-Up Contracts: 10 skins of Tier N -> 1 skin of Tier N+1.
6. Inventory & Loadout Management.
"""

from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Rarity(str, Enum):
    CONSUMER = "consumer"  # Common (Grey/White)
    INDUSTRIAL = "industrial"  # Uncommon (Light Blue)
    MIL_SPEC = "mil_spec"  # Rare (Dark Blue)
    RESTRICTED = "restricted"  # Mythical (Purple)
    CLASSIFIED = "classified"  # Legendary (Pink)
    COVERT = "covert"  # Ancient (Red)
    SPECIAL = "special"  # Exceedingly Rare ⭐ (Gold)


RARITY_ORDER = [
    Rarity.CONSUMER,
    Rarity.INDUSTRIAL,
    Rarity.MIL_SPEC,
    Rarity.RESTRICTED,
    Rarity.CLASSIFIED,
    Rarity.COVERT,
    Rarity.SPECIAL,
]

RARITY_COLORS = {
    Rarity.CONSUMER: "#b0c3d9",
    Rarity.INDUSTRIAL: "#5e98d9",
    Rarity.MIL_SPEC: "#4b69ff",
    Rarity.RESTRICTED: "#8847ff",
    Rarity.CLASSIFIED: "#d32ce6",
    Rarity.COVERT: "#eb4b4b",
    Rarity.SPECIAL: "#ffd700",
}

# Drop weight probabilities (approximating CS case / level drop distributions)
DROP_WEIGHTS = {
    Rarity.CONSUMER: 50.0,
    Rarity.INDUSTRIAL: 30.0,
    Rarity.MIL_SPEC: 15.0,
    Rarity.RESTRICTED: 3.8,
    Rarity.CLASSIFIED: 0.9,
    Rarity.COVERT: 0.25,
    Rarity.SPECIAL: 0.05,
}


def float_to_wear_name(float_val: float) -> str:
    if float_val < 0.07:
        return "Factory New"
    if float_val < 0.15:
        return "Minimal Wear"
    if float_val < 0.38:
        return "Field-Tested"
    if float_val < 0.45:
        return "Well-Worn"
    return "Battle-Scarred"


@dataclass(frozen=True, slots=True)
class SkinDefinition:
    """Base template for a weapon skin."""

    id: str
    name: str
    weapon_id: str  # "knife" | "pistol" | "assault" | "shotgun" | "sniper"
    rarity: Rarity
    collection: str
    base_color: str
    accent_color: str
    pattern_type: (
        str  # "solid" | "camo" | "anodized" | "custom_art" | "patina" | "fade"
    )
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "weaponId": self.weapon_id,
            "rarity": self.rarity.value,
            "rarityColor": RARITY_COLORS[self.rarity],
            "collection": self.collection,
            "baseColor": self.base_color,
            "accentColor": self.accent_color,
            "patternType": self.pattern_type,
            "description": self.description,
        }


@dataclass
class SkinInstance:
    """An individual skin item in a player's inventory."""

    instance_id: str
    skin_id: str
    float_value: float  # 0.000 to 1.000
    pattern_seed: int  # 1 to 1000
    acquired_at: float
    is_equipped: bool = False
    is_tradable: bool = True
    stat_tracker_kills: int | None = None  # Counter-Strike StatTrak equivalent

    @property
    def wear_name(self) -> str:
        return float_to_wear_name(self.float_value)

    def to_dict(self, definition: SkinDefinition | None = None) -> dict[str, Any]:
        base = {
            "instanceId": self.instance_id,
            "skinId": self.skin_id,
            "floatValue": round(self.float_value, 5),
            "wearName": self.wear_name,
            "patternSeed": self.pattern_seed,
            "acquiredAt": self.acquired_at,
            "isEquipped": self.is_equipped,
            "isTradable": self.is_tradable,
            "statTrackerKills": self.stat_tracker_kills,
        }
        if definition:
            base["definition"] = definition.to_dict()
        return base


# Master Skin Catalog (Collections spanning weapons and rarities)
SKIN_CATALOG: list[SkinDefinition] = [
    # Knives & Exceedingly Rare (Special ⭐)
    SkinDefinition(
        id="knife_fade",
        name="Fade",
        weapon_id="knife",
        rarity=Rarity.SPECIAL,
        collection="Chroma Collection",
        base_color="#38bdf8",
        accent_color="#f43f5e",
        pattern_type="fade",
        description="Airbrushed with transparent paints that fade together over a chrome base coat.",
    ),
    SkinDefinition(
        id="knife_damascus",
        name="Damascus Steel",
        weapon_id="knife",
        rarity=Rarity.SPECIAL,
        collection="Cobblestone Collection",
        base_color="#e2e8f0",
        accent_color="#64748b",
        pattern_type="patina",
        description="Forged from two different types of carbon steel for a marbled look.",
    ),
    SkinDefinition(
        id="knife_crimson",
        name="Crimson Web",
        weapon_id="knife",
        rarity=Rarity.SPECIAL,
        collection="Inferno Collection",
        base_color="#dc2626",
        accent_color="#18181b",
        pattern_type="custom_art",
        description="Painted using a red base coat with black spiderweb patterns.",
    ),
    # Covert (Red - Ancient)
    SkinDefinition(
        id="sniper_dragonfire",
        name="Dragonfire Lore",
        weapon_id="sniper",
        rarity=Rarity.COVERT,
        collection="Cobblestone Collection",
        base_color="#eab308",
        accent_color="#dc2626",
        pattern_type="custom_art",
        description="Adorned with an intricate golden wyvern spitting celestial flames.",
    ),
    SkinDefinition(
        id="assault_asiimov",
        name="Asiimov",
        weapon_id="assault",
        rarity=Rarity.COVERT,
        collection="Phoenix Collection",
        base_color="#f8fafc",
        accent_color="#ea580c",
        pattern_type="custom_art",
        description="Custom painted with a futuristic sci-fi aesthetic in high-contrast orange and white.",
    ),
    SkinDefinition(
        id="assault_printstream",
        name="Printstream",
        weapon_id="assault",
        rarity=Rarity.COVERT,
        collection="Fracture Collection",
        base_color="#ffffff",
        accent_color="#1e1e2e",
        pattern_type="anodized",
        description="Monochrome finish with pearlescent accents and digital optical tracking marks.",
    ),
    # Classified (Pink - Legendary)
    SkinDefinition(
        id="sniper_hyperbeast",
        name="Hyper Beast",
        weapon_id="sniper",
        rarity=Rarity.CLASSIFIED,
        collection="Falchion Collection",
        base_color="#06b6d4",
        accent_color="#f43f5e",
        pattern_type="custom_art",
        description="Custom painted with a ferocious psychedelic beast in saturated neon hues.",
    ),
    SkinDefinition(
        id="shotgun_vulcan",
        name="Vulcan",
        weapon_id="shotgun",
        rarity=Rarity.CLASSIFIED,
        collection="Huntsman Collection",
        base_color="#0284c7",
        accent_color="#0f172a",
        pattern_type="custom_art",
        description="Sporty high-tech finish with azure racing stripes and matte carbon.",
    ),
    SkinDefinition(
        id="pistol_kill_confirmed",
        name="Kill Confirmed",
        weapon_id="pistol",
        rarity=Rarity.CLASSIFIED,
        collection="Shadow Collection",
        base_color="#b91c1c",
        accent_color="#fbbf24",
        pattern_type="custom_art",
        description="Painted with a fiery skull shattered by an accelerating projectile.",
    ),
    # Restricted (Purple - Mythical)
    SkinDefinition(
        id="assault_redline",
        name="Redline",
        weapon_id="assault",
        rarity=Rarity.RESTRICTED,
        collection="Phoenix Collection",
        base_color="#18181b",
        accent_color="#ef4444",
        pattern_type="custom_art",
        description="Carbon fiber composite weave with clean crimson pinstripes.",
    ),
    SkinDefinition(
        id="shotgun_heat",
        name="Heat",
        weapon_id="shotgun",
        rarity=Rarity.RESTRICTED,
        collection="Inferno Collection",
        base_color="#27272a",
        accent_color="#f97316",
        pattern_type="anodized",
        description="Finished with an intense thermal glowing effect along the barrel edges.",
    ),
    SkinDefinition(
        id="pistol_case_hardened",
        name="Case Hardened",
        weapon_id="pistol",
        rarity=Rarity.RESTRICTED,
        collection="Arms Deal Collection",
        base_color="#38bdf8",
        accent_color="#d97706",
        pattern_type="patina",
        description="Color case-hardened through wood charcoal heating. Pattern seed determines blue percentage.",
    ),
    # Mil-Spec (Blue - Rare)
    SkinDefinition(
        id="assault_slate",
        name="Slate",
        weapon_id="assault",
        rarity=Rarity.MIL_SPEC,
        collection="Snakebite Collection",
        base_color="#09090b",
        accent_color="#27272a",
        pattern_type="solid",
        description="Minimalist all-black matte stealth finish.",
    ),
    SkinDefinition(
        id="sniper_cortex",
        name="Cortex",
        weapon_id="sniper",
        rarity=Rarity.MIL_SPEC,
        collection="Clutch Collection",
        base_color="#f472b6",
        accent_color="#1e293b",
        pattern_type="custom_art",
        description="Graphic street art with exposed neural synapses flowing into the grip.",
    ),
    SkinDefinition(
        id="pistol_glock_water",
        name="Water Elemental",
        weapon_id="pistol",
        rarity=Rarity.MIL_SPEC,
        collection="Breakout Collection",
        base_color="#0284c7",
        accent_color="#ef4444",
        pattern_type="custom_art",
        description="Painted with a flowing water elemental sprite over crimson metal.",
    ),
    # Industrial (Light Blue - Uncommon)
    SkinDefinition(
        id="shotgun_sand_dune",
        name="Sand Dune",
        weapon_id="shotgun",
        rarity=Rarity.INDUSTRIAL,
        collection="Dust II Collection",
        base_color="#d4b996",
        accent_color="#8c7853",
        pattern_type="camo",
        description="Spray-painted with desert tan camo tones.",
    ),
    SkinDefinition(
        id="assault_safari",
        name="Safari Mesh",
        weapon_id="assault",
        rarity=Rarity.INDUSTRIAL,
        collection="Mirage Collection",
        base_color="#847c64",
        accent_color="#3f3b2f",
        pattern_type="camo",
        description="Spray-painted using mesh wire fencing as a stencil.",
    ),
    # Consumer (White - Common)
    SkinDefinition(
        id="pistol_groundwater",
        name="Groundwater",
        weapon_id="pistol",
        rarity=Rarity.CONSUMER,
        collection="Lake Collection",
        base_color="#4b5563",
        accent_color="#374151",
        pattern_type="solid",
        description="Standard military field finish.",
    ),
]

SKIN_DICT = {s.id: s for s in SKIN_CATALOG}


class SkinInventoryManager:
    """Manages player inventories, random drops, trade-up contracts, and AtlasDB sync."""

    def __init__(self) -> None:
        #: account_id -> list[SkinInstance]. A **cache** now, not the store:
        #: `app.db` is where an inventory lives (see `_load` / `_save`). It was
        #: this dict alone, which meant every skin anybody earned was gone the
        #: next time the backend restarted — including the drop the post-match
        #: card had just congratulated them on.
        self._inventories: dict[str, list[SkinInstance]] = {}

    # -- persistence --------------------------------------------------------

    @staticmethod
    def _conn():
        import sqlite3

        from backend.modules.database.app_db import ensure_app_db_dir

        conn = sqlite3.connect(str(ensure_app_db_dir()))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hassault_skins (
                instance_id  TEXT PRIMARY KEY,
                account_id   TEXT NOT NULL,
                skin_id      TEXT NOT NULL,
                float_value  REAL NOT NULL,
                pattern_seed INTEGER NOT NULL,
                acquired_at  REAL NOT NULL,
                is_equipped  INTEGER NOT NULL DEFAULT 0,
                stat_kills   INTEGER
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hassault_skins_account "
            "ON hassault_skins(account_id)"
        )
        return conn

    def _load(self, account_id: str) -> list[SkinInstance] | None:
        """This account's inventory from the database, or `None` if it has never
        had one — which is what tells a new player from one who has traded
        everything away."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM hassault_skins WHERE account_id = ? ORDER BY acquired_at",
                (account_id,),
            ).fetchall()
        if not rows:
            return None
        return [
            SkinInstance(
                instance_id=str(r["instance_id"]),
                skin_id=str(r["skin_id"]),
                float_value=float(r["float_value"]),
                pattern_seed=int(r["pattern_seed"]),
                acquired_at=float(r["acquired_at"]),
                is_equipped=bool(r["is_equipped"]),
                stat_tracker_kills=(
                    None if r["stat_kills"] is None else int(r["stat_kills"])
                ),
            )
            for r in rows
        ]

    def _save(self, account_id: str) -> None:
        """Write this account's inventory back.

        A delete-and-reinsert of one account's rows rather than a diff: an
        inventory is at most a few dozen items, a trade-up burns ten of them in
        one operation, and a diff is where "the ten went and the one never
        arrived" comes from.
        """
        items = self._inventories.get(account_id, [])
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM hassault_skins WHERE account_id = ?", (account_id,)
            )
            conn.executemany(
                """
                INSERT INTO hassault_skins
                    (instance_id, account_id, skin_id, float_value, pattern_seed,
                     acquired_at, is_equipped, stat_kills)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.instance_id,
                        account_id,
                        item.skin_id,
                        item.float_value,
                        item.pattern_seed,
                        item.acquired_at,
                        1 if item.is_equipped else 0,
                        item.stat_tracker_kills,
                    )
                    for item in items
                ],
            )
            conn.commit()

    def has_local(self, account_id: str) -> bool:
        """Whether this node already holds this account's inventory.

        The question a caller needs before reaching for Atlas: the cluster is a
        **sync between machines**, not the read path. Asking it on every
        inventory poll is a round trip per poll for a document that only changes
        when this node changes it.
        """
        if account_id in self._inventories:
            return True
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM hassault_skins WHERE account_id = ? LIMIT 1",
                (account_id,),
            ).fetchone()
        return row is not None

    def find_instance(self, account_id: str, instance_id: str) -> SkinInstance | None:
        """One item by id, for a card that knows only which drop it was given."""
        return next(
            (i for i in self.get_inventory(account_id) if i.instance_id == instance_id),
            None,
        )

    async def sync_to_atlas(self, account_id: str) -> None:
        """Asynchronously sync inventory to Atlas MongoDB collection if configured."""
        try:
            from backend import atlas

            col = atlas.collection("player_skins")
            if col is not None:
                inv = self.get_inventory(account_id)
                data = [item.to_dict() for item in inv]
                await col.update_one(
                    {"account_id": account_id},
                    {
                        "$set": {
                            "account_id": account_id,
                            "inventory": data,
                            "updated_at": time.time(),
                        }
                    },
                    upsert=True,
                )
        except Exception:
            pass

    async def load_from_atlas(self, account_id: str) -> None:
        """Load inventory from Atlas MongoDB if present."""
        try:
            from backend import atlas

            col = atlas.collection("player_skins")
            if col is not None:
                doc = await col.find_one({"account_id": account_id})
                if doc and "inventory" in doc:
                    items: list[SkinInstance] = []
                    for raw in doc["inventory"]:
                        items.append(
                            SkinInstance(
                                instance_id=raw["instanceId"],
                                skin_id=raw["skinId"],
                                float_value=raw["floatValue"],
                                pattern_seed=raw["patternSeed"],
                                acquired_at=raw["acquiredAt"],
                                is_equipped=raw.get("isEquipped", False),
                                is_tradable=raw.get("isTradable", True),
                                stat_tracker_kills=raw.get("statTrackerKills"),
                            )
                        )
                    if items:
                        self._inventories[account_id] = items
                        # Adopted locally, so this is the *last* time Atlas is
                        # asked for this account on this machine. The cluster is
                        # a sync between machines, not the read path.
                        self._save(account_id)
        except Exception:
            pass

    def get_inventory(self, account_id: str) -> list[SkinInstance]:
        if account_id not in self._inventories:
            stored = self._load(account_id)
            if stored is not None:
                self._inventories[account_id] = stored
                return stored
            # Never played before: seed the starter inventory, and **save it**,
            # so the two weapons somebody starts with keep their float values and
            # pattern seeds instead of being re-rolled on every restart.
            self._inventories[account_id] = [
                SkinInstance(
                    instance_id=str(uuid.uuid4()),
                    skin_id="assault_slate",
                    float_value=0.0345,
                    pattern_seed=142,
                    acquired_at=time.time(),
                    is_equipped=True,
                ),
                SkinInstance(
                    instance_id=str(uuid.uuid4()),
                    skin_id="pistol_glock_water",
                    float_value=0.1284,
                    pattern_seed=568,
                    acquired_at=time.time(),
                    is_equipped=True,
                ),
            ]
            self._save(account_id)
        return self._inventories[account_id]

    def equip_skin(self, account_id: str, instance_id: str) -> bool:
        inv = self.get_inventory(account_id)
        target = next((item for item in inv if item.instance_id == instance_id), None)
        if not target:
            return False

        target_def = SKIN_DICT.get(target.skin_id)
        if not target_def:
            return False

        # Unequip other skins for the same weapon slot
        for item in inv:
            item_def = SKIN_DICT.get(item.skin_id)
            if item_def and item_def.weapon_id == target_def.weapon_id:
                item.is_equipped = False

        target.is_equipped = True
        self._save(account_id)
        return True

    def roll_drop(self, account_id: str) -> SkinInstance:
        """Roll a weighted rarity skin drop upon leveling up."""
        rarities = list(DROP_WEIGHTS.keys())
        weights = [DROP_WEIGHTS[r] for r in rarities]
        chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]

        matching_skins = [s for s in SKIN_CATALOG if s.rarity == chosen_rarity]
        if not matching_skins:
            matching_skins = SKIN_CATALOG

        chosen_skin = random.choice(matching_skins)
        # Beta distribution for float value: biases toward Field-Tested / Minimal Wear
        float_val = round(random.betavariate(2.0, 3.0), 5)
        float_val = max(0.00001, min(0.99999, float_val))
        seed = random.randint(1, 1000)

        # 10% chance for StatTrak kill counter
        stat_trak = 0 if random.random() < 0.10 else None

        instance = SkinInstance(
            instance_id=str(uuid.uuid4()),
            skin_id=chosen_skin.id,
            float_value=float_val,
            pattern_seed=seed,
            acquired_at=time.time(),
            stat_tracker_kills=stat_trak,
        )

        inv = self.get_inventory(account_id)
        inv.append(instance)
        self._save(account_id)
        return instance

    def trade_up_contract(
        self, account_id: str, instance_ids: list[str]
    ) -> SkinInstance | None:
        """Execute a CS-style 10-to-1 Trade-Up Contract.

        Takes 10 items of rarity tier N, burns them, and outputs 1 item of tier N+1
        with average float value computed from inputs.
        """
        if len(instance_ids) != 10:
            return None

        inv = self.get_inventory(account_id)
        selected_items = [item for item in inv if item.instance_id in set(instance_ids)]
        if len(selected_items) != 10:
            return None

        # Verify all items share the exact same rarity
        first_def = SKIN_DICT.get(selected_items[0].skin_id)
        if not first_def:
            return None

        current_rarity = first_def.rarity
        for item in selected_items:
            item_def = SKIN_DICT.get(item.skin_id)
            if not item_def or item_def.rarity != current_rarity:
                return None  # Mismatched rarity

        rarity_idx = RARITY_ORDER.index(current_rarity)
        if rarity_idx >= len(RARITY_ORDER) - 1:
            return None  # Already max rarity (Special)

        next_rarity = RARITY_ORDER[rarity_idx + 1]
        next_pool = [s for s in SKIN_CATALOG if s.rarity == next_rarity]
        if not next_pool:
            return None

        # CS float formula: average of input floats
        avg_float = sum(item.float_value for item in selected_items) / 10.0
        new_skin_def = random.choice(next_pool)
        new_seed = random.randint(1, 1000)

        # Remove the 10 burned items
        burned_ids = {item.instance_id for item in selected_items}
        self._inventories[account_id] = [
            item for item in inv if item.instance_id not in burned_ids
        ]

        result_instance = SkinInstance(
            instance_id=str(uuid.uuid4()),
            skin_id=new_skin_def.id,
            float_value=round(avg_float, 5),
            pattern_seed=new_seed,
            acquired_at=time.time(),
        )

        self._inventories[account_id].append(result_instance)
        # Ten burned and one made, written in **one** transaction by `_save`:
        # a contract that persisted the burn and not the reward would be the
        # worst possible thing to get half-right.
        self._save(account_id)
        return result_instance


skin_manager = SkinInventoryManager()
