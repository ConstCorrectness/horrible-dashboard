"""Local-model provider abstraction for the agent.

Two API dialects cover every provider we support:

- **ollama** — Ollama's native API (``/api/tags``, ``/api/chat``,
  ``/api/generate``, ``/api/pull``).
- **openai** — the OpenAI-compatible API (``/v1/models``,
  ``/v1/chat/completions``) served by **LM Studio**, **vLLM** and the node's own
  **llama.cpp** server (see backend/modules/llamacpp).

`PROVIDERS` maps a provider *kind* to its metadata; the dialect functions below
normalize the two wire formats so the orchestrator and routes stay
provider-agnostic. See docs/modules/agent-chat.md.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
import re
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

# Emits streamed deltas: (reasoning_text, content_text). Either may be empty.
DeltaSink = Callable[[str, str], Awaitable[None]]

import httpx
import litellm

# One `chat` span per provider call, at the chokepoint every caller shares.
from backend.modules.otel.tracing import traced_chat
from backend.modules.telemetry.instrument import tee_stream


@dataclass(frozen=True)
class ProviderInfo:
    kind: str  # stable id stored in AgentConfig
    label: str  # human label for the UI
    dialect: str  # "ollama" | "openai" | "litellm"
    default_endpoint: str
    install_url: str
    can_pull: bool  # supports `/api/pull`-style model download
    can_spawn: bool  # the backend can launch a server process for it

    # --- hosted (`litellm`) providers only -------------------------------------
    #: This provider is a hosted API reached over the internet with a key, not a
    #: server on this machine. Its readiness is "do we hold a key", not "is a port
    #: open", which is why `list_models` raises `MissingApiKey` without one instead
    #: of reporting a provider the user cannot actually use as reachable.
    hosted: bool = False
    #: Where a human goes to create the key. Rendered as the link in the API-keys
    #: settings section; empty means we have nowhere to point them.
    api_key_url: str = ""
    #: Environment variable litellm itself honours for this provider. Checked as a
    #: fallback so a key already exported in the shell counts as configured — the
    #: stored secret is not the only way a key can be present, and reporting
    #: "no key" for a provider that works would be a lie the user cannot debug.
    env_var: str = ""
    #: Prefix litellm needs on the model id to route to this provider. OpenAI and
    #: Anthropic model ids are unambiguous and take none; Google AI Studio and
    #: OpenRouter both need one, and a bare id there is routed to the wrong vendor
    #: (or to nothing) with no error we could attribute.
    model_prefix: str = ""
    #: Public catalog listing the provider's models, used to fill the model
    #: dropdown. OpenRouter serves one without authentication; the others do not,
    #: so they fall back to `static_models`.
    catalog_url: str = ""
    #: The connector that holds this provider's key, when the credential belongs to
    #: one rather than to `secrets.db` under the provider kind. NVIDIA's key is one
    #: credential with two uses (NIM inference *and* the NGC catalog), so it lives in
    #: the `nvidia` connector and the provider borrows it -- a second copy under the
    #: provider kind would be a second thing to rotate and a second thing to leak.
    #: Non-empty also means the key-write routes refuse this provider: one home.
    key_connector: str = ""
    #: This provider validates function names against OpenAI's documented pattern
    #: (`a-zA-Z0-9_-`) and **rejects the whole request** if one does not match.
    #: Every tool in this codebase is namespaced with a dot (`library.search`), and
    #: the orchestrator groups tools by that prefix, so the names cannot simply be
    #: renamed at the source -- they are translated at this seam instead. NVIDIA NIM
    #: is the first provider strict about it: it answers
    #: `400 Validation: Function at index 7 has an invalid name: "agent.ask_peer"`
    #: in 281ms, before any inference, so *every* turn fails identically.
    strict_tool_names: bool = False
    #: One line about what this provider's models cost, shown once beneath the model
    #: list rather than as a per-model tag. Deliberately **group-level**: NVIDIA
    #: publishes no per-model price signal -- `/v1/models` is the plain OpenAI shape
    #: with no pricing, and the "Free Endpoint" badge on build.nvidia.com is
    #: server-rendered with no API behind it -- so tagging individual ids `free` the
    #: way `free_models` does for OpenRouter would assert a fact nothing states. A
    #: sentence about the tier is true of the whole group and needs no such claim.
    tier_note: str = ""
    #: Last-resort model list when there is no catalog to fetch. Deliberately short
    #: — the field is a starting point for a dropdown, never a claim to be the
    #: provider's full range, and the model field stays free-text everywhere.
    static_models: tuple[str, ...] = ()


PROVIDERS: dict[str, ProviderInfo] = {
    "ollama": ProviderInfo(
        kind="ollama",
        label="Ollama",
        dialect="ollama",
        default_endpoint="http://localhost:11434",
        install_url="https://ollama.com",
        can_pull=True,
        can_spawn=False,
    ),
    "lmstudio": ProviderInfo(
        kind="lmstudio",
        label="LM Studio",
        dialect="openai",
        default_endpoint="http://localhost:1234",
        install_url="https://lmstudio.ai",
        can_pull=False,
        can_spawn=False,
    ),
    "llamacpp": ProviderInfo(
        kind="llamacpp",
        label="llama.cpp",
        # `llama-server` speaks the OpenAI API, so this is not a new dialect — it
        # inherits streamed reasoning, tool-call assembly and the
        # `tool_choice="required"` retry unchanged. A bespoke dialect would have
        # meant six new branches in this file and would have silently lost that
        # retry, which is gated on `info.dialect == "openai"`.
        dialect="openai",
        default_endpoint="http://127.0.0.1:8080",
        install_url="https://github.com/ggml-org/llama.cpp",
        # Not `can_pull`: weights are fetched from Hugging Face by the llamacpp
        # module's own catalog, not by asking the inference server to pull them
        # the way Ollama does.
        can_pull=False,
        can_spawn=True,
    ),
    "peer": ProviderInfo(
        kind="peer",
        label="Borrowed peer",
        # Same reasoning as `llamacpp` above, and for the same reason: what is on
        # the other end of the tunnel *is* a llama-server, reached through a local
        # port. A bespoke dialect would duplicate six branches and silently lose
        # the `tool_choice="required"` retry gated on `dialect == "openai"`.
        dialect="openai",
        # No default: the endpoint is a tunnel port chosen when the lease is
        # granted, so `_endpoint_for` resolves it from the live lease and there is
        # nothing sensible to fall back to. Without a lease this provider is
        # simply unreachable, which is the truth.
        default_endpoint="",
        install_url="",
        can_pull=False,
        # The lender spawns; a borrower never does.
        can_spawn=False,
    ),
    "nim": ProviderInfo(
        kind="nim",
        label="NVIDIA NIM",
        # The llama.cpp precedent again, and for the same reason: NIM speaks the
        # OpenAI chat API, so this inherits streamed reasoning, tool-call assembly
        # and the `tool_choice="required"` retry unchanged. A bespoke dialect would
        # mean six new branches here and would silently lose that retry, which is
        # gated on `info.dialect == "openai"`.
        dialect="openai",
        default_endpoint="https://integrate.api.nvidia.com",
        install_url="https://build.nvidia.com",
        # Hosted: nothing to pull and nothing to spawn. The key comes from the
        # `nvidia` connector rather than a setting, because it is a credential.
        can_pull=False,
        can_spawn=False,
        # `hosted` is what makes it *discoverable*. It used to be false, which read
        # as "a server on this machine" and, combined with the filter in
        # `routes.status`, meant a keyless NIM was not reported at all -- so nothing
        # anywhere in the agent settings said NVIDIA was a provider this node could
        # use. A hosted provider without a key is listed, unreachable, with somewhere
        # to go; that is the whole point of the flag.
        hosted=True,
        api_key_url="https://build.nvidia.com",
        key_connector="nvidia",
        strict_tool_names=True,
        tier_note=(
            "NVIDIA's hosted endpoints run on your build.nvidia.com credits — no "
            "card, and no per-model price is published, so none is shown per model."
        ),
    ),
    "vllm": ProviderInfo(
        kind="vllm",
        label="vLLM",
        dialect="openai",
        default_endpoint="http://localhost:8001",
        install_url="https://docs.vllm.ai",
        can_pull=False,
        can_spawn=True,
    ),
    "openai": ProviderInfo(
        kind="openai",
        label="OpenAI",
        dialect="litellm",
        default_endpoint="",
        install_url="",
        can_pull=False,
        can_spawn=False,
        hosted=True,
        api_key_url="https://platform.openai.com/api-keys",
        env_var="OPENAI_API_KEY",
        static_models=("gpt-4o", "gpt-4o-mini", "o1-preview", "o1-mini"),
    ),
    "anthropic": ProviderInfo(
        kind="anthropic",
        label="Anthropic",
        dialect="litellm",
        default_endpoint="",
        install_url="",
        can_pull=False,
        can_spawn=False,
        hosted=True,
        api_key_url="https://console.anthropic.com/settings/keys",
        env_var="ANTHROPIC_API_KEY",
        static_models=("claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"),
    ),
    "gemini": ProviderInfo(
        kind="gemini",
        label="Google Gemini",
        dialect="litellm",
        default_endpoint="",
        install_url="",
        can_pull=False,
        can_spawn=False,
        hosted=True,
        api_key_url="https://aistudio.google.com/app/apikey",
        env_var="GEMINI_API_KEY",
        # Google AI Studio ids collide with Vertex's, so litellm routes on this
        # prefix; without it `gemini-2.5-pro` is not a model litellm can place.
        model_prefix="gemini/",
        static_models=("gemini-2.5-pro", "gemini-2.5-flash", "gemini-1.5-pro"),
    ),
    "openrouter": ProviderInfo(
        kind="openrouter",
        label="OpenRouter",
        dialect="litellm",
        default_endpoint="",
        install_url="",
        can_pull=False,
        can_spawn=False,
        hosted=True,
        api_key_url="https://openrouter.ai/keys",
        env_var="OPENROUTER_API_KEY",
        model_prefix="openrouter/",
        # The one provider whose catalog is public: the model list is fetched live
        # rather than pinned here, because OpenRouter's range (and which entries
        # are `:free`) changes weekly and a hardcoded list would be wrong within
        # the month.
        catalog_url="https://openrouter.ai/api/v1/models",
        # Only a fallback for a catalog fetch that times out — never the range on
        # offer. `:free` entries are rate-limited rather than unlimited.
        static_models=("minimax/minimax-m3:free", "minimax/minimax-m3"),
    ),
}

DEFAULT_PROVIDER = "ollama"


def provider_for(kind: str | None) -> ProviderInfo:
    """The provider for a kind, falling back to the default for unknown/None."""
    return PROVIDERS.get(kind or DEFAULT_PROVIDER, PROVIDERS[DEFAULT_PROVIDER])


class MissingApiKey(httpx.HTTPError):
    """A hosted provider was asked for something and we hold no key for it.

    Deliberately an `httpx.HTTPError`: a hosted provider's *readiness* is whether we
    can call it, and the probe in `backend/modules/agent/routes.py` already treats
    that error as "not reachable". Before this existed, a keyless OpenAI reported
    itself reachable with a list of models, so onboarding happily offered a provider
    that failed on the first turn.
    """


def api_key_for(info: ProviderInfo) -> str | None:
    """The API key for a hosted provider: the stored secret first, then the
    environment variable litellm would have read anyway.

    The env fallback is not redundant. litellm picks the variable up on its own, so
    without checking it here a key that *works* would still be reported as missing —
    and the user would be told to fix something that isn't broken.
    """
    if not info.hosted:
        return None
    if info.key_connector:
        from backend.modules.connectors import store as connector_store

        cred = connector_store.load(info.key_connector)
        # A `Credential` dataclass, never a dict -- see the note in the nvidia
        # connector's `api_key`, where reading it as one made it unusable.
        return (cred.access_token.strip() or None) if cred else None
    from backend.modules.database.secrets_store import get_secret_or_none

    stored = (get_secret_or_none(info.kind) or "").strip()
    if stored:
        return stored
    if info.env_var:
        return os.environ.get(info.env_var, "").strip() or None
    return None


def auth_headers(info: ProviderInfo) -> dict[str, str]:
    """Authorization for an openai-dialect provider that needs one.

    Empty for every local server — llama.cpp, LM Studio, vLLM and a borrowed peer
    take no key — so adding this to the shared request path changes nothing for
    them. NVIDIA NIM is the first openai-dialect provider that is *hosted*, and its
    key lives in the `nvidia` connector rather than a setting, because it is a
    credential and `GET /api/settings` hands the whole settings bag to the browser.
    """
    if not info.hosted or info.dialect != "openai":
        return {}
    key = api_key_for(info)
    return {"Authorization": f"Bearer {key}"} if key else {}


def qualify_model(info: ProviderInfo, model: str) -> str:
    """The model id as litellm needs to see it — prefixed for the providers whose
    ids are ambiguous, and left alone when the caller already prefixed it."""
    prefix = info.model_prefix
    if not prefix or not model or model.startswith(prefix):
        return model
    return prefix + model


def litellm_call_kwargs(info: ProviderInfo) -> dict[str, Any]:
    """The auth kwargs for a litellm call. Raises when a hosted provider has no key,
    so the failure names the missing credential instead of surfacing as whatever the
    vendor returns for an unauthenticated request."""
    key = api_key_for(info)
    if not key:
        raise MissingApiKey(f"No API key configured for {info.label}")
    return {"api_key": key}


def raise_for_status_with_body(res: httpx.Response, body: str) -> None:
    """`raise_for_status`, but the provider's explanation survives.

    httpx's own message is the status line and a link to MDN, so a provider that
    said exactly what was wrong — NVIDIA answers
    `Validation: Function at index 7 has an invalid name: "agent.ask_peer"` — is
    reported to the user as an unexplained 400. Diagnosing one of those meant
    digging the response body out of the telemetry ring, which is not a thing a
    user can do.

    The body is truncated: a provider echoing the whole request back (some do) would
    otherwise put the entire tool catalog in a chat bubble.
    """
    if res.status_code < 400:
        return
    detail = " ".join((body or "").split())[:400]
    message = f"{res.status_code} from {res.request.url}"
    if detail:
        message = f"{message}: {detail}"
    raise httpx.HTTPStatusError(message, request=res.request, response=res)


#: Everything OpenAI's function-name grammar disallows. Replaced with `_`.
_UNSAFE_TOOL_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_tool_names(
    tools: list[dict[str, Any]], messages: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Rewrite dotted tool names to the `a-zA-Z0-9_-` grammar a strict provider
    accepts, returning the rewritten tools, the rewritten messages, and the map
    back.

    **The history has to be rewritten too, not just the `tools` array.** An
    assistant turn already in the transcript carries `tool_calls[].function.name`,
    and a name there that no longer matches any declared tool is how this fix would
    half-work: the first round succeeds and the second, once a tool has been called,
    fails or quietly confuses the model about what it just did.

    Collisions are resolved rather than ignored. `library.search` and
    `library_search` both map to `library_search`, and silently merging them would
    route one tool's call to the other -- a wrong action taken confidently, which is
    worse than an error. The second one to appear gets a `_2` suffix.
    """
    forward: dict[str, str] = {}
    restore: dict[str, str] = {}
    for tool in tools:
        name = str((tool.get("function") or {}).get("name") or "")
        if not name or name in forward:
            continue
        safe = _UNSAFE_TOOL_CHARS.sub("_", name)
        if safe != name or safe in restore:
            candidate, n = safe, 1
            while candidate in restore:
                n += 1
                candidate = f"{safe}_{n}"
            safe = candidate
        forward[name] = safe
        restore[safe] = name

    if all(k == v for k, v in forward.items()):
        return tools, messages, {}

    def _rename(tool_calls: list[Any]) -> list[Any]:
        out = []
        for call in tool_calls:
            fn = (call or {}).get("function") or {}
            name = fn.get("name")
            if name in forward:
                call = {**call, "function": {**fn, "name": forward[name]}}
            out.append(call)
        return out

    new_tools = [
        {
            **t,
            "function": {
                **(t.get("function") or {}),
                "name": forward.get(
                    str((t.get("function") or {}).get("name") or ""),
                    (t.get("function") or {}).get("name"),
                ),
            },
        }
        for t in tools
    ]
    new_messages = [
        {**m, "tool_calls": _rename(m["tool_calls"])}
        if isinstance(m.get("tool_calls"), list)
        else m
        for m in messages
    ]
    return new_tools, new_messages, restore


