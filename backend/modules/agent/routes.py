import asyncio
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

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
from backend import paths

router = APIRouter(prefix="/agent", tags=["agent"])
logger = logging.getLogger(__name__)


def _config_path() -> Path:
    return paths.data_dir() / "agent-config.json"


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
    # Same placement, same reason: a borrowed peer is reached through a tunnel on
    # an ephemeral loopback port, chosen when the lease was granted. A saved
    # endpoint for this provider can only be stale.
    if info.kind == "peer":
        from backend.modules.network.lease import leases

        borrowed = leases.active_borrow("llama")
        if borrowed is not None and borrowed.endpoint:
            return borrowed.endpoint
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
        hosted=info.hosted,
        has_api_key=P.api_key_for(info) is not None,
        api_key_url=info.api_key_url,
    )


@router.get("/status", response_model=AgentStatus)
async def status() -> AgentStatus:
    config = _load_config()
    # `peer` is listed only while a lease is actually held. It is the one provider
    # nobody can install or fix: without a lease it has no endpoint by design, so
    # probing it would put a permanently-unreachable row in a list whose whole
    # purpose is telling the user what they could switch to.
    infos = [
        info
        for info in P.PROVIDERS.values()
        if info.kind != "peer" or _endpoint_for(info, config)
    ]
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


class ProviderKeyRequest(BaseModel):
    key: str


def _hosted_provider(kind: str) -> P.ProviderInfo:
    info = P.PROVIDERS.get(kind)
    if info is None or not info.hosted:
        raise HTTPException(
            status_code=404, detail=f"{kind} is not a hosted provider with an API key"
        )
    return info


@router.put("/providers/{kind}/key")
def put_provider_key(kind: str, req: ProviderKeyRequest) -> dict[str, bool]:
    """Store the API key for a hosted provider.

    Write-only by design: nothing reads a key back out to the browser, so the
    response says only whether one is now held. The key is Fernet-encrypted in
    `secrets.db` under the provider kind — the same name `providers.api_key_for`
    reads, which is why this route exists instead of the UI posting to the generic
    secrets endpoint and having to know that convention.
    """
    from backend.modules.database.secrets_store import delete_secret, upsert_secret

    info = _hosted_provider(kind)
    value = req.key.strip()
    # An emptied field means "remove it", not "store an empty key" — an empty
    # secret would shadow the environment variable and report as configured.
    if value:
        upsert_secret(info.kind, value)
    else:
        delete_secret(info.kind)
    return {"has_api_key": P.api_key_for(info) is not None}


@router.delete("/providers/{kind}/key")
def delete_provider_key(kind: str) -> dict[str, bool]:
    """Forget a hosted provider's stored key.

    `has_api_key` can still come back true: an environment variable litellm reads
    is not ours to delete, and claiming the provider is now unconfigured when the
    next turn would still succeed is the lie this return value avoids.
    """
    from backend.modules.database.secrets_store import delete_secret

    info = _hosted_provider(kind)
    delete_secret(info.kind)
    return {"has_api_key": P.api_key_for(info) is not None}


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


@router.post("/generate")
async def generate(req: ChatRequest) -> dict[str, str]:
    """One complete, non-streamed generation.

    The streaming ``/chat`` route is the wrong shape for a caller that needs the
    whole answer before it can act on it — the Clubhouse voice agent has to hand
    finished text to TTS, so consuming a token stream only to rejoin it adds a
    round of buffering for nothing.
    """
    config = _load_config()
    if config is None:
        raise HTTPException(
            status_code=409, detail="Agent not configured — finish onboarding"
        )
    info = P.provider_for(config.provider)
    endpoint = config.endpoint or info.default_endpoint
    async with instrumented_client(timeout=30) as client:
        try:
            completion = await P.generate(
                client,
                info,
                endpoint,
                config.model,
                req.prompt,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                system=req.system,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"completion": completion}


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


