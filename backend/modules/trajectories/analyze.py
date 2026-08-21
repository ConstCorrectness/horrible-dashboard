"""Aggregates across runs — the half that makes this more than a log viewer.

Two questions, and the second is the reason the module exists.

**Stats**: which tools get called, how often they fail, how often the harness
blocked them. A `GROUP BY` over `traj_steps`, served rather than left to the
console so the agent's `trajectories.stats` tool can ask it too.

**Compare**: did changing the harness help? This is the continual-learning
question, and answering it carelessly is worse than not answering it.

## Why compare refuses to headline a number

Two harnesses were almost never run on the same tasks. If harness A ran on ten easy
goals and harness B on ten hard ones, B's lower success rate says nothing about B.
So the report separates two things:

- the **paired** comparison — goals both harnesses actually attempted, which is a
  real A/B and where the regressions and fixes are listed by name;
- the **marginal** rates over everything each harness ran, which are reported but
  flagged `comparable: false` unless enough goals are shared.

`MIN_PAIRED` is the threshold. Below it the caller is told the sets barely overlap
rather than being handed a difference that looks like evidence.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.modules.trajectories.store import get_db_conn, get_harness

#: Below this many shared goals, a rate difference is observational, not an A/B.
MIN_PAIRED = 5

#: Outcomes that count as a win when scoring a paired goal.
_WINS = ("success",)


def tool_stats(
    *, dataset_id: str | None = None, harness: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    """Per-tool call counts, failure rate and gate rate.

    `gated` is split out from `failed` deliberately: "the tool errored" and "the
    permission policy refused it" are the same shape in the data and completely
    different findings — one is a broken tool, the other is a harness that will not
    let the agent work.
    """
    where = ["s.kind = 'action'"]
    params: list[Any] = []
    if dataset_id:
        where.append("r.dataset_id = ?")
        params.append(dataset_id)
    if harness:
        where.append("r.harness = ?")
        params.append(harness)
    clause = " AND ".join(where)
    with get_db_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT s.name AS name,
                   COUNT(*) AS calls,
                   SUM(CASE WHEN s.ok = 0 THEN 1 ELSE 0 END) AS failures,
                   SUM(s.gated) AS gated,
                   AVG(s.duration_ms) AS avg_ms
            FROM traj_steps s JOIN traj_runs r ON r.id = s.run_id
            WHERE {clause}
            GROUP BY s.name ORDER BY calls DESC LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return [
        {
            "name": r["name"],
            "calls": r["calls"],
            "failures": r["failures"] or 0,
            "gated": r["gated"] or 0,
            "failureRate": round((r["failures"] or 0) / r["calls"], 4)
            if r["calls"]
            else 0.0,
            "avgMs": round(r["avg_ms"], 1) if r["avg_ms"] is not None else None,
        }
        for r in rows
    ]


def dataset_stats(dataset_id: str | None = None) -> dict[str, Any]:
    """Headline counts for a dataset: runs, outcomes, and the ungraded remainder."""
    where = " WHERE dataset_id = ?" if dataset_id else ""
    params = [dataset_id] if dataset_id else []
    with get_db_conn() as conn:
        totals = conn.execute(
            f"SELECT COUNT(*) AS runs, AVG(steps) AS avg_steps,"
            f" AVG(duration_ms) AS avg_ms FROM traj_runs{where}",
            params,
        ).fetchone()
        outcomes = conn.execute(
            f"SELECT COALESCE(outcome, 'ungraded') AS o, COUNT(*) AS n"
            f" FROM traj_runs{where} GROUP BY o",
            params,
        ).fetchall()
    return {
        "runs": totals["runs"],
        "avgSteps": round(totals["avg_steps"], 2) if totals["avg_steps"] else 0.0,
        "avgMs": round(totals["avg_ms"], 1) if totals["avg_ms"] else None,
        "outcomes": {r["o"]: r["n"] for r in outcomes},
        "tools": tool_stats(dataset_id=dataset_id, limit=10),
    }


def _harness_side(fingerprint: str) -> dict[str, Any]:
    with get_db_conn() as conn:
        agg = conn.execute(
            "SELECT COUNT(*) AS runs, AVG(steps) AS avg_steps, AVG(duration_ms) AS avg_ms,"
            " SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS wins,"
            " SUM(CASE WHEN outcome IS NULL THEN 1 ELSE 0 END) AS ungraded"
            " FROM traj_runs WHERE harness = ?",
            (fingerprint,),
        ).fetchone()
    graded = (agg["runs"] or 0) - (agg["ungraded"] or 0)
    harness = get_harness(fingerprint)
    return {
        "fingerprint": fingerprint,
        "label": harness.label if harness else fingerprint,
        "model": harness.model if harness else "",
        "runs": agg["runs"] or 0,
        "graded": graded,
        "wins": agg["wins"] or 0,
        #: None rather than 0 when nothing is graded — a rate over zero graded runs
        #: is not "0%", it is "unknown", and rendering it as 0% invents a finding.
        "successRate": round((agg["wins"] or 0) / graded, 4) if graded else None,
        "avgSteps": round(agg["avg_steps"], 2) if agg["avg_steps"] else 0.0,
        "avgMs": round(agg["avg_ms"], 1) if agg["avg_ms"] else None,
        "tools": tool_stats(harness=fingerprint, limit=15),
    }


def _goal_outcomes(fingerprint: str) -> dict[str, str]:
    """Best outcome per goal for one harness.

    "Best" because a goal run three times with one success is a goal the harness
    can do; taking the last run instead would make the comparison depend on
    ordering noise.
    """
    with get_db_conn() as conn:
        rows = conn.execute(
            "SELECT goal, outcome FROM traj_runs WHERE harness = ? AND goal != ''"
            " AND outcome IS NOT NULL",
            (fingerprint,),
        ).fetchall()
    best: dict[str, str] = {}
    for row in rows:
        goal, outcome = row["goal"], row["outcome"]
        if best.get(goal) in _WINS:
            continue
        best[goal] = outcome
    return best


def compare(a: str, b: str) -> dict[str, Any]:
    """Compare two harnesses. See the module docstring for what `comparable` means."""
    side_a, side_b = _harness_side(a), _harness_side(b)
    goals_a, goals_b = _goal_outcomes(a), _goal_outcomes(b)
    shared = sorted(set(goals_a) & set(goals_b))

    regressions = [g for g in shared if goals_a[g] in _WINS and goals_b[g] not in _WINS]
    fixes = [g for g in shared if goals_a[g] not in _WINS and goals_b[g] in _WINS]
    paired_a = sum(1 for g in shared if goals_a[g] in _WINS)
    paired_b = sum(1 for g in shared if goals_b[g] in _WINS)

    # Per-tool call-frequency delta, normalised per run so a harness with more
    # runs does not look like one that calls everything more often.
    per_run: dict[str, dict[str, float]] = defaultdict(dict)
    for key, side in (("a", side_a), ("b", side_b)):
        runs = max(1, side["runs"])
        for tool in side["tools"]:
            per_run[tool["name"]][key] = round(tool["calls"] / runs, 3)
    tool_delta = sorted(
        (
            {
                "name": name,
                "a": values.get("a", 0.0),
                "b": values.get("b", 0.0),
                "delta": round(values.get("b", 0.0) - values.get("a", 0.0), 3),
            }
            for name, values in per_run.items()
        ),
        key=lambda row: abs(row["delta"]),
        reverse=True,
    )

    comparable = len(shared) >= MIN_PAIRED
    return {
        "a": side_a,
        "b": side_b,
        "pairedGoals": len(shared),
        "comparable": comparable,
        #: The honest headline. When the goal sets barely overlap this says so
        #: instead of handing back a difference that reads as evidence.
        "note": (
            f"{len(shared)} goals in common — a real A/B."
            if comparable
            else f"Only {len(shared)} goals in common; the marginal rates describe"
            " different workloads and are not a comparison."
        ),
        "pairedSuccess": {"a": paired_a, "b": paired_b, "of": len(shared)},
        "regressions": regressions[:25],
        "fixes": fixes[:25],
        "toolDelta": tool_delta[:25],
    }
