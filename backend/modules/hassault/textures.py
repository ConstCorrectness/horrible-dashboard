"""The texture palette, which is ours.

A texture in the map format is a bare integer slot. AssaultCube resolves one
through a map's `.cfg` file, naming an image in its own package — content this
project cannot ship, for the same reason it ships no AssaultCube maps. So
nothing has ever resolved a slot here: both renderers tint a surface by hashing
its id through a golden-ratio hue step, which reads as architecture but leaves
the mapper picking numbers out of the air.

This is the smallest honest fix. The palette becomes a **named catalogue we
own** — a slot has a name, a group, a colour and a procedural pattern, and no
image file exists anywhere — served like `plane_order`, `zoomLevels` and the
weapon table already are, so neither client holds a second copy.

**An unknown slot keeps exactly the look it has today.** `color_for` is the
existing hue step, in Python, and every catalogued entry's colour was taken from
it rather than chosen. That is the property that makes this additive: a map using
a slot nobody has named still renders precisely as it did, so adding the palette
cannot regress a map, and the catalogue can grow one entry at a time.

The ids below are the ones the three bundled maps already use, plus the format's
own defaults. They are spread across the 0..255 space rather than packed at the
bottom, because the hue step is what makes adjacent entries look different and
`0,1,2,3` are four shades of the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The golden-ratio conjugate, to the digits `geometry.ts` uses. Both clients
#: compute this in f64 for a reason recorded there: at f32 with a truncated
#: constant the two renderers drift apart by id 255, which is exactly the size of
#: error that survives every screenshot anybody thinks to take.
_PHI = 0.618033988749895
_SAT = 0.22
_LIGHT = 0.55


def color_for(tex: int) -> str:
    """The colour a slot is already drawn with, as `#rrggbb`.

    A port of `tex_color(tex, shade=1.0)` from `geometry.ts` and `geometry.rs`.
    It is here so the catalogue's colours are *read off* the renderers rather
    than invented beside them — a palette whose entries disagreed with the
    fallback would make naming a slot change how it looks, which is the one thing
    this must not do.
    """
    hue = (tex * _PHI) % 1.0
    a = _SAT * min(_LIGHT, 1.0 - _LIGHT)

    def channel(n: float) -> int:
        k = (n + hue * 12.0) % 12.0
        value = _LIGHT - a * max(-1.0, min(k - 3.0, 9.0 - k, 1.0))
        return max(0, min(255, round(value * 255)))

    return f"#{channel(0.0):02x}{channel(8.0):02x}{channel(4.0):02x}"


#: Every pattern a slot may name. Procedural, drawn by the client at startup —
#: there is no image file in this repo and there is not going to be one.
PATTERNS = ("flat", "grid", "brick", "plate", "concrete", "grate", "panel")


@dataclass(frozen=True, slots=True)
class Texture:
    id: int
    name: str
    group: str
    pattern: str
    roughness: float = 0.8

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "group": self.group,
            "color": color_for(self.id),
            "pattern": self.pattern,
            "roughness": self.roughness,
        }


PALETTE: tuple[Texture, ...] = (
    # The format's own defaults, so a cube that was never painted still has a
    # name in the picker instead of a blank.
    Texture(0, "Sky", "system", "flat", 1.0),
    Texture(1, "Liquid", "system", "flat", 0.2),
    Texture(2, "Default Wall", "system", "concrete"),
    Texture(3, "Default Floor", "system", "plate"),
    Texture(4, "Default Ceiling", "system", "panel"),
    # Bedrock: what every cell starts as before a brush touches it.
    Texture(6, "Rock", "structure", "concrete", 0.95),
    # The slots the bundled maps are painted with.
    Texture(12, "Concrete Wall", "structure", "concrete"),
    Texture(21, "Steel Deck", "floor", "plate", 0.6),
    Texture(33, "Ceiling Panel", "ceiling", "panel", 0.7),
    Texture(47, "Brick", "structure", "brick", 0.85),
    Texture(58, "Grated Floor", "floor", "grate", 0.5),
    Texture(76, "Painted Block", "structure", "grid", 0.75),
    Texture(89, "Rusted Plate", "structure", "plate", 0.9),
    # Room to build with. Chosen for hue separation, not for the numbers.
    Texture(104, "Tile", "floor", "grid", 0.55),
    Texture(131, "Bulkhead", "structure", "panel", 0.7),
    Texture(150, "Catwalk", "floor", "grate", 0.5),
    Texture(178, "Cinder Block", "structure", "brick", 0.9),
    Texture(199, "Glass Panel", "trim", "flat", 0.15),
    Texture(214, "Hazard Stripe", "trim", "grid", 0.6),
    Texture(233, "Vent", "ceiling", "grate", 0.6),
)

_BY_ID = {t.id: t for t in PALETTE}


def catalog() -> list[dict[str, Any]]:
    """The whole palette, as the route serves it."""
    return [t.to_dict() for t in PALETTE]


def get(tex: int) -> Texture | None:
    return _BY_ID.get(tex)


def describe(tex: int) -> dict[str, Any]:
    """One slot, catalogued or not.

    An uncatalogued slot is described rather than refused: it is a perfectly
    valid texture id that simply has no name yet, and a picker that showed
    nothing for it would be lying about a map that renders fine.
    """
    known = _BY_ID.get(tex)
    if known is not None:
        return known.to_dict()
    return {
        "id": tex,
        "name": f"Slot {tex}",
        "group": "unnamed",
        "color": color_for(tex),
        "pattern": "flat",
        "roughness": 0.8,
    }
