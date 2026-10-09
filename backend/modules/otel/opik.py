"""Opik (Comet) as an export destination: the connector, and the span shaping.

Opik ingests OTLP/HTTP at `<base>/api/v1/private/otel/v1/traces`, with the API
key in `Authorization`, the workspace in `Comet-Workspace` and the project in
`projectName`. Pointing the generic OTLP connector there would "work", and every
trace would arrive unreadable — because of how Opik maps attributes.

## Why spans are reshaped for Opik (and only for Opik)

Opik's ingest matches each attribute against mapping rules (GenAI, OpenInference,
"general"…). **An attribute no rule matches is filed under the span's INPUT.** Ours
carry `horrible.turn_id`, `horrible.round`, `horrible.cost_usd` and friends, so
every span's input — and, through the root span, the trace's — would be our
bookkeeping instead of the prompt. Other rules that matter:

- `gen_ai.request.model` types a span as an LLM call. Right for `chat`, wrong for
  `invoke_agent`, which would then read as a model call wrapping model calls.
- `gen_ai.request.*` is a prefix rule into INPUT, so `max_tokens` lands in input.
- Cost is read from `gen_ai.usage.cost`, never from our own attribute.
- `opik.tags` and `opik.metadata` (a JSON object as a string) on the **root**
  span become the trace's tags and metadata; `thread_id` groups traces.
- `opik.trace_id` / `opik.parent_span_id` re-attach a span to another trace, so
  they are stripped from anything we did not set ourselves.

`opik_span` applies all of that to a copy of each span on the way to this
destination alone. The local store, the trace view and every other destination
keep the semconv spans as they were made.

## Content

Content has its own toggle here, defaulting on only when Opik runs on this machine
(a Docker Opik is not a third party; Comet Cloud is). Turning it on records
content for this destination and nowhere else — see `tracing.content_wanted`.

## Configuration

The Opik SDK's own variables win, the way `OTEL_*` do for the generic exporter:
`OPIK_URL_OVERRIDE`, `OPIK_API_KEY`, `OPIK_WORKSPACE`, `OPIK_PROJECT_NAME`.
Otherwise the connector's values, in the encrypted secrets store.

**A local trap.** Opik's Docker UI listens on `0.0.0.0:5173`, which is also the
Vite dev server's port — Vite binds `127.0.0.1:5173`. Under `pnpm dev`,
`http://127.0.0.1:5173` is *our* dev server and `http://[::1]:5173` is Opik, and
`localhost` is whichever the resolver tries first. `probe()` catches this: the
connector refuses a URL that does not answer as Opik, and says why.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from backend.sdk.types import Connector, ConnectorAccount, ConnectorStatus

logger = logging.getLogger("otel")

CONNECTOR_ID = "opik"
CLOUD_URL = "https://www.comet.com/opik"
DEFAULT_PROJECT = "horrible-dashboard"
DEFAULT_WORKSPACE = "default"

_URL = "opik:url"
_KEY = "opik:api_key"
_WORKSPACE = "opik:workspace"
_PROJECT = "opik:project"
_CONTENT = "opik:include_content"

#: Attributes Opik maps into metadata or typing correctly as they are. Anything
#: else under `gen_ai.` it maps too, except the keys in `_TO_METADATA`.
_KEEP_EXACT = frozenset({"server.address", "thread_id", "input", "output"})
#: `gen_ai.request.*` is an INPUT prefix rule; these are parameters, not input.
_TO_METADATA = frozenset({"gen_ai.request.max_tokens", "gen_ai.request.top_p"})


# --- configuration ----------------------------------------------------------------


def _get(name: str) -> str:
    from backend.modules.database.secrets_store import get_secret_or_none

    try:
        return (get_secret_or_none(name) or "").strip()
    except Exception as exc:  # noqa: BLE001 — an unreadable store is "not configured"
        logger.info("opik: could not read %s (%s)", name, exc)
        return ""


def _put(name: str, value: str) -> None:
    from backend.modules.database.secrets_store import delete_secret, upsert_secret

    if value:
        upsert_secret(name, value)
    else:
        delete_secret(name)


def normalize_base(url: str) -> str:
    """The Opik base URL a user might paste in any of its forms.

    `…/api/v1/private/otel` (the documented OTLP endpoint) and `…/api` are cut
    back to the base; Comet Cloud's bare host gets its `/opik` path."""
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    path = parsed.path
    cut = path.find("/api")
    if cut != -1 and (len(path) == cut + 4 or path[cut + 4] == "/"):
        path = path[:cut]
    host = (parsed.hostname or "").lower()
    if (host == "comet.com" or host.endswith(".comet.com")) and not path:
        path = "/opik"
    return parsed._replace(path=path, params="", query="", fragment="").geturl()


