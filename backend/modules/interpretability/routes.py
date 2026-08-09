"""HTTP surface for the interpretability pane, mounted at `/api/interpretability`.

The live path is the `interpretability` `/ws` channel (recorder pushes each round as
it happens); these routes cover what a socket can't: back-filling the pane on open,
and asking the provider what the loaded model actually is.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from backend.modules.interpretability import architecture as arch
from backend.modules.interpretability import recorder
from backend.modules.interpretability.models import (
    ModelArchitecture,
    ModelInfoResponse,
    TurnListResponse,
    TurnSnapshot,
)
from backend.modules.interpretability.tokenizer import (
    context_length_from_show,
    repo_for_model,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interpretability", tags=["interpretability"])


@router.get("/turns", response_model=TurnListResponse)
def list_turns(limit: int = recorder.MAX_TURNS) -> TurnListResponse:
    """Recent captured turns, newest first — what the pane loads on open."""
    return TurnListResponse(turns=recorder.recent_turns(limit))


@router.get("/turns/{turn_id}", response_model=TurnSnapshot)
def get_turn(turn_id: str) -> TurnSnapshot:
    turn = recorder.get_turn(turn_id)
    if turn is None:
        raise HTTPException(status_code=404, detail=f"No captured turn {turn_id!r}")
    return turn


@router.delete("/turns", response_model=TurnListResponse)
def clear_turns() -> TurnListResponse:
    recorder.clear()
    return TurnListResponse(turns=[])


@router.get("/model", response_model=ModelInfoResponse)
async def model_info() -> ModelInfoResponse:
    """What the provider reports about the loaded model — crucially its *true*
    context length, which is the denominator for the pane's budget bar.

    Ollama-only: `/api/show` has no OpenAI-dialect equivalent, so LM Studio and vLLM
    return an `error` the pane renders as "context length unknown" rather than
    guessing a number the whole budget view would then be wrong about.
    """
    from backend.modules.agent import routes as agent_routes
    from backend.modules.agent import providers as P

    config = agent_routes._load_config()
    if config is None:
        return ModelInfoResponse(error="No agent provider configured")
    info = P.provider_for(config.provider)
    model = config.model or ""
    if info.dialect != "ollama":
        return ModelInfoResponse(
            model=model,
            provider=config.provider,
            error=f"{info.label} exposes no model-metadata endpoint",
        )
    endpoint = agent_routes._endpoint_for(info, config)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.post(
                f"{endpoint.rstrip('/')}/api/show", json={"model": model}
            )
            res.raise_for_status()
            data: dict[str, Any] = res.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("interpretability: /api/show failed for %s (%s)", model, exc)
        return ModelInfoResponse(
            model=model, provider=config.provider, error=f"Could not reach {info.label}"
        )
    details = data.get("details") or {}
    return ModelInfoResponse(
        model=model,
        provider=config.provider,
        contextLength=context_length_from_show(data),
        template=data.get("template"),
        parameters=details.get("parameter_size"),
        family=details.get("family"),
    )


@router.get("/architecture", response_model=ModelArchitecture)
async def model_architecture() -> ModelArchitecture:
    """The loaded model's structure, for the diagram beside the context view.

    Two sources, tried in confidence order:

    1. **Ollama `/api/show`** — GGUF metadata written from the actual weights the
       server loaded. Authoritative.
    2. **A Hugging Face `config.json`** — the repo resolved from the model id or
       pinned via `interpretability.modelRepo`. Describes the architecture, though
       strictly it describes the *repo*, which is why `source` is reported.

    OpenAI-dialect servers (LM Studio, vLLM) expose no architecture endpoint at
    all, so for those path 2 is the only option — and it needs a resolvable repo.
    """
    from backend.modules.agent import providers as P
    from backend.modules.agent import routes as agent_routes
    from backend.modules.agent.orchestrator import _tokenizer_repo

    config = agent_routes._load_config()
    if config is None:
        return ModelArchitecture(error="No agent provider configured")
    info = P.provider_for(config.provider)
    model = config.model or ""

    if info.dialect == "ollama":
        endpoint = agent_routes._endpoint_for(info, config)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.post(
                    f"{endpoint.rstrip('/')}/api/show", json={"model": model}
                )
                res.raise_for_status()
                data: dict[str, Any] = res.json()
            primary = arch.from_ollama_show(model, endpoint, data)
            # GGUF metadata is dense but not complete — Gemma 4's omits KV-head
            # count and vocab size entirely. Fill *only* those gaps from the repo
            # the GGUF itself names as its base model (or the user's pin), never
            # overriding anything the weights already stated.
            repo = arch.declared_repo(data) or _tokenizer_repo().strip()
            if repo:
                cfg = await arch.fetch_hf_config(repo)
                if cfg is not None:
                    filled = arch.fill_gaps(
                        primary, arch.from_hf_config(model, repo, cfg)
                    )
                    if filled:
                        primary.notes.append(
                            f"Gaps in the GGUF metadata filled from {repo}: "
                            f"{', '.join(filled)}."
                        )
            return primary
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("interpretability: /api/show failed for %s (%s)", model, exc)
            # Fall through to Hugging Face rather than giving up — a reachable repo
            # still describes the architecture even when the server is unhelpful.

    repo, _source = repo_for_model(model, _tokenizer_repo())
    if not repo:
        return ModelArchitecture(
            model=model,
            error=(
                f"{info.label} exposes no architecture metadata, and '{model}' does "
                "not resolve to a Hugging Face repo. Set interpretability.modelRepo "
                "to the repo for this model."
            ),
        )
    cfg = await arch.fetch_hf_config(repo)
    if cfg is None:
        return ModelArchitecture(
            model=model,
            error=(
                f"Could not read config.json from '{repo}'. If the repo is gated, "
                "connect Hugging Face; if it's the wrong repo, set "
                "interpretability.modelRepo."
            ),
        )
    return arch.from_hf_config(model, repo, cfg)
