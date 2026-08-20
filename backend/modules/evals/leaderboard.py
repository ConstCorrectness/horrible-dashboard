"""Comparing runs of one suite: who is ahead, what changed, and what nobody passes.

A per-run pass rate is a number you cannot act on. The three questions actually
worth asking of a set of runs are different from each other, and the pane answers
all three because answering only the first is what makes a leaderboard decorative:

1. **Who is ahead?** The ranking. Cheap, and the least interesting.
2. **What did this change fix, and what did it break?** The fine-tune question. A
   model that went from 8/12 to 9/12 having fixed three cases and broken two is not
   the same event as one that fixed one and broke none, and the totals cannot tell
   them apart.
3. **Which cases does *everything* fail?** The one this module keeps needing. Three
   separate times a case here was wrong rather than the model — an expectation that
   contradicted the tool's own description, a GSM8K reference containing the whole
   worked solution — and the signature of that is every model failing the same case.
   A universal failure is a prompt to go and read the case, not to go and get a
   better model.

## Comparability is not free

Two runs are only comparable if they asked the same questions. Two things break
that, and both are invisible if you compare on totals:

- **Different case sets.** A run started with a `case_ids` filter attempted fewer
  cases. 5/5 beats 8/12 on percentage and means nothing. So every comparison here
  is computed over the **intersection of cases both runs actually attempted**, and
  the pane says how many were dropped.
- **The suite changed underneath.** Case ids survive an edit, so
  `layout-open-terminal` before and after someone corrected its expectation is the
  same id and a different question. That is why a result carries `case_hash`; where
  two runs disagree about a case's hash, the comparison reports it as **changed**
  rather than as a fix or a regression. Results written before the column existed
  have an empty hash, which is reported as "cannot tell" — not as agreement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.modules.evals import store
from backend.modules.evals.models import CaseResult, EvalRun


@dataclass(slots=True)
class CaseRow:
    """One case, across every run being compared."""

    case_id: str
    #: run id → verdict, for the runs that attempted it. A run missing from here
    #: did not attempt the case; that is not the same as failing it.
    verdicts: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    #: Distinct `case_hash` values seen. More than one means the case itself was
    #: edited between these runs and the column is not a like-for-like comparison.
    hashes: set[str] = field(default_factory=set)

    @property
    def attempted(self) -> int:
        return len(self.verdicts)

    @property
    def passes(self) -> int:
        return sum(1 for v in self.verdicts.values() if v)

    @property
    def edited(self) -> bool:
        """Whether the case changed between the runs shown."""
        known = {h for h in self.hashes if h}
        return len(known) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "caseId": self.case_id,
            "verdicts": self.verdicts,
            "details": self.details,
            "attempted": self.attempted,
            "passes": self.passes,
            "edited": self.edited,
            # A case nothing has ever passed. The module's recurring lesson, as a
            # flag: suspect the case before the models.
            "universalFailure": self.attempted > 1 and self.passes == 0,
            "universalPass": self.attempted > 1 and self.passes == self.attempted,
        }


def build(
    suite_id: str, run_ids: list[str] | None = None, limit: int = 8
) -> dict[str, Any]:
    """The comparison table for a suite.

    Newest runs first, capped: a suite that has been swept forty times produces a
    table nobody can read, and the useful comparison is nearly always between the
    last few.
    """
    suite = store.get_suite(suite_id)
    if suite is None:
        raise ValueError(f"no suite {suite_id!r}")

    runs = [r for r in store.list_runs(suite_id, limit=200) if r.status == "done"]
    if run_ids:
        wanted = set(run_ids)
        runs = [r for r in runs if r.id in wanted]
    runs = runs[:limit]

    rows: dict[str, CaseRow] = {}
    per_run: dict[str, list[CaseResult]] = {}
    for run in runs:
        results = store.list_results(run.id)
        per_run[run.id] = results
        for result in results:
            row = rows.setdefault(result.case_id, CaseRow(case_id=result.case_id))
            row.verdicts[run.id] = result.passed
            row.details[run.id] = result.error or result.detail
            row.hashes.add(result.case_hash)

    # Failure-first, then by how many runs attempted it: the rows you came to read
    # are the ones something is wrong with. A leaderboard sorted alphabetically
    # buries them.
    ordered = sorted(
        rows.values(),
        key=lambda r: (r.passes / r.attempted if r.attempted else 1.0, r.case_id),
    )

    return {
        "suite": suite.model_dump(),
        "runs": [_run_summary(r, per_run[r.id]) for r in runs],
        "cases": [r.to_dict() for r in ordered],
        "universalFailures": [
            r.case_id for r in ordered if r.to_dict()["universalFailure"]
        ],
        "editedCases": [r.case_id for r in ordered if r.edited],
    }


def _run_summary(run: EvalRun, results: list[CaseResult]) -> dict[str, Any]:
    attempted = len(results)
    passed = sum(1 for r in results if r.passed)
    errored = sum(1 for r in results if r.error)
    return {
        "id": run.id,
        "label": run.label or run.model,
        "model": run.model,
        "provider": run.provider,
        "startedAt": run.started_at,
        "attempted": attempted,
        "passed": passed,
        "errored": errored,
        "rate": (passed / attempted) if attempted else 0.0,
        # Averaged over attempted cases rather than reported per case: what it is
        # for is spotting the model that passes by taking four rounds where another
        # took one, and that only shows up in aggregate.
        "avgRounds": (sum(r.rounds for r in results) / attempted) if attempted else 0.0,
        "avgMs": (sum(r.duration_ms for r in results) / attempted)
        if attempted
        else 0.0,
    }


def diff(base_run_id: str, other_run_id: str) -> dict[str, Any]:
    """What changed between two runs, over the cases both attempted.

    The fine-tune question. Deliberately not derived from the totals: a run that
    went 8/12 → 9/12 by fixing three and breaking two is a different event from one
    that fixed one and broke none, and only this can tell them apart.

    Four outcomes, not two, because two of them are not about the model at all: a
    case whose content changed is `changed`, and a case where either run hit a
    provider error is `errored`. Both would otherwise land in `fixed`/`broken` and
    read as the model getting better or worse.
    """
    base = store.get_run(base_run_id)
    other = store.get_run(other_run_id)
    if base is None or other is None:
        raise ValueError("both runs must exist")

    base_results = {r.case_id: r for r in store.list_results(base_run_id)}
    other_results = {r.case_id: r for r in store.list_results(other_run_id)}

    shared = sorted(set(base_results) & set(other_results))
    fixed: list[dict[str, Any]] = []
    broken: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    errored: list[dict[str, Any]] = []
    still_failing: list[str] = []

    for case_id in shared:
        before, after = base_results[case_id], other_results[case_id]
        # Checked before the verdict comparison: if the case itself was edited,
        # "fixed" and "broke" are both the wrong word for what happened.
        if before.case_hash and after.case_hash and before.case_hash != after.case_hash:
            changed.append(
                {
                    "caseId": case_id,
                    "before": before.passed,
                    "after": after.passed,
                    "detail": "the case was edited between these runs",
                }
            )
            continue
        if after.error or before.error:
            # An errored case did not regress; something else broke. A 500 from the
            # model server sitting in the "broke" column is how an infrastructure
            # hiccup gets read as a model getting worse — and this pane exists to
            # stop exactly that kind of misreading. The exporter already refuses to
            # treat an errored case as a lesson for the same reason.
            errored.append({"caseId": case_id, "detail": after.error or before.error})
        elif before.passed and not after.passed:
            broken.append({"caseId": case_id, "detail": after.error or after.detail})
        elif not before.passed and after.passed:
            fixed.append({"caseId": case_id, "detail": after.detail})
        elif not before.passed:
            still_failing.append(case_id)

    return {
        "base": {"id": base.id, "label": base.label or base.model},
        "other": {"id": other.id, "label": other.label or other.model},
        "shared": len(shared),
        # Named so the pane can say what it left out. A comparison that quietly
        # drops half the cases is how "the fine-tune is better" gets said about a
        # run that attempted five of twelve.
        "onlyInBase": sorted(set(base_results) - set(other_results)),
        "onlyInOther": sorted(set(other_results) - set(base_results)),
        "fixed": fixed,
        "broken": broken,
        "changed": changed,
        "errored": errored,
        "stillFailing": still_failing,
        #: True when neither run recorded hashes, so "edited" cannot be ruled out.
        "hashesUnknown": not any(
            r.case_hash for r in (*base_results.values(), *other_results.values())
        ),
    }