def restore_tool_names(
    calls: list[ToolCall], restore: dict[str, str]
) -> list[ToolCall]:
    """Undo `sanitize_tool_names` on what the model called.

    Without this the orchestrator is handed `library_search`, which matches no
    registered tool -- so the turn reports the model hallucinated a tool it was in
    fact handed, and the real failure (our own rewrite) is invisible.
    """
    if not restore:
        return calls
    return [replace(c, name=restore[c.name]) if c.name in restore else c for c in calls]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    #: Set when the model's argument payload could not be parsed as a JSON object.
    #: The call must then be reported back to the model as an error rather than run —
    #: see `_coerce_args`.
    arg_error: str | None = None


class ProviderStreamError(RuntimeError):
    """The provider reported an error inside an already-200 stream.

    Distinct from an HTTP error because `raise_for_status` cannot see it: the
    status line said 200 long before the engine gave up. Raised so a caller can
    tell "the provider failed" from "the model had nothing to say", which are the
    same thing on the wire and very different things to a user.
    """


@dataclass(frozen=True)
class Usage:
    """What one round actually consumed, as the provider reported it.

    Every field is nullable and nullable means *nobody said* — never zero. A round
    whose provider reports nothing and a round that genuinely used no tokens are
    different facts, and averaging the first into a total as though it were the
    second is how a cost readout becomes confidently wrong.

    `cost_usd` is set here **only when the provider priced the call itself**
    (litellm does). A price looked up from our own table is applied later, in
    `agent/cost.py`, so that "the vendor billed this" and "we estimated this from a
    table that may be months stale" never get conflated at the point of display.
    """

    tokens_in: int | None = None
    tokens_out: int | None = None
    #: Prompt tokens served from the provider's cache. A subset of `tokens_in`, not
    #: an addition to it — billed at a lower rate where it is billed at all.
    cached_in: int | None = None
    cost_usd: float | None = None

    def is_empty(self) -> bool:
        return (
            self.tokens_in is None and self.tokens_out is None and self.cost_usd is None
        )


