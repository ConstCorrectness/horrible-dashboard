"""Publishing a trajectory to the agent commons — as a digest of its shape.

A friend pulling a run (`fabric.py`) is one person you chose. The commons is
strangers, re-served by an index you do not run, and **a federated index has no
recall**: unpublishing stops future fetches and reaches no copy already taken. So
what leaves is reduced to what makes a run useful as a public dataset and little else.

## What a digest keeps, and what it drops

Kept: the harness fingerprint, model and provider kind, tool names, the tool-call
sequence by name with each step's `ok`/`duration_ms`/`gated`/`round`, rounds,
status/outcome/reward and token counts.

Dropped: every `args`/`result`/`content` payload, `turn_id`, `external_id`,
`person_id`, `parent_run_id`, `meta`, labels, and the system prompt (the fingerprint
stands in for it). `goal` is dropped unless the publisher opts in for this run, and
even then it is scrubbed and clipped.

## Two things that are not anonymous, said rather than hidden

- The digest is **signed with this node's key**, so it names this node. That is the
  price of verification: an unsigned digest could be forged under anyone's name and
  could not be withdrawn by its publisher.
- **Tool names are shape, and can still say something** — an MCP server id is
  whatever its owner called it (`mcp-acme-jira.search`). The preview shows the exact
  digest before anything is sent, which is the only honest defence.

## The confirmation is bound to the content

`publish` takes a `confirm` code that is the first characters of the digest's content
hash, which the pane only has by fetching the preview. So a confirmation cannot be
replayed onto different content: if the run changed after the preview — a label, a
step still arriving — the code no longer matches and nothing is sent.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from backend.modules.network.models import (
    CommonsDigestStep,
    CommonsTrajectoryDigest,
    canonical_digest_bytes,
    digest_content_id,
)
from backend.modules.trajectories import outbound, store

logger = logging.getLogger("trajectories")

#: How much of the goal an opted-in digest carries.
GOAL_MAX = 500
#: Characters of the content hash the publisher types to confirm.
CONFIRM_LEN = 6


class PublishRefused(Exception):
    """This run cannot be published, and the message says why."""


def build_digest(run_id: str, *, include_goal: bool = False) -> CommonsTrajectoryDigest:
    """The digest for a run, unsigned and unstamped — exactly what the preview shows."""
    from backend.modules.network.hub import peer_hub

    run = store.get_run(run_id)
    if run is None:
        raise PublishRefused("no such run")
    dataset = store.get_dataset(run.dataset_id)
    # A friend's run is not yours to publish, the same rule that stops sharing a
    # `peer-…` dataset onward.
    if run.source == "peer" or (dataset is not None and dataset.source_kind == "peer"):
        raise PublishRefused("this run came from a friend and is not yours to publish")
    if run.status == "running":
        raise PublishRefused("the run is still in progress")

    harness = store.get_harness(run.harness) if run.harness else None
    goal: str | None = None
    if include_goal and run.goal:
        text = outbound.scrub_text(run.goal)
        goal = text if len(text) <= GOAL_MAX else text[:GOAL_MAX] + "…"

    signer = peer_hub.signer
    digest = CommonsTrajectoryDigest(
        node_id=signer.node_id,
        public_key=signer.public_key,
        harness_fingerprint=run.harness or "",
        model=run.model,
        provider=run.provider,
        tool_names=sorted(harness.tool_names) if harness else [],
        goal=goal,
        status=run.status,
        outcome=run.outcome,
        reward=run.reward,
        rounds=run.rounds,
        steps=[
            CommonsDigestStep(
                seq=step.seq,
                kind=step.kind,
                round=step.round,
                name=(step.name or "") if step.kind == "action" else "",
                ok=step.ok,
                duration_ms=step.duration_ms,
                gated=step.gated,
            )
            for step in run.step_list
        ],
        tokens_in=run.tokens_in,
        tokens_out=run.tokens_out,
        duration_ms=run.duration_ms,
    )
    digest.digest_id = digest_content_id(digest)
    return digest


def confirm_code(digest: CommonsTrajectoryDigest) -> str:
    return digest.digest_id[:CONFIRM_LEN]


def preview(run_id: str, *, include_goal: bool = False) -> dict[str, Any]:
    digest = build_digest(run_id, include_goal=include_goal)
    return {
        "digest": digest.model_dump(exclude={"sig", "published_at"}),
        "confirm": confirm_code(digest),
    }


async def publish(run_id: str, *, include_goal: bool, confirm: str) -> dict[str, Any]:
    """Sign and send a run's digest, once the typed code matches its content."""
    from backend.modules.network.commons import commons_client
    from backend.modules.network.hub import peer_hub

    digest = build_digest(run_id, include_goal=include_goal)
    if (confirm or "").strip().lower() != confirm_code(digest):
        raise PublishRefused(
            "the confirmation does not match this digest — the run may have changed "
            "since the preview; review it again"
        )
    if not commons_client.connected:
        raise PublishRefused("not connected to a commons index")

    digest.published_at = time.time()
    digest.sig = peer_hub.signer.sign(canonical_digest_bytes(digest))
    try:
        reply = await commons_client.publish_trajectory(digest.model_dump())
    except (ConnectionError, TimeoutError) as exc:
        raise PublishRefused(
            f"the index did not take it: {exc or 'no answer'}"
        ) from exc
    except RuntimeError as exc:
        raise PublishRefused(f"the index refused it: {exc}") from exc
    logger.info("trajectories: published run %s as %s", run_id, digest.digest_id)
    return {"digest_id": digest.digest_id, "duplicate": bool(reply.get("duplicate"))}
