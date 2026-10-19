"""The crosshair spec, its share code and its shapes (`crosshair.py`).

The first half argues the behaviour is right. The second replays
`crosshair-vectors.json`, which binds this reference to `crosshair.ts` and
`crosshair.rs`: if that fails, the generator was not re-run after a change here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.modules.hassault import crosshair as xh
from backend.modules.hassault.crosshair import CrosshairCodeError, Spec

VECTORS = (
    Path(__file__).resolve().parents[2]
    / "packages/core/src/modules/hassault/__tests__/crosshair-vectors.json"
)


def _luma(rgb) -> float:
    return (2126 * rgb[0] + 7152 * rgb[1] + 722 * rgb[2]) / 10000


# ---- behaviour ----------------------------------------------------------------


def test_a_code_round_trips_every_field():
    spec = Spec(
        style="circle",
        size=7.5,
        gap=-2.5,
        thickness=1.3,
        outline=0.7,
        alpha=0.42,
        dot=False,
        t_style=True,
        dynamic=False,
        color="custom",
        rgb=(200, 120, 255),
    )
    assert xh.decode(xh.encode(spec)) == spec


def test_a_code_looks_like_a_code():
    code = xh.encode(Spec())
    assert code.startswith("HXC1-")
    groups = code[5:].split("-")
    assert all(len(g) == 5 for g in groups[:-1]) and 1 <= len(groups[-1]) <= 5
    assert set("".join(groups)) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_a_paste_survives_case_spaces_and_look_alikes():
    spec = xh.PRESETS["classic"]
    code = xh.encode(spec)
    mangled = "  " + code.lower().replace("-", " ").replace("hxc1 ", "hxc1-") + "\n"
    assert xh.decode(mangled) == xh.normalize(spec)


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda c: c[:-1] + ("0" if c[-1] != "0" else "1"), "crc"),
        (lambda c: c.replace("HXC1-", "HXC9-"), "prefix"),
        (lambda c: c[:10], "short"),
        (lambda c: c[:-1] + "U", "char"),
    ],
)
def test_a_damaged_code_is_refused_and_says_why(mutate, reason):
    with pytest.raises(CrosshairCodeError) as err:
        xh.decode(mutate(xh.encode(Spec())))
    assert err.value.reason == reason


def test_an_unknown_version_is_refused_even_with_a_good_crc():
    body = bytes([2]) + xh.pack(Spec())[1:-1]
    with pytest.raises(CrosshairCodeError) as err:
        xh.decode(xh.encode_payload(body + bytes([xh.crc8(body)])))
    assert err.value.reason == "version"


def test_fields_appended_by_a_later_writer_are_ignored():
    spec = xh.PRESETS["t-cross"]
    body = xh.pack(spec)[:-1] + b"\x01\x02\x03"
    assert xh.decode(xh.encode_payload(body + bytes([xh.crc8(body)]))) == xh.normalize(
        spec
    )


def test_a_dark_custom_colour_is_lifted_but_keeps_its_hue():
    for rgb in [(0, 0, 0), (0, 0, 255), (40, 10, 10), (10, 60, 10)]:
        lifted = xh.normalize(Spec(color="custom", rgb=rgb)).rgb
        assert _luma(lifted) >= xh.LUMA_FLOOR
        # Towards white, so the strongest channel stays the strongest.
        assert max(range(3), key=lambda i: lifted[i]) == max(
            range(3), key=lambda i: rgb[i]
        )
    bright = (255, 128, 0)
    assert xh.normalize(Spec(color="custom", rgb=bright)).rgb == bright


def test_out_of_range_numbers_are_clamped_not_believed():
    s = xh.normalize(Spec(size=99, gap=-99, thickness=0, outline=-3, alpha=0))
    assert (s.size, s.gap, s.thickness, s.outline, s.alpha) == (
        12.0,
        -5.0,
        0.2,
        0.0,
        0.15,
    )


def test_the_gap_is_signed_and_opens_with_the_cone_only_when_dynamic():
    def left_arm_inner(spec, cone):
        fills = [r for r in xh.crosshair_rects(spec, cone, 3.0) if r["layer"] == "fill"]
        return fills[0]["x"] + fills[0]["w"]

    assert left_arm_inner(Spec(gap=-4), 0) > 0  # arms cross the centre
    assert left_arm_inner(Spec(gap=4), 9.0) == pytest.approx(
        left_arm_inner(Spec(gap=4), 0) - 9.0
    )
    still = Spec(gap=4, dynamic=False)
    assert left_arm_inner(still, 9.0) == left_arm_inner(still, 0)


def test_t_style_drops_the_top_arm_only():
    def arms(spec):
        return [r for r in xh.crosshair_rects(spec, 0, 3.0) if r["layer"] == "fill"]

    cross = arms(Spec(dot=False))
    tee = arms(Spec(dot=False, t_style=True))
    assert len(cross) == 4 and len(tee) == 3
    assert not any(r["y"] + r["h"] < 0 and r["w"] < r["h"] for r in tee)


def test_the_outline_surrounds_every_fill_and_is_drawn_first():
    shapes = xh.crosshair_rects(Spec(), 0, 3.0)
    outline = [s for s in shapes if s["layer"] == "outline"]
    fill = [s for s in shapes if s["layer"] == "fill"]
    assert shapes[: len(outline)] == outline
    for o, f in zip(outline, fill):
        assert o["x"] < f["x"] and o["y"] < f["y"]
        assert o["x"] + o["w"] > f["x"] + f["w"] and o["y"] + o["h"] > f["y"] + f["h"]
    assert not [
        s
        for s in xh.crosshair_rects(Spec(outline=0), 0, 3.0)
        if s["layer"] == "outline"
    ]


def test_the_presets_are_distinct_codes():
    codes = [xh.encode(s) for s in xh.PRESETS.values()]
    assert len(set(codes)) == len(codes)


# ---- the shared fixture --------------------------------------------------------


@pytest.fixture(scope="module")
def vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_the_fixture_constants_are_this_files(vectors):
    assert vectors["prefix"] == xh.PREFIX
    assert vectors["version"] == xh.VERSION
    assert vectors["styles"] == list(xh.STYLES)
    assert vectors["namedColors"] == {k: list(v) for k, v in xh.NAMED_COLORS.items()}
    assert vectors["lumaFloor"] == xh.LUMA_FLOOR
    assert vectors["outlineAlpha"] == xh.OUTLINE_ALPHA
    assert vectors["pipAlpha"] == xh.PIP_ALPHA
    assert vectors["conePxPerRad"] == xh.CONE_PX_PER_RAD
    assert vectors["default"] == Spec().to_dict()
    assert vectors["presets"] == {n: xh.encode(s) for n, s in xh.PRESETS.items()}


def test_the_fixture_replays(vectors):
    for case in vectors["crc8"]:
        assert xh.crc8(bytes(case["bytes"])) == case["crc"]
    for case in vectors["hudUnit"]:
        assert xh.hud_unit(case["height"], case["hudScale"]) == case["unit"]
    for case in vectors["coneGap"]:
        assert xh.cone_gap_px(case["cone"], case["unit"]) == pytest.approx(
            case["px"], abs=1e-9
        )
    for case in vectors["encode"]:
        spec = Spec.from_dict(case["spec"])
        assert xh.normalize(spec).to_dict() == case["normalized"]
        assert xh.encode(spec) == case["code"]
    for case in vectors["decode"]:
        assert xh.decode(case["code"]).to_dict() == case["spec"], case["code"]
    for case in vectors["rejects"]:
        with pytest.raises(CrosshairCodeError) as err:
            xh.decode(case["code"])
        assert err.value.reason == case["reason"], case["code"]
    tol = vectors["tolerance"]
    for case in vectors["rects"]:
        got = xh.crosshair_rects(
            Spec.from_dict(case["spec"]), case["coneGap"], case["unit"]
        )
        assert len(got) == len(case["shapes"])
        for g, w in zip(got, case["shapes"]):
            assert g.keys() == w.keys()
            for k, v in w.items():
                if isinstance(v, float):
                    assert g[k] == pytest.approx(v, abs=tol)
                else:
                    assert g[k] == v


def test_the_settings_bag_replays(vectors):
    for case in vectors["fromSettings"]:
        assert xh.from_settings(case["bag"]).to_dict() == case["spec"], case["bag"]
    for case in vectors["toSettings"]:
        assert xh.to_settings(Spec.from_dict(case["spec"])) == case["rows"]


def test_writing_a_spec_and_reading_it_back_is_the_same_spec():
    for spec in xh.PRESETS.values():
        assert xh.from_settings(xh.to_settings(spec)) == xh.normalize(spec)
    custom = Spec(color="custom", rgb=(250, 30, 200), outline=0)
    assert xh.from_settings(xh.to_settings(custom)) == xh.normalize(custom)
