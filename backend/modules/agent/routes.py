import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.modules.agent import providers as P
from backend.modules.agent.models import (
    AgentConfig,
    AgentStatus,
    ChatRequest,
    CompleteRequest,
    DetectedProvider,
    PullRequest,
    RosterAgent,
    RosterResponse,
    VllmSpawnRequest,
)
from backend.modules.agent.vllm import vllm_manager
from backend.modules.telemetry.instrument import instrumented_client, tee_stream

router = APIRouter(prefix="/agent", tags=["agent"])


def _config_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "agent-config.json"


def _load_config() -> AgentConfig | None:
    path = _config_path()
    if path.is_file():
        return AgentConfig.model_validate_json(path.read_text())
    return None


def _endpoint_for(info: P.ProviderInfo, config: AgentConfig | None) -> str:
    """The endpoint to probe for a provider: the saved one if it's the configured
    provider, else the provider default. ``HORRIBLE_OLLAMA_URL`` still overrides
    the Ollama default for back-compat, and a spawned vLLM advertises its port."""
    # Checked BEFORE the saved endpoint, unlike every other provider: a spawned
    # llama-server picks an ephemeral port when the default one is taken, so a
    # saved `:8080` would point at nothing while the real server sits elsewhere.
    # We are the ones who started it — the live manager is the authority.
    if info.kind == "llamacpp":
        from backend.modules.llamacpp.server import llama_manager

        if llama_manager.running():
            return llama_manager.endpoint
    if config and config.provider == info.kind and config.endpoint:
        return config.endpoint
    if info.kind == "ollama":
        return os.environ.get("HORRIBLE_OLLAMA_URL", info.default_endpoint)
    if info.kind == "vllm" and vllm_manager.running():
        return vllm_manager.endpoint
    return info.default_endpoint


async def _probe(
    client: httpx.AsyncClient, info: P.ProviderInfo, endpoint: str
) -> DetectedProvider:
    reachable = False
    models: list[str] = []
    try:
        models = await P.list_models(client, info, endpoint)
        reachable = True
    except httpx.HTTPError:
        pass
    return DetectedProvider(
        kind=info.kind,
        label=info.label,
        endpoint=endpoint,
        reachable=reachable,
        models=models,
        can_pull=info.can_pull,
        can_spawn=info.can_spawn,
        install_url=info.install_url,
    )


@router.get("/status", response_model=AgentStatus)
async def status() -> AgentStatus:
    config = _load_config()
    infos = list(P.PROVIDERS.values())
    async with instrumented_client(timeout=2) as client:
        detected = await asyncio.gather(
            *(_probe(client, info, _endpoint_for(info, config)) for info in infos)
        )
    active_kind = config.provider if config else P.DEFAULT_PROVIDER
    active = next((d for d in detected if d.kind == active_kind), detected[0])
    return AgentStatus(
        configured=config is not None,
        provider=config.provider if config else None,
        model=config.model if config else None,
        endpoint=active.endpoint,
        reachable=active.reachable,
        available_models=active.models,
        providers=list(detected),
        vllm=vllm_manager.status(),
    )


@router.put("/config", response_model=AgentConfig)
def put_config(config: AgentConfig) -> AgentConfig:
    if config.provider not in P.PROVIDERS:
        raise HTTPException(
            status_code=422, detail=f"Unknown provider: {config.provider}"
        )
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json())
    return config


async def _proxy_ndjson(url: str, payload: dict[str, object]) -> AsyncIterator[str]:
    """Stream a provider's NDJSON responses through to the client line by line."""
    async with instrumented_client(timeout=None) as client:
        async with client.stream("POST", url, json=payload) as res:
            async for line in tee_stream(res, res.aiter_lines()):
                if line:
                    yield line + "\n"


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    config = _load_config()
    if config is None:
        raise HTTPException(
            status_code=409, detail="Agent not configured — finish onboarding"
        )
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint

    async def gen() -> AsyncIterator[str]:
        async with instrumented_client(timeout=None) as client:
            async for line in P.generate_stream(
                client, info, endpoint, config.model, req.prompt
            ):
                yield line

    return StreamingResponse(gen(), media_type="application/x-ndjson")


_COMPLETE_SYSTEM = (
    "You are an inline code/text completion engine. Continue the text at the "
    "<CURSOR> marker so it fits naturally between the text before and after it. "
    "Reply with ONLY the raw text to insert — no explanation, no markdown fences, "
    "no repetition of the surrounding text. Keep it short (finish the current line "
    "or statement). When language-server context is provided, prefer the symbols it "
    "lists so the completion actually resolves."
)


def _grounding(req: CompleteRequest) -> str:
    """The LSP-context block for the prompt: the in-scope completion candidates and
    the type/signature at the cursor. Empty when the client sent no grounding."""
    parts: list[str] = []
    if req.completions:
        # Cap the list — a long candidate set bloats the prompt without helping.
        symbols = ", ".join(req.completions[:40])
        parts.append(f"Symbols in scope at the cursor: {symbols}")
    if req.hover:
        parts.append(f"Type/signature at the cursor:\n{req.hover}")
    return ("\n\n" + "\n".join(parts)) if parts else ""


@router.post("/complete")
async def complete(req: CompleteRequest) -> dict[str, str]:
    """Return one short fill-in completion for the editor's inline autosuggest."""
    config = _load_config()
    if config is None:
        raise HTTPException(
            status_code=409, detail="Agent not configured — finish onboarding"
        )
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint
    lang = f" The language is {req.language}." if req.language else ""
    prompt = (
        f"{_COMPLETE_SYSTEM}{lang}{_grounding(req)}\n\n"
        f"{req.prefix}<CURSOR>{req.suffix}\n\n"
        "Text to insert at <CURSOR>:"
    )
    async with instrumented_client(timeout=20) as client:
        try:
            completion = await P.generate(client, info, endpoint, config.model, prompt)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"completion": completion}


@router.post("/pull")
async def pull(req: PullRequest) -> StreamingResponse:
    config = _load_config()
    info = P.provider_for(config.provider if config else None)
    if not info.can_pull:
        raise HTTPException(
            status_code=400, detail=f"{info.label} does not support pulling models"
        )
    endpoint = _endpoint_for(info, config)
    return StreamingResponse(
        _proxy_ndjson(f"{endpoint}/api/pull", {"model": req.model, "stream": True}),
        media_type="application/x-ndjson",
    )


@router.get("/vllm/status")
def vllm_status() -> dict[str, Any]:
    return vllm_manager.status()


@router.post("/vllm/spawn")
def vllm_spawn(req: VllmSpawnRequest) -> dict[str, Any]:
    try:
        return vllm_manager.spawn(req.model, req.port)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/vllm/stop")
def vllm_stop() -> dict[str, Any]:
    return vllm_manager.stop()


@router.get("/roster", response_model=RosterResponse)
def roster() -> RosterResponse:
    """The agent roster: built-ins plus plugin-contributed agents. The chat
    widget's picker and the settings page read this."""
    from backend.modules.agent.roster import list_agents

    return RosterResponse(
        agents=[
            RosterAgent(
                id=spec.id,
                name=spec.name,
                description=spec.description,
                tool_groups=spec.tool_groups,
                default_mode=spec.default_mode,
            )
            for spec in list_agents()
        ]
    )
