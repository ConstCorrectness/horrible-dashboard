"""The crosshair: spec v1, its share code, and the shapes it draws.

This is the **reference**. The browser (`packages/core/.../hassault/crosshair.ts`)
and the native client (`apps/native-fps/src/crosshair.rs`) each carry a port, and
`crosshair-vectors.json` — written by `scripts/gen_hassault_crosshair_vectors.py`
from this file — is replayed by pytest, vitest and cargo. A share code pasted into
either client therefore draws the same rectangles, to the pixel, in both.

### Three pieces, each pure

- **`normalize(spec)`** quantizes every field to what a share code can carry and
  applies the clamps. Both clients normalize what they read from the settings bag
  before drawing, so the picture a code produces is the picture its author saw.
- **`encode` / `decode`** — `HXC1-` and Crockford base32 in groups of five. The
  payload is a version byte, the fields in a fixed order, then a CRC-8 over all of
  it. An unknown version or a bad CRC is rejected; bytes after the known fields
  are ignored, so a later writer can *append* a field under the same version and
  an older reader still decodes the rest. The version only moves for a layout an
  older reader would misread.
- **`crosshair_rects(spec, cone_gap_px, hud_unit)`** — the shapes to draw, in
  pixels around the screen centre (y down), outline layer first. The caller picks
  the colour by layer (`fill` is the spec's, `outline` near-black) and nothing
  else, so the geometry is decided in exactly one place.

### Hit and kill feedback are not here

A hitmarker is its own overlay, drawn around the reticle by each client. It never
recolours or reshapes the crosshair: while spraying into somebody — the moment aim
matters most — the thing you are aiming with must not change under you.

### Rounding

Every quantization is `floor(x * k + 0.5)`. Python's `round` is banker's rounding
and Rust's rounds half away from zero; the three ports agree only because none of
them uses either.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

# ---- the spec ----------------------------------------------------------------

STYLES: tuple[str, ...] = ("cross", "crossDot", "dot", "circle")

#: The six named colours, as 8-bit sRGB. All bright: a dark crosshair on a dark
#: map is the one choice that would make the game worse.
NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "white": (235, 240, 245),
    "green": (115, 242, 115),
    "cyan": (102, 230, 242),
    "amber": (247, 199, 89),
    "magenta": (242, 115, 230),
    "red": (247, 89, 82),
}
COLOR_NAMES: tuple[str, ...] = tuple(NAMED_COLORS)
CUSTOM = "custom"

#: The floor a custom colour's luma is lifted to, on 0–255 (Rec. 709 weights on
#: the sRGB values). A near-black reticle is invisible on most of every map, so a
#: custom colour may be any hue but not dark.
LUMA_FLOOR = 90
_LUMA_WEIGHTS = (2126, 7152, 722)  # sum 10000

# (lowest, highest, quanta per unit) per numeric field, in quanta: a value is
# `q / k`. Integers, so the bounds are exact in every port; the share code carries
# `q` and the settings rows use the same ranges.
SIZE = (2, 24, 2)  # 1–12 in halves
GAP = (-10, 40, 2)  # -5–20 in halves, signed
THICKNESS = (2, 30, 10)  # 0.2–3 in tenths
OUTLINE = (0, 20, 10)  # 0–2 in tenths
ALPHA = (15, 100, 100)  # 0.15–1 in hundredths

#: The near-black every outline is drawn in, and its opacity before the spec's
#: own alpha multiplies it. Clients own the RGB; the alpha is pinned here.
OUTLINE_ALPHA = 0.85
#: The larger centre pip `crossDot` adds, as a fraction of the fill's alpha.
PIP_ALPHA = 0.45
#: Screen pixels per radian of cone, per HUD unit. The native client's old
#: `spread * 260 * u * 0.5`, kept so its crosshair opens exactly as wide as it did.
CONE_PX_PER_RAD = 130.0


@dataclass(frozen=True)
class Spec:
    style: str = "cross"
    size: float = 3.0
    gap: float = 4.0
    thickness: float = 0.6
    #: Outline width in HUD units; 0 is no outline.
    outline: float = 0.3
    alpha: float = 0.9
    dot: bool = True
    #: Drop the top arm.
    t_style: bool = False
    #: Open with the weapon's cone. Off draws the gap alone.
    dynamic: bool = True
    #: One of `COLOR_NAMES`, or `"custom"` with `rgb`.
    color: str = "white"
    rgb: tuple[int, int, int] = NAMED_COLORS["white"]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tStyle"] = d.pop("t_style")
        d["rgb"] = list(self.rgb)
        return d

    @staticmethod
    def from_dict(d: dict) -> Spec:
        return Spec(
            style=d.get("style", "cross"),
            size=d.get("size", 3.0),
            gap=d.get("gap", 4.0),
            thickness=d.get("thickness", 0.6),
            outline=d.get("outline", 0.3),
            alpha=d.get("alpha", 0.9),
            dot=bool(d.get("dot", True)),
            t_style=bool(d.get("tStyle", False)),
            dynamic=bool(d.get("dynamic", True)),
            color=d.get("color", "white"),
            rgb=tuple(d.get("rgb", NAMED_COLORS["white"])),  # type: ignore[arg-type]
        )


DEFAULT = Spec()


def _q(value: float, rng: tuple[int, int, int]) -> int:
    """`value` in quanta, clamped. Non-finite reads as the range floor."""
    lo, hi, k = rng
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return lo
    return max(lo, min(hi, math.floor(value * k + 0.5)))


def _from_q(q: int, rng: tuple[int, int, int]) -> float:
    # Divide rather than multiply by a step: 7 * 0.1 is 0.7000000000000001,
    # 7 / 10 is 0.7, and the ports must land on the same double.
    return q / rng[2]


def _byte(v: object) -> int:
    if not isinstance(v, (int, float)) or not math.isfinite(v):
        return 0
    return max(0, min(255, math.floor(v + 0.5)))


def lift_luma(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """Lift a colour towards white until its luma reaches `LUMA_FLOOR`.

    Integer arithmetic throughout, so the three ports agree exactly: each channel
    moves by `ceil((255 - c) * need / room)`, which is at least the fraction that
    puts the weighted sum on the floor.
    """
    luma = sum(w * c for w, c in zip(_LUMA_WEIGHTS, rgb))
    floor = LUMA_FLOOR * 10000
    if luma >= floor:
        return rgb
    need = floor - luma
    room = 255 * 10000 - luma
    return tuple(  # type: ignore[return-value]
        c + -((-(255 - c) * need) // room) for c in rgb
    )


def normalize(spec: Spec) -> Spec:
    """The spec as a share code carries it: quantized, clamped, colour lifted."""
    style = spec.style if spec.style in STYLES else "cross"
    if spec.color in NAMED_COLORS:
        color, rgb = spec.color, NAMED_COLORS[spec.color]
    elif spec.color == CUSTOM:
        color = CUSTOM
        rgb = lift_luma(tuple(_byte(c) for c in (list(spec.rgb) + [0, 0, 0])[:3]))  # type: ignore[arg-type]
    else:
        color, rgb = "white", NAMED_COLORS["white"]
    return Spec(
        style=style,
        size=_from_q(_q(spec.size, SIZE), SIZE),
        gap=_from_q(_q(spec.gap, GAP), GAP),
        thickness=_from_q(_q(spec.thickness, THICKNESS), THICKNESS),
        outline=_from_q(_q(spec.outline, OUTLINE), OUTLINE),
        alpha=_from_q(_q(spec.alpha, ALPHA), ALPHA),
        dot=bool(spec.dot),
        t_style=bool(spec.t_style),
        dynamic=bool(spec.dynamic),
        color=color,
        rgb=rgb,
    )


# ---- settings bag ------------------------------------------------------------

#: The settings rows a spec lives in. `hassault.crosshair.color` holds a name or
#: `#rrggbb` — one row, so a custom colour can never disagree with a name.
KEYS = {
    "style": "hassault.crosshair.style",
    "size": "hassault.crosshair.size",
    "gap": "hassault.crosshair.gap",
    "thickness": "hassault.crosshair.thickness",
    "outline": "hassault.crosshair.outline",
    "outlineThickness": "hassault.crosshair.outlineThickness",
    "alpha": "hassault.crosshair.alpha",
    "dot": "hassault.crosshair.dot",
    "tStyle": "hassault.crosshair.tStyle",
    "dynamic": "hassault.crosshair.dynamic",
    "color": "hassault.crosshair.color",
}


def hex_rgb(text: str) -> tuple[int, int, int] | None:
    """`#rrggbb` as bytes, or None. Strict: no shorthand, no names, no spaces."""
    t = text.strip()
    if len(t) != 7 or t[0] != "#" or any(c not in "0123456789abcdefABCDEF" for c in t[1:]):
        return None
    return (int(t[1:3], 16), int(t[3:5], 16), int(t[5:7], 16))


def rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{c:02x}" for c in rgb)


def from_settings(bag: dict) -> Spec:
    """The spec the settings bag describes, normalized.

    Per key: a missing or mistyped row is that field's default, never a reason to
    drop the rest. `outline` is the on/off switch and `outlineThickness` the
    width it draws at, so turning the outline off and on keeps the width.
    """
    d = DEFAULT

    def get(field: str, kind: type, default):
        v = bag.get(KEYS[field])
        if kind is float:
            ok = isinstance(v, (int, float)) and not isinstance(v, bool)
        else:
            ok = isinstance(v, kind)
        return v if ok else default

    outline_on = get("outline", bool, True)
    width = get("outlineThickness", float, d.outline)
    color_text = get("color", str, d.color)
    rgb = hex_rgb(color_text)
    return normalize(
        Spec(
            style=get("style", str, d.style),
            size=get("size", float, d.size),
            gap=get("gap", float, d.gap),
            thickness=get("thickness", float, d.thickness),
            outline=width if outline_on else 0.0,
            alpha=get("alpha", float, d.alpha),
            dot=get("dot", bool, d.dot),
            t_style=get("tStyle", bool, d.t_style),
            dynamic=get("dynamic", bool, d.dynamic),
            color=CUSTOM if rgb else color_text,
            rgb=rgb or d.rgb,
        )
    )


def to_settings(spec: Spec) -> dict:
    """The rows to write for `spec` — what applying a share code saves.

    An outline of 0 writes the switch off and leaves the width row alone, so the
    width the player had comes back when they turn it on again.
    """
    s = normalize(spec)
    rows = {
        KEYS["style"]: s.style,
        KEYS["size"]: s.size,
        KEYS["gap"]: s.gap,
        KEYS["thickness"]: s.thickness,
        KEYS["outline"]: s.outline > 0,
        KEYS["alpha"]: s.alpha,
        KEYS["dot"]: s.dot,
        KEYS["tStyle"]: s.t_style,
        KEYS["dynamic"]: s.dynamic,
        KEYS["color"]: rgb_hex(s.rgb) if s.color == CUSTOM else s.color,
    }
    if s.outline > 0:
        rows[KEYS["outlineThickness"]] = s.outline
    return rows


# ---- share code --------------------------------------------------------------

PREFIX = "HXC1-"
VERSION = 1
#: Bytes of v1 fields after the version byte.
_FIELDS = 11
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}
_DECODE.update({"O": 0, "I": 1, "L": 1})


class CrosshairCodeError(ValueError):
    """Why a code was refused: `prefix`, `char`, `short`, `crc` or `version`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def crc8(data: bytes) -> int:
    """CRC-8/SMBUS: polynomial 0x07, init 0, no reflection."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def pack(spec: Spec) -> bytes:
    s = normalize(spec)
    flags = (1 if s.dot else 0) | (2 if s.t_style else 0) | (4 if s.dynamic else 0)
    color = COLOR_NAMES.index(s.color) if s.color in NAMED_COLORS else len(COLOR_NAMES)
    rgb = s.rgb if s.color == CUSTOM else (0, 0, 0)
    body = bytes(
        [
            VERSION,
            STYLES.index(s.style),
            flags,
            _q(s.size, SIZE),
            _q(s.gap, GAP) & 0xFF,
            _q(s.thickness, THICKNESS),
            _q(s.outline, OUTLINE),
            _q(s.alpha, ALPHA),
            color,
            *rgb,
        ]
    )
    return body + bytes([crc8(body)])


def unpack(payload: bytes) -> Spec:
    if len(payload) < 2 + _FIELDS:
        raise CrosshairCodeError("short")
    if crc8(payload[:-1]) != payload[-1]:
        raise CrosshairCodeError("crc")
    if payload[0] != VERSION:
        raise CrosshairCodeError("version")
    f = payload[1 : 1 + _FIELDS]
    gap = f[3] - 256 if f[3] >= 128 else f[3]
    if f[7] < len(COLOR_NAMES):
        color, rgb = COLOR_NAMES[f[7]], NAMED_COLORS[COLOR_NAMES[f[7]]]
    elif f[7] == len(COLOR_NAMES):
        color, rgb = CUSTOM, (f[8], f[9], f[10])
    else:
        color, rgb = "white", NAMED_COLORS["white"]
    return normalize(
        Spec(
            style=STYLES[f[0]] if f[0] < len(STYLES) else "cross",
            size=_from_q(f[2], SIZE),
            gap=_from_q(gap, GAP),
            thickness=_from_q(f[4], THICKNESS),
            outline=_from_q(f[5], OUTLINE),
            alpha=_from_q(f[6], ALPHA),
            dot=bool(f[1] & 1),
            t_style=bool(f[1] & 2),
            dynamic=bool(f[1] & 4),
            color=color,
            rgb=rgb,
        )
    )


def base32(data: bytes) -> str:
    out, acc, bits = [], 0, 0
    for b in data:
        acc = (acc << 8) | b
        bits += 8
        while bits >= 5:
            bits -= 5
            out.append(_ALPHABET[(acc >> bits) & 31])
    if bits:
        out.append(_ALPHABET[(acc << (5 - bits)) & 31])
    return "".join(out)


def unbase32(text: str) -> bytes:
    out, acc, bits = bytearray(), 0, 0
    for ch in text:
        v = _DECODE.get(ch)
        if v is None:
            raise CrosshairCodeError("char")
        acc = ((acc << 5) | v) & 0xFFFF
        bits += 5
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out)


def encode_payload(payload: bytes) -> str:
    chars = base32(payload)
    return PREFIX + "-".join(chars[i : i + 5] for i in range(0, len(chars), 5))


def encode(spec: Spec) -> str:
    return encode_payload(pack(spec))


def decode(code: str) -> Spec:
    """A spec from a share code, or `CrosshairCodeError` saying why not.

    Forgiving about what a paste does to a code — case, surrounding space, dashes
    and spaces inside, and the Crockford look-alikes O/I/L — and strict about
    everything that would change what it draws.
    """
    t = code.strip().upper()
    if not t.startswith(PREFIX):
        raise CrosshairCodeError("prefix")
    body = "".join(ch for ch in t[len(PREFIX) :] if ch not in "- ")
    return unpack(unbase32(body))


# ---- geometry ----------------------------------------------------------------


def hud_unit(height_px: float, hud_scale: float) -> float:
    """Pixels per HUD unit for a viewport `height_px` tall.

    Whole pixels per 360 of height, times the player's HUD scale — the native
    HUD's own unit, so 1440p and 4K can land on 1080p's size rather than drawing
    a reticle that is a fraction of a pixel off on every arm.
    """
    scale = min(1.5, max(0.75, hud_scale))
    return max(2.0, math.floor(height_px / 360.0 + 0.5) * scale)


def cone_gap_px(cone_rad: float, unit: float) -> float:
    """How far a cone of `cone_rad` opens the crosshair, in pixels."""
    return max(0.0, cone_rad) * CONE_PX_PER_RAD * unit


def crosshair_rects(spec: Spec, cone_gap: float, unit: float) -> list[dict]:
    """The shapes to draw, around the centre, outline layer first.

    Each is a rect `{kind: "rect", layer, alpha, x, y, w, h}` or, for the circle
    style, a ring `{kind: "ring", layer, alpha, r0, r1}`. `alpha` is final: the
    fill's is the spec's (times the pip's fraction), the outline's is
    `OUTLINE_ALPHA` times that.
    """
    s = normalize(spec)
    half = max(s.thickness * unit, 0.35)
    arm = s.size * unit
    gap = s.gap * unit * 0.5 + (max(0.0, cone_gap) if s.dynamic else 0.0)
    out = max(s.outline * unit, 1.0) if s.outline > 0 else 0.0

    # Elements in fill geometry, with each one's alpha fraction.
    elements: list[tuple[str, tuple[float, ...], float]] = []

    def square(r: float, frac: float = 1.0) -> None:
        elements.append(("rect", (-r, -r, r * 2, r * 2), frac))

    def arms() -> None:
        elements.append(("rect", (-gap - arm, -half, arm, half * 2), 1.0))
        elements.append(("rect", (gap, -half, arm, half * 2), 1.0))
        if not s.t_style:
            elements.append(("rect", (-half, -gap - arm, half * 2, arm), 1.0))
        elements.append(("rect", (-half, gap, half * 2, arm), 1.0))

    if s.style == "cross":
        arms()
        if s.dot:
            square(half)
    elif s.style == "crossDot":
        arms()
        # A second, larger pip so the centre survives a busy background, which
        # is the whole reason to pick this over the plain cross.
        square(half * 2, PIP_ALPHA)
        if s.dot:
            square(half)
    elif s.style == "dot":
        square(half)
    else:  # circle: a ring *at* the opening, the honest picture of a cone.
        inner = max(gap, 1.0)
        elements.append(("ring", (inner, inner + half * 2), 1.0))
        if s.dot:
            square(half)

    shapes: list[dict] = []
    if out > 0:
        for kind, g, frac in elements:
            alpha = OUTLINE_ALPHA * s.alpha * frac
            if kind == "rect":
                x, y, w, h = g
                shapes.append(
                    {
                        "kind": "rect",
                        "layer": "outline",
                        "alpha": alpha,
                        "x": x - out,
                        "y": y - out,
                        "w": w + out * 2,
                        "h": h + out * 2,
                    }
                )
            else:
                r0, r1 = g
                shapes.append(
                    {
                        "kind": "ring",
                        "layer": "outline",
                        "alpha": alpha,
                        "r0": max(0.0, r0 - out),
                        "r1": r1 + out,
                    }
                )
    for kind, g, frac in elements:
        alpha = s.alpha * frac
        if kind == "rect":
            x, y, w, h = g
            shapes.append(
                {
                    "kind": "rect",
                    "layer": "fill",
                    "alpha": alpha,
                    "x": x,
                    "y": y,
                    "w": w,
                    "h": h,
                }
            )
        else:
            shapes.append(
                {
                    "kind": "ring",
                    "layer": "fill",
                    "alpha": alpha,
                    "r0": g[0],
                    "r1": g[1],
                }
            )
    return shapes


#: A few starting points for the generator page, pinned in the vectors by their
#: codes so a preset is exactly what pasting that code gives.
PRESETS: dict[str, Spec] = {
    "default": DEFAULT,
    "classic": Spec(
        size=4.0, gap=2.0, thickness=0.5, outline=0.3, dot=False, color="green"
    ),
    "tight": Spec(
        size=2.0,
        gap=-1.0,
        thickness=0.4,
        outline=0.0,
        dot=False,
        dynamic=False,
        color="cyan",
    ),
    "t-cross": Spec(
        size=5.0, gap=3.0, thickness=0.5, t_style=True, dot=True, color="amber"
    ),
    "ring": Spec(style="circle", thickness=0.4, gap=6.0, dot=True, color="magenta"),
    "dot": Spec(style="dot", thickness=1.0, outline=0.4, color="red", alpha=1.0),
}

