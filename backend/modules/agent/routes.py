import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.modules.agent.models import (
    DEFAULT_OLLAMA_ENDPOINT,
    AgentConfig,
    AgentStatus,
    ChatRequest,
    PullRequest,
)
from backend.modules.telemetry.instrument import instrumented_client

router = APIRouter(prefix="/agent", tags=["agent"])


def _config_path() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data")) / "agent-config.json"


def _load_config() -> AgentConfig | None:
    path = _config_path()
    if path.is_file():
        return AgentConfig.model_validate_json(path.read_text())
    return None


def _endpoint(config: AgentConfig | None) -> str:
    if config:
        return config.endpoint
    return os.environ.get("HORRIBLE_OLLAMA_URL", DEFAULT_OLLAMA_ENDPOINT)


@router.get("/status", response_model=AgentStatus)
async def status() -> AgentStatus:
    config = _load_config()
    endpoint = _endpoint(config)
    reachable = False
    models: list[str] = []
    try:
        async with instrumented_client(timeout=2) as client:
            res = await client.get(f"{endpoint}/api/tags")
            res.raise_for_status()
            reachable = True
            models = [m["name"] for m in res.json().get("models", [])]
    except httpx.HTTPError:
        pass
    return AgentStatus(
        ollama_reachable=reachable,
        configured=config is not None,
        model=config.model if config else None,
        endpoint=endpoint,
        available_models=models,
    )


@router.put("/config", response_model=AgentConfig)
def put_config(config: AgentConfig) -> AgentConfig:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json())
    return config


async def _proxy_ndjson(url: str, payload: dict[str, object]) -> AsyncIterator[str]:
    """Stream Ollama's NDJSON responses through to the client line by line."""
    async with instrumented_client(timeout=None) as client:
        async with client.stream("POST", url, json=payload) as res:
            async for line in res.aiter_lines():
                if line:
                    yield line + "\n"


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    config = _load_config()
    if config is None:
        raise HTTPException(
            status_code=409, detail="Agent not configured — finish onboarding"
        )
    return StreamingResponse(
        _proxy_ndjson(
            f"{config.endpoint}/api/generate",
            {"model": config.model, "prompt": req.prompt, "stream": True},
        ),
        media_type="application/x-ndjson",
    )


@router.post("/pull")
async def pull(req: PullRequest) -> StreamingResponse:
    endpoint = _endpoint(_load_config())
    return StreamingResponse(
        _proxy_ndjson(f"{endpoint}/api/pull", {"model": req.model, "stream": True}),
        media_type="application/x-ndjson",
    )
