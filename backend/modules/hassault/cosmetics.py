"""What a player looks like to everyone else: their agent and their weapon skins.

A player's **cosmetics bundle** is one document per side:

    {"t":  {"agent": {"id", "armor", "trim"},
            "weapons": {weapon id: {skinId, name, baseColor, accentColor,
                                    patternType, floatValue, seed, statTrak}}},
     "ct": {...}}

Both sides travel together, so a team swap needs nothing resent; a client draws
the half for the team a player is on. Colours are resolved here and travel as
hex, so a client draws a skin from an installed pack it has never heard of.

**Where a bundle comes from decides whether it is believed.** The game server
builds it from its own tables (`games_server/hassault_account.cosmetics`) and
never from anything a client sends. A node-hosted room takes its own player's
from the node and a guest's from the guest's node, run through `clean_bundle`
first: a friend's machine can claim any colours, as it can claim any name, and
both are display only.

Bundles go out in the welcome, in `joined`, and as a `cosmetics` event when one
changes. **Never in a snapshot**: they are a kilobyte that changes once a match,
and snapshots go to everyone twenty times a second (`test_hassault_cosmetics`).

`palette` is the reference for how a skin's two colours land on a weapon made of
boxes. `viewmodel.ts` `paletteFor` and `viewmodel.rs` `palette_for` are its
ports, pinned by `cosmetics-vectors.json`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from backend.modules.hassault import weapons


@dataclass(frozen=True, slots=True)
class Agent:
    """An operator's colours. Strictly aesthetic: no hitbox changes with it."""

    id: str
    name: str
    #: `arc` agents play T, `halon` agents CT, `neutral` either.
    faction: str
    body: str
    armor: str
    trim: str
    visor: str
    skin_tone: str
    boot: str
    roughness: float
    metalness: float

    def allowed_on(self, side: str) -> bool:
        return self.faction == "neutral" or self.faction == _FACTION_SIDE[side]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "faction": self.faction,
            "sides": [s for s in weapons.SIDES if self.allowed_on(s)],
            "bodyColor": self.body,
            "armorColor": self.armor,
            "trimColor": self.trim,
            "visorColor": self.visor,
            "skinToneColor": self.skin_tone,
            "bootColor": self.boot,
            "roughness": self.roughness,
            "metalness": self.metalness,
        }


_FACTION_SIDE = {"t": "arc", "ct": "halon"}

#: Moved here from the browser's `models/skins.ts`, which had the only copy.
AGENTS: list[Agent] = [
    Agent(
        "arc_default", "ARC Scavenger", "arc",
        "#d9a441", "#4a3b2c", "#f2e2c4", "#222222", "#c89d7c", "#2b231d", 0.8, 0.15,
    ),
    Agent(
        "halon_default", "HALON Enforcer", "halon",
        "#4c8fd4", "#1e293b", "#94a3b8", "#38bdf8", "#dfb190", "#0f172a", 0.6, 0.35,
    ),
    Agent(
        "scavenger_prime", "Scavenger Prime", "arc",
        "#b45309", "#1c1917", "#f59e0b", "#fbbf24", "#a16207", "#0c0a09", 0.75, 0.4,
    ),
    Agent(
        "hazmat_warden", "Hazmat Warden", "halon",
        "#eab308", "#18181b", "#ef4444", "#22c55e", "#ca8a04", "#1c1917", 0.85, 0.1,
    ),
    Agent(
        "cyber_ghost", "Ghost Recon", "neutral",
        "#18181b", "#27272a", "#a855f7", "#c084fc", "#d4d4d8", "#09090b", 0.5, 0.6,
    ),
    Agent(
        "arctic_specops", "Arctic SpecOps", "neutral",
        "#e2e8f0", "#64748b", "#38bdf8", "#0ea5e9", "#f1f5f9", "#334155", 0.65, 0.25,
    ),
]  # fmt: skip

AGENT_BY_ID = {a.id: a for a in AGENTS}
DEFAULT_AGENTS: dict[str, str] = {"t": "arc_default", "ct": "halon_default"}

#: The setting that keeps a player's agent per side, `{t: id, ct: id}`.
AGENTS_KEY = "hassault.agents"


