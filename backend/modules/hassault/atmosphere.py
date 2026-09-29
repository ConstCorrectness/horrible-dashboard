"""How a map is lit and what surrounds it: one resolved block, served to both clients.

Until this existed the light rig lived twice, by hand: `HorribleAssaultPanel.tsx`
set a hemisphere, a sun and a fill, `lighting.wgsl.inc` held the same numbers as
WGSL constants, and nothing but a parity test kept them together. Every map was
lit the same way whatever it was meant to be — a desert at noon and an office at
night under one sun.

Now a map source may carry an `atmosphere` object, and whatever it leaves out
comes from the defaults below. The **resolved** block is what `GET /maps/{name}`
serves, so neither client carries a default of its own and a map looks the same
in the pane, the standalone web build and the native window. Quality levels may
draw it with less (no sky dome, fewer lights) but never with *different* numbers.

Colours are sRGB hex integers — the form three's `Color` takes and the form a
mapper picks — and each client decodes them to linear itself. Directions are
three-space (y up), pointing **toward** the light, and are normalised here so no
client has to.

The two defaults are today's rigs, exactly:

- **cube** maps are sealed Cube 1 boxes, lit by the rig the browser has always
  used, with a dark blue-black fog that is also the background.
- **gltf** maps are open to the sky, with the daylight background and the thin
  fog the pane's GLB path used to hard-code.
"""

from __future__ import annotations

import math
from typing import Any

from backend.modules.hassault.cgz import LIGHT, CgzError, CgzMap

#: The browser's cube-map rig, verbatim. `lighting.wgsl.inc` used to hold the
#: same numbers as constants; now it reads them from a uniform filled from this.
CUBE_DEFAULT: dict[str, Any] = {
    "skyZenith": 0x11161F,
    "skyHorizon": 0x11161F,
    "hemiSky": 0xBFD4FF,
    "hemiGround": 0x33302C,
    "hemiIntensity": 1.55,
    "sunColor": 0xFFF2DD,
    "sunIntensity": 1.75,
    # `sun.position` minus its target in the pane: (0.55, 0.82, 0.36) of reach.
    "sunDir": [0.55, 0.82, 0.36],
    "fillColor": 0x9FB6FF,
    "fillIntensity": 0.45,
    "fillDir": [-0.5, 0.35, -0.7],
    "fogColor": 0x11161F,
    "fogDensity": 0.0055,
    "exposure": 1.15,
    # A sealed map has no visible sky, so there is no disc to draw.
    "sunDisc": False,
}

#: The daylight the pane's GLB path hard-coded (`0x76a7eb` background,
#: `FogExp2(0x9cbde8, 0.001)`), with the same light rig as a cube map.
GLTF_DEFAULT: dict[str, Any] = {
    **CUBE_DEFAULT,
    "skyZenith": 0x3F74C8,
    "skyHorizon": 0x9CBDE8,
    "fogColor": 0x9CBDE8,
    "fogDensity": 0.001,
    "sunDisc": True,
}

_COLOR_KEYS = (
    "skyZenith",
    "skyHorizon",
    "hemiSky",
    "hemiGround",
    "sunColor",
    "fillColor",
    "fogColor",
)
_DIR_KEYS = ("sunDir", "fillDir")
#: (minimum, maximum) for each scalar. Bounded so a typo cannot blind a map:
#: an exposure of 40 is a white screen, a fog density of 1 is a black one.
_SCALARS: dict[str, tuple[float, float]] = {
    "hemiIntensity": (0.0, 6.0),
    "sunIntensity": (0.0, 8.0),
    "fillIntensity": (0.0, 4.0),
    "fogDensity": (0.0, 0.05),
    "exposure": (0.25, 4.0),
}


def _fail(message: str) -> CgzError:
    return CgzError(f"bad map source: atmosphere.{message}")


def _color(value: Any, key: str) -> int:
    """A hex int, or a `"#rrggbb"` string, which is what a mapper will type."""
    if isinstance(value, str):
        text = value.strip().removeprefix("#").removeprefix("0x")
        try:
            value = int(text, 16)
        except ValueError:
            raise _fail(f"{key} is not a colour: {value!r}") from None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= 0xFFFFFF
    ):
        raise _fail(f"{key} must be 0x000000..0xffffff, got {value!r}")
    return value


def _direction(value: Any, key: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        )
    ):
        raise _fail(f"{key} must be [x, y, z], got {value!r}")
    length = math.sqrt(sum(float(v) * float(v) for v in value))
    if length < 1e-6:
        raise _fail(f"{key} has no length")
    return [round(float(v) / length, 6) for v in value]


def validate(spec: Any) -> dict[str, Any]:
    """Check a source's `atmosphere` object, keeping only the keys it sets.

    Returned *unresolved*: which defaults fill the rest depends on the level
    format, which is decided where the map is served rather than where it is
    built.
    """
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise _fail(f"must be an object, got {spec!r}")
    unknown = set(spec) - set(CUBE_DEFAULT)
    if unknown:
        raise _fail(f"unknown keys {sorted(unknown)}")
    out: dict[str, Any] = {}
    for key, value in spec.items():
        if key in _COLOR_KEYS:
            out[key] = _color(value, key)
        elif key in _DIR_KEYS:
            out[key] = _direction(value, key)
        elif key in _SCALARS:
            lo, hi = _SCALARS[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise _fail(f"{key} must be a number, got {value!r}")
            if not lo <= float(value) <= hi:
                raise _fail(f"{key} must be {lo}..{hi}, got {value!r}")
            out[key] = float(value)
        elif key == "sunDisc":
            if not isinstance(value, bool):
                raise _fail(f"sunDisc must be true or false, got {value!r}")
            out[key] = value
    return out


def resolve(spec: dict[str, Any] | None, level_format: str) -> dict[str, Any]:
    """The full block a client draws: the format's defaults under the map's own."""
    base = GLTF_DEFAULT if level_format == "gltf" else CUBE_DEFAULT
    out = {**base, **validate(spec or {})}
    for key in _DIR_KEYS:
        out[key] = _direction(out[key], key)
    return out


# ---- point lights ------------------------------------------------------------------

#: A light's `intensity` is a multiplier: both clients give it
#: `intensity * radius / 2` candela, with three's inverse-square falloff windowed
#: to zero at the radius. Linear in the radius, so a wide lamp is not a white
#: blowout on the wall it hangs beside. A mapper thinks in radius and a
#: multiplier rather than in candela.
INTENSITY_PERCENT_DEFAULT = 100


def lights(world: CgzMap) -> list[dict[str, Any]]:
    """Every `light` entity, in the shape both clients draw.

    AC's own light entity is `radius, r, g, b`, and a light whose `g` and `b`
    are zero is a **grey** light of brightness `r` — the engine's shorthand, and
    the form most community maps use. Ours puts an intensity percentage in the
    fifth attribute, which AC ignores, so an exported map still opens there.
    """
    out: list[dict[str, Any]] = []
    for e in world.entities:
        if e.type != LIGHT:
            continue
        radius = max(1, e.attr1)
        r, g, b = e.attr2, e.attr3, e.attr4
        if g == 0 and b == 0:
            g = b = r
        percent = e.attr5 or INTENSITY_PERCENT_DEFAULT
        out.append(
            {
                "x": float(e.x),
                "y": float(e.y),
                "z": float(e.z),
                "radius": float(radius),
                "color": (r << 16) | (g << 8) | b,
                "intensity": round(percent / 100.0, 4),
            }
        )
    return out