def _int_or_none(value: Any) -> int | None:
    """A token count, or None when the provider omitted it or sent nonsense.

    Deliberately not `int(value or 0)`: that turns "absent" into a confident zero.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _usage_from_openai(raw: Any) -> Usage:
    """Usage from an OpenAI-dialect `usage` block (also litellm's shape)."""
    if not isinstance(raw, dict):
        # litellm returns an object, not a dict. Ask it for the same fields.
        raw = {
            "prompt_tokens": getattr(raw, "prompt_tokens", None),
            "completion_tokens": getattr(raw, "completion_tokens", None),
            "prompt_tokens_details": getattr(raw, "prompt_tokens_details", None),
        }
    details = raw.get("prompt_tokens_details")
    cached = None
    if isinstance(details, dict):
        cached = _int_or_none(details.get("cached_tokens"))
    elif details is not None:
        cached = _int_or_none(getattr(details, "cached_tokens", None))
    return Usage(
        tokens_in=_int_or_none(raw.get("prompt_tokens")),
        tokens_out=_int_or_none(raw.get("completion_tokens")),
        cached_in=cached,
    )


def _litellm_usage(response: Any) -> Usage:
    """Usage from a litellm response, including the cost litellm computed itself.

    litellm prices the call against its own model table and hangs the result on
    `_hidden_params["response_cost"]`. That figure is worth strictly more than
    anything we can look up: it came from the library that made the request, knows
    which model actually served it, and is updated with the package. We only fall
    back to our own table when it is absent.
    """
    usage = _usage_from_openai(getattr(response, "usage", None))
    cost = None
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        raw = hidden.get("response_cost")
        if raw is not None:
            try:
                cost = float(raw)
            except (TypeError, ValueError):
                cost = None
    if cost is None:
        return usage
    return Usage(usage.tokens_in, usage.tokens_out, usage.cached_in, cost)


def _usage_from_ollama(obj: dict[str, Any]) -> Usage:
    """Usage from Ollama's terminal (`done: true`) chunk."""
    return Usage(
        tokens_in=_int_or_none(obj.get("prompt_eval_count")),
        tokens_out=_int_or_none(obj.get("eval_count")),
    )


@dataclass(frozen=True)
class ChatResult:
    """One assistant turn, normalized across dialects."""

    assistant_message: dict[str, Any]  # appended verbatim to the running messages
    tool_calls: list[ToolCall]
    content: str
    #: What the round consumed, when the provider said. Defaulted so the six existing
    #: construction sites keep working unchanged.
    usage: Usage | None = None


def _coerce_args(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Tool-call arguments arrive as a dict (Ollama) or a JSON string (OpenAI, and
    some Ollama models). Normalize to a dict, plus an error string when the payload
    was malformed.

    The error matters: this used to swallow an unparseable blob into ``{}``, so a
    small model that emitted truncated or half-quoted JSON had its call executed with
    **no arguments at all** — `close_pane` with no instanceId, `files.delete` with no
    path. Silently running the wrong call is worse than failing, and the model got no
    signal it should retry. Returning the error lets the dispatcher hand it back as a
    tool result the model can actually recover from.

    An *empty* payload (``""``/``None``) is not an error — plenty of tools take no
    arguments.
    """
    if isinstance(raw, dict):
        return raw, None
    if raw is None:
        return {}, None
    if isinstance(raw, str):
        if not raw.strip():
            return {}, None
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            return {}, f"arguments were not valid JSON ({exc}); received: {raw[:200]!r}"
        if not isinstance(parsed, dict):
            return {}, (
                f"arguments must be a JSON object, got {type(parsed).__name__}; "
                f"received: {raw[:200]!r}"
            )
        return parsed, None
    return {}, f"arguments must be a JSON object, got {type(raw).__name__}"


def _tool_call(call_id: str, name: str, raw_args: Any) -> ToolCall:
    """Build a ToolCall, carrying any argument-parse failure with it.

    The one place calls are constructed, so the streaming paths — which accumulate
    `arguments` as concatenated deltas and are therefore the *likeliest* source of a
    truncated payload — cannot quietly skip the error the batch path reports.
    """
    args, err = _coerce_args(raw_args)
    return ToolCall(id=call_id, name=name, arguments=args, arg_error=err)


def _parse_tool_calls(raw_calls: list[dict[str, Any]]) -> list[ToolCall]:
    return [
        _tool_call(
            str(c.get("id") or i),
            c.get("function", {}).get("name", ""),
            c.get("function", {}).get("arguments"),
        )
        for i, c in enumerate(raw_calls)
    ]


def normalize_system_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reduce a turn to **one leading system message and no others**.

    The orchestrator assembles the system tier as several separate messages on
    purpose — the spec's prompt, the skill catalog, the tool-group guides — because
    the interpretability recorder tells them apart by position and content marker,
    and a single glued string would make that attribution impossible. It also appends
    a system nudge mid-conversation when a weak model narrates a tool call instead of
    emitting one.

    Many models' Jinja chat templates reject both shapes outright::

        raise_exception('System message must be at the beginning.')

    which surfaces as a **500 from the engine, not a bad answer** — the whole turn is
    lost. So the split survives right up to the wire and is flattened here, at the one
    chokepoint every dialect passes through:

    - leading system messages are **joined** (blank line between, order preserved);
    - a *later* one becomes a ``user`` message, since its position is the point (a
      nudge merged into the preamble would arrive before the failure it answers).

    Ollama and litellm tolerate the original shape, but this runs on every dialect
    anyway: the flattened form is semantically identical, and a per-dialect branch
    would mean the strictness of a template decided whether the nudge was delivered.
    """
    leading: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") != "system":
            rest.append(msg)
            continue
        content = msg.get("content") or ""
        # `not rest` is what makes this "leading": once any non-system message has
        # been seen, a system message is mid-conversation and keeps its place.
        if not rest:
            if content:
                leading.append(content)
        else:
            rest.append({**msg, "role": "user"})
    merged: list[dict[str, Any]] = []
    if leading:
        merged.append({"role": "system", "content": "\n\n".join(leading)})
    return merged + rest


#: Catalogs are fetched on every `/agent/status`, which the home page and the
#: settings page both hit. A vendor's model range does not change by the minute, so
#: a short TTL keeps a settings page from re-downloading a few hundred KB per visit.
_CATALOG_TTL = 600.0


@dataclass(frozen=True)
class CatalogListing:
    """A hosted provider's public catalog, reduced to the two lists the UI uses."""

    #: Every model id, tool-capable first.
    models: tuple[str, ...]
    #: The ids that cost nothing to call, in the same order.
    free: tuple[str, ...]


_CATALOG_CACHE: dict[str, tuple[float, CatalogListing]] = {}

#: Remote openai-dialect listings (`/v1/models`), keyed by **(kind, endpoint)** --
#: NVIDIA NIM today, a vLLM on another host tomorrow. Same TTL and same reason as
#: `_CATALOG_CACHE`: the vendor's range does not change by the minute, and every
#: home-page or settings visit calls `/agent/status`. Keyed by the endpoint too,
#: because `vllm`/`peer` can be repointed at a different server, and a list cached
#: under the bare kind would then describe a machine nobody is talking to.
_REMOTE_MODELS_CACHE: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}

#: The probe client's budget is sized for a loopback port (2s, in `routes.status`).
#: A listing fetched across the internet does not fit in it, and the timeout does
#: not surface as a slow network -- `_probe` swallows `httpx.HTTPError`, so it
#: surfaces as a provider with no models at all. Same reasoning as the explicit
#: timeout in `_catalog_listing`.
REMOTE_TIMEOUT = 10.0


def is_loopback_endpoint(endpoint: str) -> bool:
    """Whether this endpoint is a server on this machine.

    Decides the timeout and whether the listing is worth caching. An empty endpoint
    counts as loopback: the only provider with one is `peer` without a lease, which
    is unreachable either way.
    """
    host = (urlsplit(endpoint).hostname or "").lower()
    return host in {"", "localhost", "127.0.0.1", "::1"}


def invalidate_catalogs() -> None:
    """Drop every cached model listing, so the next `/agent/status` re-fetches.

    The escape hatch for the TTL: a user who just enabled a model on the vendor's
    dashboard should not have to wait ten minutes or restart the node to see it.
    """
    _CATALOG_CACHE.clear()
    _REMOTE_MODELS_CACHE.clear()


def _is_free(entry: dict[str, Any]) -> bool:
    """Whether a catalog entry costs nothing to call.

    Pricing decides, not only the `:free` suffix: most free variants carry both,
    but an id without the suffix can still be priced at zero. Both prices must
    parse and be exactly zero -- OpenRouter prices its `openrouter/auto` router at
    `-1` ("depends on what it routes to"), which is not free.
    """
    if str(entry.get("id", "")).endswith(":free"):
        return True
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return False
    try:
        prompt = float(pricing.get("prompt"))
        completion = float(pricing.get("completion"))
    except (TypeError, ValueError):
        return False
    return prompt == 0 and completion == 0


async def _catalog_listing(
    client: httpx.AsyncClient, info: ProviderInfo
) -> CatalogListing:
    """Model ids from a hosted provider's public catalog, cheapest/most useful
    ordering left exactly as the provider returned it.

    Ids are returned **unprefixed** (`minimax/minimax-m2:free`, not
    `openrouter/minimax/minimax-m2:free`) because that is the id the provider's own
    docs and dashboard use, and it is what a user pastes in. `qualify_model` adds
    the routing prefix at the call, so the two never have to agree by hand.

    A catalog that fails to load falls back to `static_models` rather than raising:
    the key is what decides usability, and an empty dropdown for a provider that
    works is a worse failure than a short one. The model field is free text
    everywhere, so a missing entry is never a dead end.
    """
    cached = _CATALOG_CACHE.get(info.kind)
    if cached and time.monotonic() - cached[0] < _CATALOG_TTL:
        return cached[1]
    try:
        # An explicit timeout, because the caller's client is the *probe* client and
        # its 2s budget is sized for a loopback port. This is a few hundred KB from
        # the other side of the internet, and timing out here does not read as a
        # slow network — it reads as a provider with two models.
        res = await client.get(info.catalog_url, timeout=10)
        res.raise_for_status()
        data = res.json().get("data") or []
    except (httpx.HTTPError, ValueError):
        return CatalogListing(
            models=tuple(info.static_models),
            free=tuple(m for m in info.static_models if m.endswith(":free")),
        )

    # Tool-capable models first. The orchestrator is a tool-calling loop, so a
    # model without tool support does not merely do worse there — it never calls a
    # tool at all, which reads as "the agent ignored me" rather than as a model
    # that cannot do this. Ordering rather than filtering, because the same list
    # feeds plain chat, where those models are perfectly good.
    tools_first: list[str] = []
    rest: list[str] = []
    free_tools_first: list[str] = []
    free_rest: list[str] = []
    for m in data:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        tools = "tools" in (m.get("supported_parameters") or [])
        model_id = str(m["id"])
        (tools_first if tools else rest).append(model_id)
        if _is_free(m):
            (free_tools_first if tools else free_rest).append(model_id)
    listing = CatalogListing(
        models=tuple(tools_first + rest), free=tuple(free_tools_first + free_rest)
    )
    _CATALOG_CACHE[info.kind] = (time.monotonic(), listing)
    return listing


async def _catalog_models(client: httpx.AsyncClient, info: ProviderInfo) -> list[str]:
    """Every model id in a hosted provider's catalog -- see `_catalog_listing`."""
    return list((await _catalog_listing(client, info)).models)


async def free_models(client: httpx.AsyncClient, info: ProviderInfo) -> list[str]:
    """The catalog's free models, **with or without a key**.

    Unlike `list_models` this never checks for a key, and that is the point: the
    catalog is public, and onboarding is exactly the moment a user has no key yet
    and wants to know what they could run for nothing once they get one. It says
    nothing about reachability -- a free model on OpenRouter still needs a key to
    call. Providers without a public catalog have no free list.
    """
    if not info.catalog_url:
        return []
    return list((await _catalog_listing(client, info)).free)


async def list_models(
    client: httpx.AsyncClient, info: ProviderInfo, endpoint: str
) -> list[str]:
    """Reachability probe doubling as a model list. Raises httpx.HTTPError when
    the provider is down."""
    # A hosted provider is "reachable" when we hold a key for it, not when a port
    # answers. Raising here is what keeps a keyless provider out of the onboarding
    # picker's reachable set -- and, for an openai-dialect hosted provider (NIM), it
    # is also what stops the probe making an unauthenticated request across the
    # internet on every status poll to be told what we already knew. Checked before
    # the dialect branch precisely because it is not a litellm-only rule.
    if info.hosted and not api_key_for(info):
        raise MissingApiKey(f"No API key configured for {info.label}")

    if info.dialect == "litellm":
        if info.catalog_url:
            return await _catalog_models(client, info)
        return list(info.static_models)

    if info.dialect == "ollama":
        res = await client.get(f"{endpoint}/api/tags")
        res.raise_for_status()
        return [m["name"] for m in res.json().get("models", [])]
    if is_loopback_endpoint(endpoint):
        res = await client.get(f"{endpoint}/v1/models", headers=auth_headers(info))
        res.raise_for_status()
        return [m["id"] for m in res.json().get("data", [])]

    # Remote (NVIDIA NIM, or a vLLM on another host): cached and given its own
    # timeout -- see `REMOTE_TIMEOUT`.
    key = (info.kind, endpoint)
    cached = _REMOTE_MODELS_CACHE.get(key)
    if cached and time.monotonic() - cached[0] < _CATALOG_TTL:
        return list(cached[1])
    res = await client.get(
        f"{endpoint}/v1/models",
        headers=auth_headers(info),
        timeout=REMOTE_TIMEOUT,
    )
    res.raise_for_status()
    models = tuple(
        str(m["id"])
        for m in res.json().get("data", [])
        if isinstance(m, dict) and m.get("id")
    )
    _REMOTE_MODELS_CACHE[key] = (time.monotonic(), models)
    return list(models)


@traced_chat
async def chat(
    client: httpx.AsyncClient,
    info: ProviderInfo,
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    think: bool | None = None,
) -> ChatResult:
    """One non-streaming tool-calling round, normalized to a `ChatResult`.

    ``think=False`` asks a thinking model to answer without reasoning first. That
    matters most when ``max_tokens`` is small: reasoning tokens count against the
    same budget, so Gemma 4 on LM Studio with a 180-token cap spent 177 of them on
    "Thinking Process: 1. Analyze the Request" and returned ``content: ""`` with
    ``finish_reason: "length"`` -- a silent non-answer, not an error. ``None``
    leaves the model's own default alone.
    """
    messages = normalize_system_messages(messages)
    if info.dialect == "litellm":
        call_kwargs = litellm_call_kwargs(info)
        if temperature is not None:
            call_kwargs["temperature"] = temperature
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens
        response = await litellm.acompletion(
            model=qualify_model(info, model),
            messages=messages,
            tools=tools or None,
            **call_kwargs,
        )
        msg = response.choices[0].message
        msg_dict = msg.model_dump()
        return ChatResult(
            assistant_message=msg_dict,
            tool_calls=_parse_tool_calls(msg_dict.get("tool_calls") or []),
            content=msg_dict.get("content") or "",
            usage=_litellm_usage(response),
        )

    # Empty for every provider but a strict one; the shared return below undoes it.
    restore: dict[str, str] = {}
    if info.dialect == "ollama":
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
        }
        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if options:
            payload["options"] = options
        if think is not None:
            payload["think"] = think
        res = await client.post(
            f"{endpoint}/api/chat",
            json=payload,
        )
        res.raise_for_status()
        body = res.json()
        msg = body.get("message", {})
        usage = _usage_from_ollama(body)
    else:
        sent_tools, sent_messages = tools, messages
        if info.strict_tool_names:
            sent_tools, sent_messages, restore = sanitize_tool_names(tools, messages)
        payload = {
            "model": model,
            "messages": sent_messages,
            "tools": sent_tools,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if think is False:
            if info.kind == "lmstudio":
                # Measured against LM Studio: this is the one knob it honours.
                # `chat_template_kwargs` and `reasoning: {effort}` are silently
                # ignored, and a non-thinking model accepts this unharmed.
                payload["reasoning_effort"] = "none"
            elif info.kind in {"llamacpp", "peer", "vllm"}:
                # llama-server and vLLM pass these through to the Jinja template.
                payload["chat_template_kwargs"] = {"enable_thinking": False}
        res = await client.post(
            f"{endpoint}/v1/chat/completions",
            json=payload,
            headers=auth_headers(info),
        )
        raise_for_status_with_body(res, res.text)
        body = res.json()
        choices = body.get("choices") or [{}]
        msg = choices[0].get("message", {})
        usage = _usage_from_openai(body.get("usage"))
    return ChatResult(
        assistant_message=msg,
        tool_calls=restore_tool_names(
            _parse_tool_calls(msg.get("tool_calls") or []), restore
        ),
        content=msg.get("content") or "",
        usage=usage,
    )


@traced_chat
async def chat_stream(
    client: httpx.AsyncClient,
    info: ProviderInfo,
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_delta: DeltaSink,
    temperature: float | None = None,
    tool_choice: str | None = None,
    context_size: int | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
) -> ChatResult:
    """One **streamed** tool-calling round. Emits the model's reasoning
    (``thinking`` / ``reasoning_content``) and answer ``content`` token-deltas via
    ``on_delta(reasoning, content)`` as they arrive, and returns the assembled
    ``ChatResult`` (assistant message + tool calls + full content) for the loop.

    ``temperature`` controls sampling (the orchestrator uses ~0 so the model emits
    structured tool calls instead of narrating them). ``tool_choice`` (``"required"``/
    ``"auto"``/a specific function) forces a call on the OpenAI dialect; Ollama has no
    reliable equivalent, so it's ignored there."""
    messages = normalize_system_messages(messages)
    if info.dialect == "ollama":
        return await _ollama_chat_stream(
            client,
            endpoint,
            model,
            messages,
            tools,
            on_delta,
            temperature,
            context_size,
            max_tokens,
            top_p,
        )
    if info.dialect == "litellm":
        return await _litellm_chat_stream(
            info.kind,
            model,
            messages,
            tools,
            on_delta,
            temperature,
            tool_choice,
            max_tokens,
            top_p,
        )
    return await _openai_chat_stream(
        client,
        endpoint,
        model,
        messages,
        tools,
        on_delta,
        temperature,
        tool_choice,
        max_tokens,
        top_p,
        auth_headers(info),
        info.strict_tool_names,
    )


class ThinkingExtractor:
    """Extracts <think>...</think> blocks from a streaming content text,
    separating reasoning deltas from final content deltas, and flushing the remainder."""

    def __init__(self, on_delta: DeltaSink) -> None:
        self.on_delta = on_delta
        self.in_think = False
        self.buffer = ""
        self.reasoning_parts: list[str] = []
        self.content_parts: list[str] = []

    async def feed_reasoning(self, text: str) -> None:
        if text:
            self.reasoning_parts.append(text)
            await self.on_delta(text, "")

    async def feed_content(self, text: str) -> None:
        if not text:
            return
        self.buffer += text
        while self.buffer:
            if not self.in_think:
                idx = self.buffer.find("<think>")
                if idx != -1:
                    lead = self.buffer[:idx]
                    if lead:
                        self.content_parts.append(lead)
                        await self.on_delta("", lead)
                    self.in_think = True
                    self.buffer = self.buffer[idx + 7 :]
                else:
                    prefixes = ["<", "<t", "<th", "<thi", "<thin", "<think"]
                    partial_match = False
                    for pt in prefixes:
                        if self.buffer.endswith(pt):
                            emit_len = len(self.buffer) - len(pt)
                            if emit_len > 0:
                                lead = self.buffer[:emit_len]
                                self.content_parts.append(lead)
                                await self.on_delta("", lead)
                                self.buffer = pt
                            partial_match = True
                            break
                    if not partial_match:
                        self.content_parts.append(self.buffer)
                        await self.on_delta("", self.buffer)
                        self.buffer = ""
                    break
            else:
                idx = self.buffer.find("</think>")
                if idx != -1:
                    reason = self.buffer[:idx]
                    if reason:
                        self.reasoning_parts.append(reason)
                        await self.on_delta(reason, "")
                    self.in_think = False
                    self.buffer = self.buffer[idx + 8 :]
                else:
                    prefixes = [
                        "</",
                        "</t",
                        "</th",
                        "</thi",
                        "</thin",
                        "</think",
                        "</think>",
                    ]
                    partial_match = False
                    for pt in prefixes:
                        if self.buffer.endswith(pt):
                            emit_len = len(self.buffer) - len(pt)
                            if emit_len > 0:
                                reason = self.buffer[:emit_len]
                                self.reasoning_parts.append(reason)
                                await self.on_delta(reason, "")
                                self.buffer = pt
                            partial_match = True
                            break
                    if not partial_match:
                        self.reasoning_parts.append(self.buffer)
                        await self.on_delta(self.buffer, "")
                        self.buffer = ""
                    break

    async def flush(self) -> tuple[str, str]:
        if self.buffer:
            if self.in_think:
                self.reasoning_parts.append(self.buffer)
                await self.on_delta(self.buffer, "")
            else:
                self.content_parts.append(self.buffer)
                await self.on_delta("", self.buffer)
            self.buffer = ""
        return "".join(self.reasoning_parts), "".join(self.content_parts)


async def _ollama_chat_stream(
    client: httpx.AsyncClient,
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_delta: DeltaSink,
    temperature: float | None = None,
    context_size: int | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
) -> ChatResult:
    url = f"{endpoint}/api/chat"
    base: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    # Omitted when empty rather than sent as `[]`: a tool-less round is a real
    # request (the orchestrator's final answer after its step budget runs out), and
    # several OpenAI-compatible servers reject an empty array outright.
    if tools:
        base["tools"] = tools
    options: dict[str, Any] = {}
    if temperature is not None:
        options["temperature"] = temperature
    if context_size is not None:
        options["num_ctx"] = context_size
    if max_tokens is not None:
        options["num_predict"] = max_tokens
    if top_p is not None:
        options["top_p"] = top_p
    if options:
        base["options"] = options

    async def run(think: bool) -> ChatResult:
        import logging

        logger = logging.getLogger("backend.agent.providers")
        payload = {**base, "think": True} if think else base
        logger.info(f"Ollama Request Payload: {json.dumps(payload)}")
        extractor = ThinkingExtractor(on_delta)
        tool_calls_raw: list[dict[str, Any]] = []
        usage = Usage()
        async with client.stream("POST", url, json=payload) as res:
            logger.info(f"Ollama Response Status: {res.status_code}")
            if res.status_code >= 400:
                await res.aread()
                logger.error(f"Ollama error body: {res.text}")
                res.raise_for_status()
            async for line in tee_stream(res, res.aiter_lines()):
                if not line:
                    continue
                obj = json.loads(line)
                msg = obj.get("message") or {}
                thinking = msg.get("thinking")
                content = msg.get("content")
                if thinking:
                    logger.info(f"Ollama thinking delta: {repr(thinking)}")
                    await extractor.feed_reasoning(thinking)
                if content:
                    logger.info(f"Ollama content delta: {repr(content)}")
                    await extractor.feed_content(content)
                if msg.get("tool_calls"):
                    # Ollama emits the (accumulated) tool_calls in a chunk; take latest.
                    tool_calls_raw = msg["tool_calls"]
                if obj.get("done"):
                    # The terminal chunk carries `prompt_eval_count`/`eval_count`.
                    # It was already in scope and the `break` threw it away.
                    usage = _usage_from_ollama(obj)
                    break
        reasoning, full = await extractor.flush()
        assistant: dict[str, Any] = {"role": "assistant", "content": full}
        if reasoning:
            assistant["reasoning_content"] = reasoning
        if tool_calls_raw:
            assistant["tool_calls"] = tool_calls_raw
        return ChatResult(assistant, _parse_tool_calls(tool_calls_raw), full, usage)

    try:
        return await run(think=True)
    except httpx.HTTPStatusError as exc:
        # Models without a thinking mode reject `think: true` (400) — retry plainly,
        # so non-reasoning models still stream their content.
        if exc.response is not None and exc.response.status_code == 400:
            return await run(think=False)
        raise


#: Endpoints that answered 400 to `stream_options`. Remembered per process so the
#: probe costs one failed request per server, not one per turn. Never persisted: a
#: server upgraded under us should get another chance on the next restart.
_NO_STREAM_USAGE: set[str] = set()


def _wants_stream_usage(url: str) -> bool:
    return url not in _NO_STREAM_USAGE


def _disable_stream_usage(url: str) -> None:
    _NO_STREAM_USAGE.add(url)


async def _openai_chat_stream(
    client: httpx.AsyncClient,
    endpoint: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_delta: DeltaSink,
    temperature: float | None = None,
    tool_choice: str | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
    #: Empty for every local server; a Bearer token for a hosted openai-dialect
    #: provider (NIM). See `auth_headers`.
    headers: dict[str, str] | None = None,
    #: Translate dotted tool names for a provider that rejects them outright. See
    #: `sanitize_tool_names`.
    strict_tool_names: bool = False,
) -> ChatResult:
    url = f"{endpoint}/v1/chat/completions"
    sent_tools, sent_messages, restore = (
        sanitize_tool_names(tools, messages)
        if strict_tool_names
        else (tools, messages, {})
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": sent_messages,
        "stream": True,
    }
    if sent_tools:  # never `[]` — see `_ollama_chat_stream`
        payload["tools"] = sent_tools
    if temperature is not None:
        payload["temperature"] = temperature
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if top_p is not None:
        payload["top_p"] = top_p
    # A streamed OpenAI-dialect response carries **no usage block at all** unless it
    # is asked for. Without this the parsing below is dead code that returns None
    # forever, and the obvious conclusion — "this provider doesn't report usage" —
    # is wrong. Not every local server accepts the field, so `_wants_stream_usage`
    # remembers the ones that rejected it.
    if _wants_stream_usage(url):
        payload["stream_options"] = {"include_usage": True}
    extractor = ThinkingExtractor(on_delta)
    usage = Usage()
    # OpenAI streams tool calls as partial deltas keyed by index; assemble them.
    tool_acc: dict[int, dict[str, Any]] = {}
    async with client.stream("POST", url, json=payload, headers=headers or None) as res:
        if res.status_code == 400 and "stream_options" in payload:
            # An older or stricter server rejecting the unknown field. Remember it
            # and retry plainly, the same shape as the `think=True` retry above —
            # one 400 per endpoint per process, not one per turn.
            await res.aread()
            _disable_stream_usage(url)
            payload.pop("stream_options", None)
            return await _openai_chat_stream(
                client,
                endpoint,
                model,
                messages,
                tools,
                on_delta,
                temperature,
                tool_choice,
                max_tokens,
                top_p,
                headers,
                strict_tool_names,
            )
        if res.status_code >= 400:
            # The body has not been streamed yet — read it before it is discarded,
            # or the error is a bare status line.
            raise_for_status_with_body(
                res, (await res.aread()).decode(errors="replace")
            )
        async for line in tee_stream(res, res.aiter_lines()):
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            frame = json.loads(data)
            # An error can arrive *inside* a 200 stream, as an SSE `event: error`
            # followed by a data frame carrying `error` instead of `choices`.
            # `raise_for_status` has already passed by then, so nothing else will
            # catch it — and the old code fed that frame through the normal path,
            # where `.get("choices") or [{}]` turned it into an empty choice and
            # the turn returned a blank answer with no tool calls.
            #
            # That is the worst shape a failure can take here: it is indis-
            # tinguishable from a model that chose to say nothing. It was found
            # via the evals module, where LM Studio's constrained-decoding grammar
            # rejected a tool call ("output that does not match the expected
            # peg-native format") and every affected case was scored as the model
            # declining to act.
            if isinstance(frame, dict) and frame.get("error"):
                detail = frame["error"]
                message = (
                    detail.get("message") if isinstance(detail, dict) else str(detail)
                ) or "the provider reported an error mid-stream"
                raise ProviderStreamError(message)
            # The usage frame arrives with an empty `choices` list, so it has to be
            # read before the `or [{}]` below quietly turns it into a blank choice.
            if frame.get("usage"):
                usage = _usage_from_openai(frame["usage"])
            choice = (frame.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            # DeepSeek/vLLM reasoning parsers use `reasoning_content`; some use `reasoning`.
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            content = delta.get("content")
            if reasoning:
                await extractor.feed_reasoning(reasoning)
            if content:
                await extractor.feed_content(content)
            for tc in delta.get("tool_calls") or []:
                slot = tool_acc.setdefault(
                    tc.get("index", 0), {"id": None, "name": "", "args": ""}
                )
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
    reasoning, full = await extractor.flush()
    ordered = [tool_acc[i] for i in sorted(tool_acc)]
    tool_calls = restore_tool_names(
        [
            _tool_call(str(s["id"] or i), s["name"], s["args"])
            for i, s in enumerate(ordered)
        ],
        restore,
    )
    assistant: dict[str, Any] = {"role": "assistant", "content": full}
    if reasoning:
        assistant["reasoning_content"] = reasoning
    if ordered:
        # Restored here too, so the transcript is kept in the app's own vocabulary
        # and the rewrite stays a property of the wire, reapplied per round. Leaving
        # the sanitized name in the history would make the next round's rewrite a
        # no-op on an already-rewritten name, and would show the user a tool called
        # `library_search` that appears in no registry.
        assistant["tool_calls"] = [
            {
                "id": str(s["id"] or i),
                "type": "function",
                "function": {
                    "name": restore.get(s["name"], s["name"]),
                    "arguments": s["args"],
                },
            }
            for i, s in enumerate(ordered)
        ]
    return ChatResult(assistant, tool_calls, full, usage)


async def _litellm_chat_stream(
    provider_kind: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_delta: DeltaSink,
    temperature: float | None = None,
    tool_choice: str | None = None,
    max_tokens: int | None = None,
    top_p: float | None = None,
) -> ChatResult:
    kwargs: dict[str, Any] = {}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if top_p is not None:
        kwargs["top_p"] = top_p
    if tools:
        kwargs["tools"] = tools

    info = provider_for(provider_kind)
    kwargs.update(litellm_call_kwargs(info))

    extractor = ThinkingExtractor(on_delta)
    tool_acc: dict[int, dict[str, Any]] = {}
    usage = Usage()

    response = await litellm.acompletion(
        model=qualify_model(info, model),
        messages=messages,
        stream=True,
        # Same requirement as the raw OpenAI path: no usage is emitted unless asked.
        # litellm normalizes this across vendors, so there is no per-endpoint retry
        # to do here.
        stream_options={"include_usage": True},
        **kwargs,
    )

    async for chunk in response:
        # The final chunk carries usage and may have no choices at all.
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            merged = _usage_from_openai(chunk_usage)
            if not merged.is_empty():
                usage = merged
        if not getattr(chunk, "choices", None):
            continue
        delta = chunk.choices[0].delta
        if not delta:
            continue

        reasoning = getattr(delta, "reasoning_content", None) or getattr(
            delta, "reasoning", None
        )
        content = delta.content
        if reasoning:
            await extractor.feed_reasoning(reasoning)
        if content:
            await extractor.feed_content(content)

        tool_calls = delta.tool_calls
        if tool_calls:
            for tc in tool_calls:
                idx = getattr(tc, "index", 0)
                slot = tool_acc.setdefault(idx, {"id": None, "name": "", "args": ""})
                if getattr(tc, "id", None):
                    slot["id"] = tc.id
                fn = getattr(tc, "function", None)
                if fn:
                    if getattr(fn, "name", None):
                        slot["name"] = fn.name
                    if getattr(fn, "arguments", None):
                        slot["args"] += fn.arguments

    reasoning, full = await extractor.flush()
    ordered = [tool_acc[i] for i in sorted(tool_acc)]
    tool_calls = [
        _tool_call(str(s["id"] or i), s["name"], s["args"])
        for i, s in enumerate(ordered)
    ]
    assistant: dict[str, Any] = {"role": "assistant", "content": full}
    if reasoning:
        assistant["reasoning_content"] = reasoning
    if ordered:
        assistant["tool_calls"] = [
            {
                "id": str(s["id"] or i),
                "type": "function",
                "function": {"name": s["name"], "arguments": s["args"]},
            }
            for i, s in enumerate(ordered)
        ]
    # A streamed call has no `_hidden_params`, so litellm computes no cost for it.
    # Ours is looked up from the price table instead — see `agent/cost.py`.
    return ChatResult(assistant, tool_calls, full, usage)


def tool_result_message(
    info: ProviderInfo, call: ToolCall, result: Any
) -> dict[str, Any]:
    """Format a tool result for appending to the running messages. Ollama keys on
    ``tool_name``; the OpenAI dialect keys on ``tool_call_id``."""
    content = json.dumps(result)
    if info.dialect == "ollama":
        return {"role": "tool", "content": content, "tool_name": call.name}
    return {"role": "tool", "content": content, "tool_call_id": call.id}


async def generate(
    client: httpx.AsyncClient,
    info: ProviderInfo,
    endpoint: str,
    model: str,
    prompt: str,
    max_tokens: int = 64,
    temperature: float = 0.2,
    system: str | None = None,
) -> str:
    """One non-streaming, short completion (for editor autosuggest), normalized
    across dialects to a plain string. Token-capped to keep latency low; low
    ``temperature`` by default so code completions are stable, not creative.

    ``system`` rides the dialect's own system channel rather than being glued
    onto the prompt, because Ollama's ``/api/generate`` takes a top-level
    ``system`` field while the chat dialects take a leading message — and a
    persona pasted into the prompt is answered rather than adopted."""
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    if info.dialect == "litellm":
        response = await litellm.acompletion(
            model=qualify_model(info, model),
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **litellm_call_kwargs(info),
        )
        return response.choices[0].message.content or ""

    if info.dialect == "ollama":
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        }
        if system:
            payload["system"] = system
        res = await client.post(f"{endpoint}/api/generate", json=payload)
        res.raise_for_status()
        return res.json().get("response", "")
    res = await client.post(
        f"{endpoint}/v1/chat/completions",
        json={
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
    )
    res.raise_for_status()
    choices = res.json().get("choices") or [{}]
    return choices[0].get("message", {}).get("content") or ""


async def generate_stream(
    client: httpx.AsyncClient,
    info: ProviderInfo,
    endpoint: str,
    model: str,
    prompt: str,
) -> AsyncIterator[str]:
    """Stream a one-shot completion as NDJSON ``{"response": <token>}`` lines,
    normalizing Ollama's ``/api/generate`` and the OpenAI ``/v1/chat/completions``
    SSE stream into the one shape the frontend already understands."""
    if info.dialect == "litellm":
        response = await litellm.acompletion(
            model=qualify_model(info, model),
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            **litellm_call_kwargs(info),
        )
        async for chunk in response:
            token = chunk.choices[0].delta.content or ""
            if token:
                yield json.dumps({"response": token}) + "\n"
        return

    if info.dialect == "ollama":
        async with client.stream(
            "POST",
            f"{endpoint}/api/generate",
            json={"model": model, "prompt": prompt, "stream": True},
        ) as res:
            res.raise_for_status()
            async for line in tee_stream(res, res.aiter_lines()):
                if not line:
                    continue
                obj = json.loads(line)
                token = obj.get("response", "")
                if token:
                    yield json.dumps({"response": token}) + "\n"
                if obj.get("done"):
                    return
        return
    async with client.stream(
        "POST",
        f"{endpoint}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        },
    ) as res:
        res.raise_for_status()
        async for line in tee_stream(res, res.aiter_lines()):
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                return
            delta = (json.loads(data).get("choices") or [{}])[0].get("delta", {})
            token = delta.get("content") or ""
            if token:
                yield json.dumps({"response": token}) + "\n"
