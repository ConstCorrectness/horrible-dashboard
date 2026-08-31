"""Generate the cross-language conformance fixture for "was this a match?".

    PYTHONPATH=. uv run python scripts/gen_hassault_result_vectors.py

The output is committed and replayed by *both*
`backend/tests/test_hassault_results.py` and
`apps/native-fps/tests/conformance.rs`, which is what keeps
`results.is_recordable` and `Summary::is_recordable` from drifting apart.

There is no browser entry: the pane does not decide this, it reads `won`,
`recordable` and the card the server already assembled. Two implementations, one
file — the same bargain `physics-vectors.json` makes for three.

The two predicates differ in their third term on purpose, and the fixture is
where that is pinned rather than assumed. The node asks `damageDealt > 0`; the
native client asks `hits > 0`, because it cannot reproduce `damage_dealt` (the
wire's hitmarker is uncapped, the server's counter is capped at the victim's
remaining health — see the header of `apps/native-fps/src/summary.rs`). Every
case therefore carries **both** numbers, and every case is chosen so the two
agree. A case where they disagree would be a case where one card says a match
happened and the other says it did not.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.modules.hassault.results import is_recordable

OUT = Path("packages/core/src/modules/hassault/__tests__/result-vectors.json")

#: `{name, kills, deaths, damageDealt, hits, opponents, bestOther}`.
#:
#: `bestOther` is the highest kill count anybody else in the room reached, and
#: `-1` is what `max(..., default=-1)` yields in an empty room — the value that
#: made every abandoned session a victory.
CASES: list[dict[str, object]] = [
    {
        "name": "opened the pane and left",
        "kills": 0,
        "deaths": 0,
        "damageDealt": 0,
        "hits": 0,
        "opponents": 0,
        "bestOther": -1,
    },
    {
        "name": "alone on a map, shot a wall",
        "kills": 0,
        "deaths": 0,
        "damageDealt": 0,
        "hits": 0,
        "opponents": 0,
        "bestOther": -1,
    },
    {
        "name": "joined a room with a bot and left immediately",
        "kills": 0,
        "deaths": 0,
        "damageDealt": 0,
        "hits": 0,
        "opponents": 1,
        "bestOther": 0,
    },
    {
        "name": "landed one hit on a bot",
        "kills": 0,
        "deaths": 0,
        "damageDealt": 34,
        "hits": 1,
        "opponents": 1,
        "bestOther": 0,
    },
    {
        "name": "died once without landing anything",
        "kills": 0,
        "deaths": 1,
        "damageDealt": 0,
        "hits": 0,
        "opponents": 1,
        "bestOther": 1,
    },
    {
        "name": "traded one for one",
        "kills": 1,
        "deaths": 1,
        "damageDealt": 118,
        "hits": 4,
        "opponents": 1,
        "bestOther": 1,
    },
    {
        "name": "outscored the room",
        "kills": 9,
        "deaths": 3,
        "damageDealt": 1240,
        "hits": 41,
        "opponents": 3,
        "bestOther": 5,
    },
    {
        "name": "tied at the top",
        "kills": 5,
        "deaths": 5,
        "damageDealt": 700,
        "hits": 24,
        "opponents": 2,
        "bestOther": 5,
    },
    {
        "name": "flattened, but it was a match",
        "kills": 0,
        "deaths": 15,
        "damageDealt": 0,
        "hits": 0,
        "opponents": 5,
        "bestOther": 15,
    },
]


def main() -> None:
    out = []
    for case in CASES:
        recordable = is_recordable(case)
        best = int(case["bestOther"])  # type: ignore[arg-type]
        kills = int(case["kills"])  # type: ignore[arg-type]
        won = recordable and kills >= best
        mvp = recordable and kills > best
        if not recordable:
            verdict = "NO CONTEST"
        elif mvp:
            verdict = "MVP"
        elif won:
            verdict = "TOP OF THE BOARD"
        else:
            verdict = "MATCH OVER"
        # The one invariant a generator can enforce that no reader can: the two
        # predicates must agree on every case in the file.
        native = int(case["opponents"]) > 0 and (  # type: ignore[arg-type]
            kills > 0
            or int(case["deaths"]) > 0  # type: ignore[arg-type]
            or int(case["hits"]) > 0  # type: ignore[arg-type]
        )
        assert native == recordable, f"predicates disagree on {case['name']!r}"
        out.append({**case, "expect": {"recordable": recordable, "won": won, "mvp": mvp, "verdict": verdict}})

    payload = {
        "_comment": (
            "Cross-language conformance vectors for 'was this a match?'. Read by "
            "BOTH backend/tests/test_hassault_results.py (results.is_recordable) "
            "and apps/native-fps/tests/conformance.rs "
            "(summary::Summary::is_recordable + verdict). These pin that the two "
            "agree, not that either is right in the abstract. Regenerate with "
            "scripts/gen_hassault_result_vectors.py and make both suites pass "
            "before committing. 'damageDealt' is what the node counts and 'hits' "
            "is what the native client counts; every case is chosen so the two "
            "reach the same answer, and the generator refuses to write a case "
            "where they do not."
        ),
        "verdicts": out,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} with {len(out)} cases")
    for case in out:
        e = case["expect"]
        print(f"  {case['name']:<44} {e['verdict']:<18} recordable={e['recordable']}")


if __name__ == "__main__":
    main()
