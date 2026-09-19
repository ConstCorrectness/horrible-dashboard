"""Export to an external OTLP backend, and the `otel` connector that configures it.

Phoenix, Langfuse, Jaeger, Tempo and Honeycomb all accept OTLP/HTTP. Pointing the
node at one takes an endpoint and, for the hosted ones, an auth header — and an
auth header is a credential, so it lives in the connectors' encrypted store and
never in a setting (`GET /api/settings` hands the whole bag to the browser). The
`trackers` connector (`training/trackers.py`) is the precedent followed field for
field, including contributing **no agent tools**: where your traces go is a
property of the node, not something the agent should change behind your back.

The standard `OTEL_EXPORTER_OTLP_*` variables win over the stored values, the way
`WANDB_API_KEY` wins there — a container or CI box configures it the usual way.

Two legs, both optional:

- **The node's own spans** go through an OTLP `BatchSpanProcessor` swapped into
  `tracing.export_slot()`. Content is already absent unless `otel.captureContent`
  is on (and redacted when it is), because it is decided where the span is made.
- **Received spans** (`otel.forwardReceived`, default off) are forwarded as the
  original request bytes. They are other programs' spans, which *their* authors
  decided the content of — re-encoding them would only lose fidelity.

Exporting to this node's own `/api/otel` is refused: every local span would come
back as a "received" one and overwrite its own row.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import unquote, urlparse

from backend.sdk.types import Connector, ConnectorAccount, ConnectorStatus

logger = logging.getLogger("otel")

CONNECTOR_ID = "otel"

_ENDPOINT = "otel:export_endpoint"
_HEADERS = "otel:export_headers"


def _get(name: str) -> str:
    from backend.modules.database.secrets_store import get_secret_or_none

    try:
        return (get_secret_or_none(name) or "").strip()
    except Exception as exc:  # noqa: BLE001 — an unreadable store is "not configured"
        logger.info("otel: could not read %s (%s)", name, exc)
        return ""


def _put(name: str, value: str) -> None:
    from backend.modules.database.secrets_store import delete_secret, upsert_secret

    if value:
        upsert_secret(name, value)
    else:
        delete_secret(name)


def traces_url(base: str) -> str:
    """`OTEL_EXPORTER_OTLP_ENDPOINT` semantics: the SDK appends `/v1/traces`.
    A URL that already names the traces path is taken as-is."""
    base = base.strip().rstrip("/")
    return base if base.endswith("/v1/traces") else f"{base}/v1/traces"


def parse_headers(raw: str) -> dict[str, str]:
    """`OTEL_EXPORTER_OTLP_HEADERS` format: `k=v,k=v`, values percent-encoded."""
    out: dict[str, str] = {}
    for item in raw.split(","):
        key, sep, value = item.partition("=")
        if sep and key.strip():
            out[key.strip()] = unquote(value.strip())
    return out


def config() -> tuple[str, dict[str, str]] | None:
    """(traces URL, headers), or None when nothing is configured."""
    env_url = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
    env_base = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if env_url or env_base:
        url = env_url or traces_url(env_base)
        headers = parse_headers(
            os.environ.get("OTEL_EXPORTER_OTLP_TRACES_HEADERS")
            or os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
        )
        return url, headers
    stored = _get(_ENDPOINT)
    if not stored:
        return None
    return traces_url(stored), parse_headers(_get(_HEADERS))


def _is_self(url: str) -> bool:
    return urlparse(url).path.startswith("/api/otel")


def configure() -> bool:
    """(Re)attach the exporter for the node's own spans. Returns whether one is
    attached. Safe to call repeatedly — the old processor is flushed and shut
    down on a thread so a slow collector cannot stall the caller."""
    from backend.modules.otel import tracing

    slot = tracing.export_slot()
    old = slot.inner
    slot.inner = None
    if old is not None:
        import threading

        threading.Thread(target=old.shutdown, daemon=True).start()
    cfg = config()
    if cfg is None:
        return False
    url, headers = cfg
    if _is_self(url):
        logger.warning("otel: refusing to export to this node's own receiver (%s)", url)
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        slot.inner = BatchSpanProcessor(OTLPSpanExporter(endpoint=url, headers=headers))
    except Exception:  # noqa: BLE001
        logger.warning("otel: could not attach exporter for %s", url, exc_info=True)
        return False
    return True


def _forward_enabled() -> bool:
    try:
        from backend.modules.settings import get_value

        return bool(get_value("otel.forwardReceived", False))
    except Exception:  # noqa: BLE001
        return False


async def forward_received(
    body: bytes, content_type: str | None, encoding: str | None
) -> None:
    """Relay an accepted OTLP request to the external backend, byte for byte.

    Plain httpx rather than `instrumented_client` on purpose: the forwarded
    request carries the exporter's auth header, and the I/O ring captures
    outbound headers raw."""
    if not _forward_enabled():
        return
    cfg = config()
    if cfg is None or _is_self(cfg[0]):
        return
    url, headers = cfg
    send = {**headers, "content-type": content_type or "application/x-protobuf"}
    if encoding:
        send["content-encoding"] = encoding
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, content=body, headers=send)
            if response.status_code >= 400:
                logger.info("otel: forward to %s → %s", url, response.status_code)
    except Exception as exc:  # noqa: BLE001 — forwarding is best-effort
        logger.info("otel: forward to %s failed: %s", url, exc)


# --- the connector ------------------------------------------------------------


def _form_step() -> dict[str, Any]:
    env_note = (
        " (OTEL_EXPORTER_OTLP_* in the backend's environment currently overrides this.)"
        if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        else ""
    )
    return {
        "step": "form",
        "fields": [
            {
                "name": "endpoint",
                "label": "OTLP/HTTP endpoint",
                # Secret because a collector URL can carry basic-auth userinfo.
                "secret": True,
                "value": "",
                "help": (
                    "Configured. Leave blank to keep it." + env_note
                    if _get(_ENDPOINT)
                    else "Base URL, as for OTEL_EXPORTER_OTLP_ENDPOINT — e.g. "
                    "http://localhost:4318 (Jaeger, Tempo), http://localhost:6006 "
                    "(Phoenix). /v1/traces is appended." + env_note
                ),
            },
            {
                "name": "headers",
                "label": "Headers",
                "secret": True,
                "value": "",
                "help": (
                    "Configured. Leave blank to keep it."
                    if _get(_HEADERS)
                    else "Optional, OTEL_EXPORTER_OTLP_HEADERS format: "
                    "Authorization=Bearer%20…,x-honeycomb-team=…"
                ),
            },
        ],
    }


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    return _form_step()


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    """Blank means "keep", never "clear" — the form cannot prefill a secret."""
    endpoint = (values.get("endpoint") or "").strip()
    headers = (values.get("headers") or "").strip()
    if endpoint:
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return {"error": "The endpoint must be an http(s) URL."}
        if _is_self(traces_url(endpoint)):
            return {
                "error": "That is this node's own receiver — exporting there would "
                "loop every span back in."
            }
        _put(_ENDPOINT, endpoint)
    if headers:
        _put(_HEADERS, headers)
    if not _get(_ENDPOINT):
        return {"error": "Enter an endpoint to export to."}
    await asyncio.to_thread(configure)
    return {"connected": True, "account": {"id": CONNECTOR_ID, "label": _label()}}


def _label() -> str:
    cfg = config()
    if cfg is None:
        return ""
    host = urlparse(cfg[0]).hostname or "collector"
    return f"OTLP → {host}"


def _status() -> ConnectorStatus:
    if config() is None:
        return ConnectorStatus(connected=False)
    return ConnectorStatus(
        connected=True, account=ConnectorAccount(id=CONNECTOR_ID, label=_label())
    )


async def _disconnect() -> None:
    _put(_ENDPOINT, "")
    _put(_HEADERS, "")
    await asyncio.to_thread(configure)


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Trace export (OTLP)",
        kind="api-key",
        icon="chart",
        blurb=(
            "Send the built-in agents' traces to Phoenix, Langfuse, Jaeger, Tempo or "
            "any OTLP/HTTP collector, alongside this node's own trace store."
        ),
        status=_status,
        begin=_begin,
        submit=_submit,
        disconnect=_disconnect,
        scopes=[],
        configured=None,
    )


def register() -> None:
    from backend.sdk.registry import registry

    connector = build()
    registry.connectors[connector.id] = connector