def is_cloud(base: str) -> bool:
    host = (urlparse(base).hostname or "").lower()
    return host == "comet.com" or host.endswith(".comet.com")


def _is_loopback(base: str) -> bool:
    host = (urlparse(base).hostname or "").strip("[]").lower()
    if host == "localhost":
        return True
    import ipaddress

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None) or addr
    return bool(mapped.is_loopback)


def default_include_content(base: str) -> bool:
    """Prompts and tool data go to an Opik on this machine by default, and
    nowhere else without being asked."""
    return _is_loopback(base)


@dataclass(frozen=True)
class OpikConfig:
    base: str
    api_key: str = ""
    workspace: str = ""
    project: str = DEFAULT_PROJECT
    include_content: bool = False

    @property
    def api_base(self) -> str:
        return f"{self.base}/api"

    @property
    def traces_url(self) -> str:
        return f"{self.api_base}/v1/private/otel/v1/traces"

    def headers(self) -> dict[str, str]:
        out = {"projectName": self.project or DEFAULT_PROJECT}
        if self.workspace:
            out["Comet-Workspace"] = self.workspace
        if self.api_key:
            out["Authorization"] = self.api_key
        return out


def _bool(raw: str, default: bool) -> bool:
    if raw.lower() in ("true", "1", "yes", "on"):
        return True
    if raw.lower() in ("false", "0", "no", "off"):
        return False
    return default


def config() -> OpikConfig | None:
    """The effective configuration, or None when Opik is not set up.

    Configured means a stored URL, or the Opik SDK's own variables in the
    backend's environment (a key alone means Comet Cloud)."""
    env_url = os.environ.get("OPIK_URL_OVERRIDE", "").strip()
    env_key = os.environ.get("OPIK_API_KEY", "").strip()
    stored_url = _get(_URL)
    if not (env_url or env_key or stored_url):
        return None
    base = normalize_base(env_url or stored_url or CLOUD_URL)
    content = _get(_CONTENT)
    return OpikConfig(
        base=base,
        api_key=env_key or _get(_KEY),
        workspace=(
            os.environ.get("OPIK_WORKSPACE", "").strip()
            or _get(_WORKSPACE)
            or ("" if is_cloud(base) else DEFAULT_WORKSPACE)
        ),
        project=(
            os.environ.get("OPIK_PROJECT_NAME", "").strip()
            or _get(_PROJECT)
            or DEFAULT_PROJECT
        ),
        include_content=_bool(content, default_include_content(base)),
    )


# --- probing ----------------------------------------------------------------------


@dataclass(frozen=True)
class Probe:
    ok: bool
    message: str = ""
    version: str = ""


def _vite_hint(base: str) -> str:
    """Why a local :5173 URL might have reached the wrong server — but only for
    the URLs where that can happen; `[::1]` is already the way around it."""
    parsed = urlparse(base)
    host = (parsed.hostname or "").strip("[]")
    if parsed.port == 5173 and _is_loopback(base) and host != "::1":
        return (
            " On this machine the dashboard's own dev server also uses port 5173 "
            "(on 127.0.0.1), while Docker's Opik answers on [::1] — try "
            "http://[::1]:5173."
        )
    return ""


