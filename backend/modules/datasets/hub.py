"""Peeking at a Hub dataset without downloading it.

Every mistake a benchmark case has made so far has been the same mistake in a
different costume: **the case was wrong and the model got the blame.**
`input_template` named a column the dataset does not have; `target_column` pointed
at a field holding the worked solution rather than the answer. Both scored zero and
both looked exactly like a bad model. The same is true of a fine-tuning recipe: a
`text_field` naming a column that isn't there is a wasted GPU hour that reads as a
bad dataset.

The fix is to stop asking people to guess. `first-rows` from Hugging Face's
datasets-server returns the column names and a few real rows for any public dataset
without downloading a byte of it, which is enough to populate the field pickers, to
run format detection against real values, and to show *before the run* precisely
what would be compared or trained on.

It is a fixed vendor host, so it goes over plain `httpx` like the search module's
Tavily/Brave calls rather than through `_fetch_guarded`, which is for URLs that
came from somewhere untrusted. The dataset id does reach the query string, so it is
sent as a parameter and never interpolated into the path.

This lived in `evals` first, because that is where the wasted run happened first.
It is here now because a *dataset* is not an eval concept, and the recipe form
needs exactly the same three calls. `evals.datasets` re-exports it, so nothing that
imported it from there had to change.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FIRST_ROWS = "https://datasets-server.huggingface.co/first-rows"
SPLITS = "https://datasets-server.huggingface.co/splits"

#: Short: this is a form assistant, and a picker that hangs for thirty seconds is
#: worse than one that says "could not reach the Hub" and lets you type the column
#: name yourself.
TIMEOUT_S = 12.0


class PeekError(RuntimeError):
    """The Hub could not answer. Carries something worth showing the user."""


async def splits(dataset: str) -> list[dict[str, str]]:
    """Every (config, split) this dataset offers.

    Worth a call of its own because `config` is the field people miss: `gsm8k` has
    no default config, and asking for one without it fails in a way that reads as
    "the dataset is broken".
    """
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            response = await client.get(SPLITS, params={"dataset": dataset})
        except httpx.HTTPError as exc:
            raise PeekError(f"could not reach the Hub: {exc}") from exc
    if response.status_code == 404:
        raise PeekError(f"no dataset {dataset!r} on the Hub, or it is gated")
    if response.status_code >= 400:
        raise PeekError(_detail(response, f"the Hub returned {response.status_code}"))
    return [
        {"config": str(s.get("config") or ""), "split": str(s.get("split") or "")}
        for s in (response.json().get("splits") or [])
    ]


async def first_rows(
    dataset: str, config: str = "", split: str = "train", limit: int = 3
) -> dict[str, Any]:
    """Column names and a few real rows.

    The rows are what make the editor's comparison preview honest: it renders the
    template and applies the regexes to an actual row rather than to an example
    somebody wrote in a docstring.
    """
    params = {"dataset": dataset, "split": split}
    if config:
        params["config"] = config
    else:
        # The server requires a config. Resolving it here rather than making the
        # caller do it is the whole point — "which config?" is not a question the
        # person filling in the form can answer yet.
        found = await splits(dataset)
        if not found:
            raise PeekError(f"{dataset!r} reports no splits")
        params["config"] = found[0]["config"]
        if not any(
            s["split"] == split for s in found if s["config"] == params["config"]
        ):
            params["split"] = found[0]["split"]

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            response = await client.get(FIRST_ROWS, params=params)
        except httpx.HTTPError as exc:
            raise PeekError(f"could not reach the Hub: {exc}") from exc

    if response.status_code >= 400:
        raise PeekError(_detail(response, f"the Hub returned {response.status_code}"))

    payload = response.json()
    columns = [
        str(f.get("name") or "")
        for f in (payload.get("features") or [])
        if f.get("name")
    ]
    rows = [r.get("row") or {} for r in (payload.get("rows") or [])][:limit]
    return {
        "dataset": dataset,
        "config": params["config"],
        "split": params["split"],
        "columns": columns,
        "rows": rows,
    }


def _detail(response: httpx.Response, fallback: str) -> str:
    """The Hub's own error text when there is one.

    Worth digging out: its messages name the actual problem ("Config name is
    missing", "Dataset is gated"), and replacing them with a status code would
    throw away the only useful part of the response.
    """
    try:
        body = response.json()
    except ValueError:
        return fallback
    for key in ("error", "detail", "message"):
        if body.get(key):
            return str(body[key])
    return fallback
