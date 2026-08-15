"""Per-loadout model configuration + the node-side API key store.

The model is **part of the loadout** — bring-your-own-model is part of the skill
expression, and the ladder records what played (`model_label`). A `ModelConfig`
names a provider (anthropic/openai/ollama), a model, an optional endpoint, and an
optional **key name** resolved against the local key store.

Key store trust model: keys live in `.data/games_keys.json` on the player's own
node — like the games JWT, they are **never returned to the browser or sent over
any wire**; routes expose key *names* only (see routes.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from backend import paths

Provider = Literal["anthropic", "openai", "ollama"]

DEFAULT_ENDPOINTS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com",
    "ollama": "http://localhost:11434",
}


class ModelConfig(BaseModel):
    provider: Provider
    model: str
    endpoint: str | None = None
    api_key_name: str | None = None

    def resolved_endpoint(self) -> str:
        return self.endpoint or DEFAULT_ENDPOINTS[self.provider]


def parse_model(raw: Any) -> ModelConfig | None:
    """A loadout's stored `model` dict → ModelConfig (None when absent/invalid)."""
    if not isinstance(raw, dict) or not raw.get("model"):
        return None
    try:
        return ModelConfig(**raw)
    except Exception:
        return None


def model_label(config: ModelConfig | None) -> str | None:
    """What the ladder/replay records, e.g. `anthropic/claude-sonnet-5` or
    `ollama/llama3 (local)`."""
    if config is None:
        return None
    suffix = " (local)" if is_local(config) else ""
    return f"{config.provider}/{config.model}{suffix}"


def is_local(config: ModelConfig) -> bool:
    """Local = ollama, or any provider pointed at a localhost endpoint — what a
    `model_class: "local"` ruleset asks both sides to declare."""
    if config.provider == "ollama":
        return True
    endpoint = config.resolved_endpoint()
    return "localhost" in endpoint or "127.0.0.1" in endpoint


# ---- key store ---------------------------------------------------------------


def _keys_path() -> Path:
    return paths.data_dir() / "games_keys.json"


def _read_keys() -> dict[str, str]:
    path = _keys_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}
    except ValueError:
        return {}


def set_key(name: str, value: str) -> None:
    keys = _read_keys()
    keys[name] = value
    path = _keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2), encoding="utf-8")


def delete_key(name: str) -> None:
    keys = _read_keys()
    if name in keys:
        del keys[name]
        _keys_path().write_text(json.dumps(keys, indent=2), encoding="utf-8")


def list_key_names() -> list[str]:
    """Names only — values never leave this module except into request headers."""
    return sorted(_read_keys())


def get_key(name: str | None) -> str | None:
    if not name:
        return None
    return _read_keys().get(name)
