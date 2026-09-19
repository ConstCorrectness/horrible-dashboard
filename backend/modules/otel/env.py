"""OTLP environment for processes the dashboard spawns — zero-config tracing.

Every OTel SDK (and Pydantic AI, LangChain's OpenInference instrumentor, the OpenAI
Agents SDK bridge, ADK) reads the standard `OTEL_*` variables. Setting them at kernel
spawn means an agent written in a notebook cell exports to this node with no
endpoint in the code at all — the `trackers` precedent (`training/kernels.py`):
configuration arrives through the environment, never through the notebook.

Two rules:

- **The user's own configuration wins.** If the backend was started with an OTLP
  endpoint already set, the user is routing traces somewhere on purpose, and
  silently redirecting their kernels here would be a surprise with no error.
- The endpoint is built from `server_port`, never a literal 8000 — the node may be
  on 8100 (Hyper-V port exclusions), and a hardcoded port is an exporter retrying
  into a closed socket in the background forever.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

logger = logging.getLogger("otel")

_USER_ENDPOINT_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)


def _enabled() -> bool:
    try:
        from backend.modules.settings import get_value

        return bool(get_value("otel.injectEnv", True))
    except Exception:  # noqa: BLE001 — a settings failure must not block a kernel
        return True


def _attr(value: str) -> str:
    """`OTEL_RESOURCE_ATTRIBUTES` is `k=v,k=v` with values percent-encoded."""
    return quote(value, safe="")


def otlp_env(*, service: str, dataset: str) -> dict[str, str]:
    """Variables to merge over a child process's environment. Empty when disabled
    or when the user already configured an exporter."""
    if not _enabled() or any(os.environ.get(v) for v in _USER_ENDPOINT_VARS):
        return {}
    from backend.modules.otel.mapping import DATASET_ATTR
    from backend.modules.otel.routes import local_endpoint

    attrs = f"{DATASET_ATTR}={_attr(dataset)}"
    existing = os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").strip().strip(",")
    return {
        "OTEL_EXPORTER_OTLP_ENDPOINT": local_endpoint(),
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        # Only an explicit user OTEL_SERVICE_NAME outranks the notebook's name.
        "OTEL_SERVICE_NAME": os.environ.get("OTEL_SERVICE_NAME") or service,
        "OTEL_RESOURCE_ATTRIBUTES": f"{existing},{attrs}" if existing else attrs,
        # Metrics and logs have no receiver here; without these the SDKs' default
        # exporters retry into 404s and log a warning every few seconds.
        "OTEL_METRICS_EXPORTER": os.environ.get("OTEL_METRICS_EXPORTER") or "none",
        "OTEL_LOGS_EXPORTER": os.environ.get("OTEL_LOGS_EXPORTER") or "none",
    }