def probe(cfg: OpikConfig) -> Probe:
    """Whether `cfg` reaches an Opik that accepts its credentials.

    Uses `requests`, the exporter's own HTTP stack, so `localhost` resolves here
    exactly as it will when spans are sent. Blocking — call it off the loop."""
    import requests

    try:
        r = requests.get(
            f"{cfg.api_base}/v1/private/projects",
            params={"size": 1},
            headers=cfg.headers(),
            timeout=8,
        )
    except requests.RequestException as exc:
        local = " Is Opik running?" if _is_loopback(cfg.base) else ""
        return Probe(
            False,
            f"Could not reach {cfg.base} ({type(exc).__name__}).{local}"
            + _vite_hint(cfg.base),
        )
    if r.status_code in (401, 403):
        return Probe(
            False,
            f"Opik refused the credentials (HTTP {r.status_code}). Check the API "
            "key and the workspace name.",
        )
    try:
        body = r.json()
    except ValueError:
        body = None
    if not (r.ok and isinstance(body, dict) and "content" in body):
        # Something answered, but not as Opik's projects API. The health check
        # tells a wrong URL (often our own dev server) from an Opik error.
        healthy = False
        try:
            ping = requests.get(f"{cfg.api_base}/is-alive/ping", timeout=5)
            healthy = bool(ping.ok and ping.json().get("healthy"))
        except Exception:  # noqa: BLE001
            healthy = False
        if not healthy:
            return Probe(
                False,
                f"{cfg.base} answered HTTP {r.status_code}, but not as Opik."
                + _vite_hint(cfg.base),
            )
        return Probe(False, f"Opik answered HTTP {r.status_code} listing projects.")
    version = ""
    try:
        ver = requests.get(f"{cfg.api_base}/is-alive/ver", timeout=5)
        if ver.ok:
            version = str(ver.json().get("version") or "")
    except Exception:  # noqa: BLE001 — the version is a nicety
        pass
    return Probe(True, version=version)


# --- span shaping -----------------------------------------------------------------


