"""Generate the cross-language crosshair fixture for HorribleAssault.

    PYTHONPATH=. uv run python scripts/gen_hassault_crosshair_vectors.py

`crosshair.py` is the reference for the crosshair spec, its `HXC1-` share code
and the shapes it draws. `crosshair.ts` and `apps/native-fps/src/crosshair.rs`
are ports, and this fixture — replayed by `test_hassault_crosshair.py`,
`crosshair.test.ts` and `tests/conformance.rs` — is what makes a code pasted
into either client draw the same rectangles.

Expectations come from the Python side, so this pins *agreement*;
`test_hassault_crosshair.py` argues the behaviour is right.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.modules.hassault import crosshair as xh
from backend.modules.hassault.crosshair import CrosshairCodeError, Spec

OUT = Path("packages/core/src/modules/hassault/__tests__/crosshair-vectors.json")

SPECS = [
    Spec(),
    *xh.PRESETS.values(),
    # Out of range on every axis: clamped, never believed.
    Spec(size=40, gap=-30, thickness=9, outline=7, alpha=0.01),
    Spec(size=0, gap=99, thickness=0, outline=-1, alpha=4),
    # Off-quantum values: rounded half up, the same way in every port.
    Spec(size=3.26, gap=-1.75, thickness=1.45, outline=0.15, alpha=0.555),
    Spec(size=7.75, gap=0.24, thickness=0.25, outline=0.05, alpha=0.335),
    # Unknown names fall back.
    Spec(style="spinner", color="beige"),
    # Custom colours: bright is kept, dark is lifted, out-of-range bytes clamp.
    Spec(color="custom", rgb=(255, 0, 128)),
    Spec(color="custom", rgb=(0, 0, 0)),
    Spec(color="custom", rgb=(0, 0, 255)),
    Spec(color="custom", rgb=(20, 60, 20)),
    Spec(color="custom", rgb=(300, -4, 89.6)),  # type: ignore[arg-type]
    Spec(style="circle", dot=False, outline=0, gap=-5),
    Spec(style="crossDot", t_style=True, dot=False, dynamic=False),
    Spec(style="dot", dot=False, thickness=3, outline=2),
]

RECT_INPUTS = [
    # (spec, cone_gap_px, unit)
    (Spec(), 0.0, 3.0),
    (Spec(), 7.8, 3.0),
    (Spec(dynamic=False), 7.8, 3.0),
    (Spec(), 0.0, 2.0),
    (Spec(), 3.25, 4.5),
    (Spec(outline=0), 0.0, 3.0),
    (Spec(gap=-5), 0.0, 3.0),
    (Spec(t_style=True), 1.5, 3.0),
    (Spec(style="crossDot"), 2.0, 3.0),
    (Spec(style="crossDot", dot=False), 2.0, 3.0),
    (Spec(style="dot", thickness=0.2), 9.0, 2.0),
    (Spec(style="circle"), 0.0, 3.0),
    (Spec(style="circle", gap=-5, dot=False), 0.0, 3.0),
    (Spec(style="circle"), 12.0, 3.0),
    (Spec(thickness=0.2, outline=0.1), 0.0, 1.0),
    (Spec(), -4.0, 3.0),
    *((s, 5.0, 3.0) for s in xh.PRESETS.values()),
]

K = xh.KEYS
SETTINGS_BAGS = [
    {},
    {K["color"]: "green", K["size"]: 5, K["gap"]: -2},
    {K["color"]: "#102030", K["outline"]: False, K["outlineThickness"]: 1.2},
    {K["color"]: "#FFAA00", K["outlineThickness"]: 1.2},
    {K["color"]: "#fff", K["style"]: "spinner", K["dot"]: "yes"},
    {K["color"]: "# 12345", K["size"]: "big", K["alpha"]: True, K["gap"]: None},
    {K["style"]: "circle", K["tStyle"]: True, K["dynamic"]: False, K["alpha"]: 0.333},
    {K["thickness"]: 50, K["size"]: -3, K["outline"]: True},
]

HUD_UNITS = [
    (1080, 1.0),
    (1440, 1.0),
    (2160, 1.0),
    (720, 1.0),
    (900, 1.0),
    (480, 1.0),
    (1080, 1.5),
    (1080, 0.75),
    (1080, 9.0),
    (1080, 0.1),
    (1620, 1.0),
    (1260, 1.25),
]


def _variants(code: str) -> list[str]:
    """How a pasted code arrives: case, whitespace, look-alikes, dashes."""
    body = code[len(xh.PREFIX) :]
    return [
        code,
        code.lower(),
        f"  {code}\n",
        xh.PREFIX + body.replace("-", ""),
        xh.PREFIX + body.replace("-", " "),
        xh.PREFIX + body.replace("0", "O").replace("1", "I"),
        xh.PREFIX + body.replace("1", "l"),
    ]


def _reject(code: str) -> str:
    try:
        xh.decode(code)
    except CrosshairCodeError as e:
        return e.reason
    raise AssertionError(f"{code} decoded")


def _with_crc(body: bytes) -> bytes:
    return body + bytes([xh.crc8(body)])


def main() -> None:
    encode = [
        {
            "spec": s.to_dict(),
            "normalized": xh.normalize(s).to_dict(),
            "code": xh.encode(s),
        }
        for s in SPECS
    ]

    decode = []
    for case in encode[: len(xh.PRESETS) + 1]:
        for variant in _variants(case["code"]):
            decode.append({"code": variant, "spec": case["normalized"]})
    # A later writer appended two fields under the same version: still decodes.
    ring = Spec(style="circle", color="cyan")
    longer = _with_crc(xh.pack(ring)[:-1] + bytes([0xAB, 0x07]))
    decode.append(
        {"code": xh.encode_payload(longer), "spec": xh.normalize(ring).to_dict()}
    )
    # Bytes in range for the layout but not for the field: clamped or defaulted.
    odd = _with_crc(bytes([1, 9, 0xFF, 200, 0x80, 0, 255, 3, 9, 1, 2, 3]))
    decode.append({"code": xh.encode_payload(odd), "spec": xh.unpack(odd).to_dict()})

    good = xh.encode(Spec())
    plain = xh.pack(Spec())
    bad_crc = plain[:-1] + bytes([plain[-1] ^ 0x01])
    flipped = bytearray(plain)
    flipped[3] ^= 0x04
    rejects = [
        "",
        "HXC1",
        "HXC1-",
        "HXC2-" + good[5:],
        "CSGO-" + good[5:],
        good[5:],
        good[:-3],
        good[:12],
        good[:6] + "U" + good[7:],
        good[:-1] + "!",
        xh.encode_payload(bad_crc),
        xh.encode_payload(bytes(flipped)),
        xh.encode_payload(_with_crc(bytes([2]) + plain[1:-1])),
        xh.encode_payload(_with_crc(bytes([0]) + plain[1:-1])),
    ]
    reject_cases = [{"code": c, "reason": _reject(c)} for c in rejects]

    rects = [
        {
            "spec": s.to_dict(),
            "coneGap": g,
            "unit": u,
            "shapes": xh.crosshair_rects(s, g, u),
        }
        for s, g, u in RECT_INPUTS
    ]

    settings = [
        {"bag": bag, "spec": xh.from_settings(bag).to_dict()}
        for bag in SETTINGS_BAGS
    ]
    to_settings = [{"spec": s.to_dict(), "rows": xh.to_settings(s)} for s in SPECS]

    doc = {
        "generator": "scripts/gen_hassault_crosshair_vectors.py",
        "tolerance": 1e-9,
        "prefix": xh.PREFIX,
        "version": xh.VERSION,
        "styles": list(xh.STYLES),
        "namedColors": {k: list(v) for k, v in xh.NAMED_COLORS.items()},
        "lumaFloor": xh.LUMA_FLOOR,
        "outlineAlpha": xh.OUTLINE_ALPHA,
        "pipAlpha": xh.PIP_ALPHA,
        "conePxPerRad": xh.CONE_PX_PER_RAD,
        "default": Spec().to_dict(),
        "presets": {name: xh.encode(s) for name, s in xh.PRESETS.items()},
        "crc8": [
            {"bytes": list(b), "crc": xh.crc8(b)}
            for b in (b"", b"123456789", bytes(range(13)), b"\xff\x00\x80")
        ],
        "hudUnit": [
            {"height": h, "hudScale": k, "unit": xh.hud_unit(h, k)}
            for h, k in HUD_UNITS
        ],
        "coneGap": [
            {"cone": c, "unit": u, "px": xh.cone_gap_px(c, u)}
            for c, u in (
                (0.0, 3.0),
                (0.02, 3.0),
                (0.06, 2.0),
                (-0.1, 3.0),
                (0.035, 4.5),
            )
        ],
        "encode": encode,
        "decode": decode,
        "rejects": reject_cases,
        "rects": rects,
        "fromSettings": settings,
        "toSettings": to_settings,
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(
        f"wrote {OUT} ({len(encode)} encode, {len(decode)} decode, "
        f"{len(reject_cases)} rejects, {len(rects)} rect cases)"
    )


if __name__ == "__main__":
    main()