def agents_for(choice: Any = None) -> dict[str, str]:
    """An agent id per side, validated: unknown, or the wrong faction for that
    side, is the side's default. Accepts the setting's JSON string."""
    if isinstance(choice, str):
        try:
            choice = json.loads(choice) if choice.strip() else None
        except ValueError:
            choice = None
    picked = choice if isinstance(choice, dict) else {}
    out: dict[str, str] = {}
    for side in weapons.SIDES:
        agent = AGENT_BY_ID.get(str(picked.get(side) or ""))
        out[side] = (
            agent.id
            if agent is not None and agent.allowed_on(side)
            else DEFAULT_AGENTS[side]
        )
    return out


def _agent_wire(agent_id: str, side: str) -> dict[str, str]:
    agent = AGENT_BY_ID.get(agent_id)
    if agent is None or not agent.allowed_on(side):
        agent = AGENT_BY_ID[DEFAULT_AGENTS[side]]
    return {"id": agent.id, "armor": agent.armor, "trim": agent.trim}


# ---- weapon skins ---------------------------------------------------------------

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
PATTERNS = ("solid", "camo", "anodized", "custom_art", "patina", "fade")


def weapon_entry(item: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """One inventory item (`SkinInstance.to_dict` with its definition) as a
    bundle entry, keyed by the weapon it is for. `None` without a definition:
    a skin with no colours is not one to draw."""
    definition = item.get("definition")
    if not isinstance(definition, dict):
        return None
    weapon_id = str(definition.get("weaponId") or "")
    if weapon_id not in weapons.WEAPON_BY_ID:
        return None
    return weapon_id, {
        "skinId": str(definition.get("id") or item.get("skinId") or ""),
        "name": str(definition.get("name") or ""),
        "baseColor": str(definition.get("baseColor") or ""),
        "accentColor": str(definition.get("accentColor") or ""),
        "patternType": str(definition.get("patternType") or "solid"),
        "floatValue": round(float(item.get("floatValue") or 0.0), 5),
        "seed": int(item.get("patternSeed") or 0),
        "statTrak": item.get("statTrackerKills"),
    }


def bundle(
    agents: dict[str, str] | None = None,
    equips: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """A bundle from agent ids per side and the items equipped per side."""
    chosen = agents_for(agents)
    out: dict[str, Any] = {}
    for side in weapons.SIDES:
        entries: dict[str, Any] = {}
        for item in (equips or {}).get(side, []):
            entry = weapon_entry(item)
            if entry is not None:
                entries[entry[0]] = entry[1]
        out[side] = {"agent": _agent_wire(chosen[side], side), "weapons": entries}
    return out


def default_bundle() -> dict[str, Any]:
    return bundle()


#: More weapons than this in one side of a bundle is not a loadout, it is padding.
_MAX_ENTRIES = len(weapons.WEAPONS)


def clean_bundle(raw: Any) -> dict[str, Any]:
    """A bundle from somewhere not trusted to have built it: a guest's node.

    Agents are re-resolved from this catalogue by id, so their colours are
    always ours; weapon entries keep their colours (a pack skin is not in our
    catalogue) but only as valid hex, with a known pattern, a clamped float, and
    a weapon that exists. Anything else is dropped, never repaired.
    """
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for side in weapons.SIDES:
        half = source.get(side) if isinstance(source.get(side), dict) else {}
        agent = half.get("agent") if isinstance(half.get("agent"), dict) else {}
        entries: dict[str, Any] = {}
        listed = half.get("weapons") if isinstance(half.get("weapons"), dict) else {}
        for weapon_id, entry in list(listed.items())[:_MAX_ENTRIES]:
            if weapon_id not in weapons.WEAPON_BY_ID or not isinstance(entry, dict):
                continue
            base = str(entry.get("baseColor") or "")
            accent = str(entry.get("accentColor") or "")
            pattern = str(entry.get("patternType") or "solid")
            if not (_HEX.match(base) and _HEX.match(accent)) or pattern not in PATTERNS:
                continue
            try:
                wear = min(1.0, max(0.0, float(entry.get("floatValue") or 0.0)))
                seed = int(entry.get("seed") or 0)
            except (TypeError, ValueError):
                continue
            stattrak = entry.get("statTrak")
            entries[weapon_id] = {
                "skinId": str(entry.get("skinId") or "")[:64],
                "name": str(entry.get("name") or "")[:48],
                "baseColor": base,
                "accentColor": accent,
                "patternType": pattern,
                "floatValue": round(wear, 5),
                "seed": seed,
                "statTrak": stattrak if isinstance(stattrak, int) else None,
            }
        out[side] = {
            "agent": _agent_wire(str(agent.get("id") or ""), side),
            "weapons": entries,
        }
    return out


# ---- the palette ----------------------------------------------------------------

#: The unskinned weapon, as `viewmodel.ts` `DEFAULT_PALETTE` has it.
DEFAULT_PALETTE = {
    "body": "#3a4048",
    "dark": "#1c2026",
    "grip": "#4a3f33",
    "accent": "#8a929c",
}
#: Grime: what a Battle-Scarred gun is mixed toward.
WEAR_COLOR = "#5a554e"
#: The darkest a skinned surface may be; see `viewmodel.ts` `MIN_LUMA`.
MIN_LUMA = 0.14

_Rgb = tuple[float, float, float]
_BLACK: _Rgb = (0.0, 0.0, 0.0)
_WHITE: _Rgb = (1.0, 1.0, 1.0)


def _rgb(value: str, fallback: str) -> _Rgb:
    text = value.strip() if isinstance(value, str) else ""
    if not text.startswith("#"):
        text = "#" + text
    if not _HEX.match(text):
        text = fallback
    return tuple(int(text[i : i + 2], 16) / 255.0 for i in (1, 3, 5))  # type: ignore[return-value]


def _mix(a: _Rgb, b: _Rgb, t: float) -> _Rgb:
    f = min(1.0, max(0.0, t))
    return (
        a[0] + (b[0] - a[0]) * f,
        a[1] + (b[1] - a[1]) * f,
        a[2] + (b[2] - a[2]) * f,
    )


def _lift(c: _Rgb) -> _Rgb:
    luma = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
    if luma >= MIN_LUMA:
        return c
    return _mix(c, _WHITE, (MIN_LUMA - luma) / max(1e-3, 1.0 - luma))


def palette(skin: dict[str, Any] | None) -> dict[str, _Rgb]:
    """Where a skin's colours go on a weapon, wear applied, as 0..1 RGB.

    `None` is the unskinned palette, unlifted: a player with no skin sees exactly
    what they always did. `patternType` decides how two colours spread across
    the parts, because without textures it cannot be a pattern.
    """
    if not skin:
        return {k: _rgb(v, v) for k, v in DEFAULT_PALETTE.items()}
    base = _rgb(str(skin.get("baseColor") or ""), DEFAULT_PALETTE["body"])
    accent = _rgb(str(skin.get("accentColor") or ""), DEFAULT_PALETTE["accent"])
    pattern = skin.get("patternType")
    if pattern == "fade":
        plain = (base, _mix(base, accent, 0.5), accent, _mix(accent, _WHITE, 0.25))
    elif pattern == "camo":
        plain = (
            base,
            _mix(base, _BLACK, 0.55),
            _mix(base, accent, 0.65),
            _mix(base, _BLACK, 0.3),
        )
    elif pattern == "anodized":
        plain = (base, _mix(base, _BLACK, 0.4), _mix(base, _BLACK, 0.65), accent)
    elif pattern == "patina":
        plain = (
            _mix(base, accent, 0.35),
            _mix(accent, _BLACK, 0.35),
            _mix(base, _BLACK, 0.7),
            _mix(base, _WHITE, 0.2),
        )
    elif pattern == "custom_art":
        plain = (base, accent, _mix(base, _BLACK, 0.72), _mix(accent, _WHITE, 0.3))
    else:
        plain = (base, _mix(base, _BLACK, 0.5), _mix(accent, _BLACK, 0.5), accent)
    wear = min(1.0, max(0.0, float(skin.get("floatValue") or 0.0))) * 0.55
    grime = _rgb(WEAR_COLOR, WEAR_COLOR)
    return {
        "body": _lift(_mix(plain[0], grime, wear)),
        "dark": _lift(_mix(plain[1], grime, wear * 0.7)),
        "grip": _lift(_mix(plain[2], grime, wear)),
        "accent": _lift(_mix(plain[3], grime, wear)),
    }