def _parsed(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return list(value) if isinstance(value, tuple) else value


def _keep(key: str) -> bool:
    if key in _KEEP_EXACT:
        return True
    return key.startswith("gen_ai.") and key not in _TO_METADATA


def _meta_key(key: str) -> str:
    return key[len("horrible.") :] if key.startswith("horrible.") else key


def opik_span(span: Any) -> Any:
    """A copy of `span` shaped for Opik's attribute mapping. See the module docs."""
    from backend.modules.otel import destinations

    attrs = {
        k: v
        for k, v in (span.attributes or {}).items()
        # Ours are added below; inbound ones would re-attach spans elsewhere.
        if not k.startswith("opik.") and k not in ("thread_id", "input", "output")
    }
    meta: dict[str, Any] = {}
    op = attrs.get("gen_ai.operation.name")

    if op == "invoke_agent":
        # An agent is not an LLM call: keep the model, as metadata.
        model = attrs.pop("gen_ai.request.model", None)
        if model:
            meta["model"] = model
        # The turn's prompt and answer, as general I/O rather than gen_ai.* (which
        # would type the span LLM). Present only when this destination gets content.
        prompt = attrs.pop("gen_ai.input.messages", None)
        answer = attrs.pop("gen_ai.output.messages", None)
        if prompt is not None:
            attrs["input"] = json.dumps(
                {"messages": _parsed(prompt)}, ensure_ascii=False
            )
        if answer is not None:
            attrs["output"] = json.dumps(
                {"messages": _parsed(answer)}, ensure_ascii=False
            )
    elif op == "chat":
        # Cost on the LLM span only, so the trace's total counts it once.
        cost = attrs.get("horrible.cost_usd")
        if isinstance(cost, (int, float)):
            attrs["gen_ai.usage.cost"] = float(cost)
        cached = attrs.get("horrible.usage.cached_input_tokens")
        if isinstance(cached, int):
            attrs["gen_ai.usage.cache_read.input_tokens"] = cached

    for key in list(attrs):
        if not _keep(key):
            meta[_meta_key(key)] = _parsed(attrs.pop(key))

    resource = getattr(span, "resource", None)
    node = str((resource.attributes if resource else {}).get("service.instance.id", ""))
    remote = span.parent is not None and bool(getattr(span.parent, "is_remote", False))
    entry = span.parent is None or remote
    parent: Any = destinations.KEEP
    if remote:
        # A turn a peer asked us for. Opik builds a trace record only from a root
        # span, so in a project the caller does not also export to, its parent is
        # a span that will never arrive: the turn would be orphan spans. There it
        # becomes its own trace, tagged with who asked. In a shared project (the
        # caller sent our fingerprint) it keeps the parent and nests in their trace.
        shared = str(meta.pop("peer_shared_sinks", "") or "").split(",")
        if not (_fingerprint and _fingerprint in shared):
            parent = None
            meta["remote_traceparent"] = (
                f"00-{span.context.trace_id:032x}-{span.parent.span_id:016x}-01"
            )
    if entry:
        tags = []
        agent = attrs.get("gen_ai.agent.id")
        if agent:
            tags.append(f"agent:{agent}")
        # A turn with no stated source is a chat turn; a model call with no agent
        # around it (research, the judge) is a bare LLM call.
        source = meta.get("source") or {"invoke_agent": "chat", "chat": "llm"}.get(
            str(op), str(op or "span")
        )
        tags.append(f"source:{source}")
        if node:
            tags.append(f"node:{node[:8]}")
            meta["node_id"] = node
        caller = meta.get("peer_caller")
        if remote and caller:
            tags.append(f"caller:{str(caller)[:8]}")
        attrs["opik.tags"] = tags
        conversation = attrs.get("gen_ai.conversation.id")
        if conversation:
            attrs["thread_id"] = str(conversation)
    if meta:
        attrs["opik.metadata"] = json.dumps(meta, ensure_ascii=False, default=str)
    return destinations.rebuild(span, attributes=attrs, parent=parent)


# --- attach -----------------------------------------------------------------------


def configure() -> bool:
    """(Re)attach the Opik destination from the current configuration."""
    from backend.modules.otel import destinations

    global _fingerprint
    cfg = config()
    if cfg is None:
        _fingerprint = ""
        destinations.detach(CONNECTOR_ID)
        return False
    _fingerprint = destinations.fingerprint(cfg.base, cfg.workspace, cfg.project)
    return destinations.attach(
        CONNECTOR_ID,
        url=cfg.traces_url,
        headers=cfg.headers(),
        transform=opik_span,
        include_content=cfg.include_content,
        label="Opik",
        detail={
            "project": cfg.project,
            "workspace": cfg.workspace,
            "cloud": "true" if is_cloud(cfg.base) else "false",
            # Sent to a peer we ask, so a peer exporting to the same project can
            # say so (see network/agent_bridge.py). A hash, not the names.
            "fingerprint": _fingerprint,
        },
    )


#: This node's Opik destination, as `destinations.fingerprint` — read by
#: `opik_span` to decide whether a peer's turn nests or becomes its own trace.
_fingerprint = ""


# --- the connector ----------------------------------------------------------------


def _env_note() -> str:
    names = [
        n
        for n in (
            "OPIK_URL_OVERRIDE",
            "OPIK_API_KEY",
            "OPIK_WORKSPACE",
            "OPIK_PROJECT_NAME",
        )
        if os.environ.get(n)
    ]
    return (
        f" ({', '.join(names)} in the backend's environment overrides this.)"
        if names
        else ""
    )


def _form_step() -> dict[str, Any]:
    stored_url = _get(_URL)
    base = normalize_base(stored_url or CLOUD_URL)
    content = _get(_CONTENT)
    return {
        "step": "form",
        "fields": [
            {
                "name": "url",
                "label": "Opik URL",
                "secret": False,
                "value": base,
                "help": (
                    "Comet Cloud is https://www.comet.com/opik. A self-hosted Opik is "
                    "its UI address, e.g. http://localhost:5173." + _env_note()
                ),
            },
            {
                "name": "workspace",
                "label": "Workspace",
                "secret": False,
                "value": _get(_WORKSPACE),
                "help": "Your Comet workspace. Self-hosted Opik uses “default”.",
            },
            {
                "name": "project",
                "label": "Project",
                "secret": False,
                "value": _get(_PROJECT) or DEFAULT_PROJECT,
                "help": "Every trace goes here, tagged with its agent, source and node.",
            },
            {
                "name": "api_key",
                "label": "API key",
                "secret": True,
                "value": "",
                "help": (
                    "Configured. Leave blank to keep it."
                    if _get(_KEY)
                    else "Required for Comet Cloud; leave blank for a local Opik."
                ),
            },
            {
                "name": "include_content",
                "label": "Include prompts and tool data",
                "secret": False,
                "kind": "toggle",
                "value": "true"
                if _bool(content, default_include_content(base))
                else "false",
                "help": (
                    "Opik shows little without them. Credential-shaped strings are "
                    "masked either way. Applies to Opik only."
                ),
            },
        ],
    }


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    return _form_step()


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    """Validate against the live Opik before storing anything: a wrong URL or a
    rejected key is reported now, not discovered as an empty project later."""
    raw_url = (values.get("url") or "").strip() or _get(_URL) or CLOUD_URL
    parsed = urlparse(raw_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"error": "The Opik URL must be an http(s) URL."}
    base = normalize_base(raw_url)
    key = (values.get("api_key") or "").strip() or _get(_KEY)
    workspace = (values.get("workspace") or "").strip() or (
        "" if is_cloud(base) else DEFAULT_WORKSPACE
    )
    project = (values.get("project") or "").strip() or DEFAULT_PROJECT
    raw_content = (values.get("include_content") or "").strip()
    content = _bool(raw_content, default_include_content(base))
    if is_cloud(base) and not (key and workspace):
        return {"error": "Comet Cloud needs an API key and a workspace."}

    candidate = OpikConfig(
        base=base,
        api_key=key,
        workspace=workspace,
        project=project,
        include_content=content,
    )
    result = await asyncio.to_thread(probe, candidate)
    if not result.ok:
        return {"error": result.message}

    _put(_URL, base)
    _put(_WORKSPACE, workspace)
    _put(_PROJECT, project)
    _put(_CONTENT, "true" if content else "false")
    if (values.get("api_key") or "").strip():
        _put(_KEY, key)
    await asyncio.to_thread(configure)
    return {"connected": True, "account": {"id": CONNECTOR_ID, "label": _label()}}


def _label() -> str:
    cfg = config()
    if cfg is None:
        return ""
    where = "Comet" if is_cloud(cfg.base) else (urlparse(cfg.base).hostname or "Opik")
    return f"{where} · {cfg.project}"


def _status() -> ConnectorStatus:
    from backend.modules.otel import destinations

    if config() is None:
        return ConnectorStatus(connected=False)
    st = destinations.status(CONNECTOR_ID)
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(id=CONNECTOR_ID, label=_label()),
        error=f"Last export failed: {st.last_error}" if st and st.failing else None,
    )


async def _disconnect() -> None:
    for name in (_URL, _KEY, _WORKSPACE, _PROJECT, _CONTENT):
        _put(name, "")
    await asyncio.to_thread(configure)


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Opik",
        kind="api-key",
        icon="chart",
        blurb=(
            "Send every agent's traces to Opik — Comet Cloud or self-hosted — as "
            "tagged, threaded traces with usage and cost."
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