@router.get("/tool-groups")
def tool_groups() -> dict[str, Any]:
    """Every loadable tool group, with its blurb and tool count.

    The same catalog the model gets from `list_tool_groups`, built by the same
    function, so the picker in the skills editor cannot drift from what
    `use_skill`'s `allowed-tools` will actually resolve against. Groups are how a
    skill activates capability, and until this existed the field was free text —
    you typed a group name and found out whether it was real by watching an agent
    turn fail to load it.

    It reads the **richest** manifest any connected browser has pushed, for the
    reason the evals sweep does: a second window still registering its panes would
    otherwise hand back a shorter catalog than the one the user is looking at.
    With nothing connected the frontend-contributed groups are simply absent —
    backend groups (including every connected MCP server) are still listed, which
    is the honest answer rather than an error.
    """
    from backend.modules.agent.orchestrator import _group_catalog
    from backend.modules.ws import _active_connections

    best: list[dict[str, Any]] = []
    for conn in list(_active_connections):
        tools = getattr(conn, "agent_tools", None) or []
        if len(tools) > len(best):
            best = list(tools)

    class _Shim:
        """`_group_catalog` only ever reads `agent_tools` off the connection."""

        agent_tools = best

    groups = _group_catalog(_Shim())  # type: ignore[arg-type]
    return {"groups": groups, "connected": bool(_active_connections)}


@router.get("/tts")
async def tts(
    text: str,
    voice: str = "en-US-ChristopherNeural",
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> Response:
    """Speak ``text`` as MP3 audio.

    Lazy-imported and 503s rather than 500s when the ``voice`` extra is missing:
    speech synthesis is optional (``uv sync --extra voice``), and every other
    agent route works without it."""
    try:
        from backend.modules.agent.edge_tts_service import edge_tts_service
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Text-to-speech unavailable — install it with `uv sync --extra voice`",
        ) from exc

    audio_bytes = await edge_tts_service.generate_audio(
        text, voice=voice, rate=rate, pitch=pitch
    )
    return Response(content=audio_bytes, media_type="audio/mpeg")


@router.get("/tts/voices")
async def list_tts_voices() -> list[dict[str, Any]]:
    """List available TTS neural voices."""
    try:
        from backend.modules.agent.edge_tts_service import edge_tts_service

        return await edge_tts_service.list_voices()
    except ImportError:
        from backend.modules.agent.edge_tts_service import POPULAR_VOICES

        return POPULAR_VOICES


@router.post("/stt")
async def stt(file: UploadFile, language: str | None = None) -> dict[str, str]:
    """Transcribe an uploaded audio chunk (WebM/Opus) to text.

    Same optionality as :func:`tts` — Whisper pulls in torch, which is far too
    heavy to make every install pay for."""
    from backend.modules.network import borrow

    audio_bytes = await file.read()
    decision = borrow.route("voice")

    if decision.local:
        from backend.modules.agent.stt_service import stt_service

        try:
            return {"text": await stt_service.transcribe(audio_bytes), "ranOn": "local"}
        except Exception as exc:
            logger.warning("STT transcription error: %s", exc)
            return {"text": "", "ranOn": "local"}

    if decision.where == "peer":
        endpoint, decision = await borrow.acquire("voice")
        if endpoint:
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    res = await client.post(
                        f"{endpoint}/api/agent/stt",
                        files={"file": ("audio.webm", audio_bytes)},
                    )
                    res.raise_for_status()
                    # Which node produced this is part of the answer, not a
                    # detail: a borrowed transcript that looks local is how a user
                    # comes to believe their laptop has Whisper installed.
                    return {**res.json(), "ranOn": decision.node_id or "peer"}
            except Exception as exc:  # noqa: BLE001
                logger.info("STT via peer failed: %s", exc)

    # 503 with an install hint, as before -- now also naming the borrow attempt
    # when there was one, so "why did it not use my desktop" has an answer.
    detail = f"Speech-to-text unavailable — {decision.reason}"
    if decision.install:
        detail = f"{detail}. Install it with `{decision.install}`"
    raise HTTPException(status_code=503, detail=detail)
