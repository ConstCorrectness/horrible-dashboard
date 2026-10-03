"""One HTTP call to a social platform, with its failure sorted into "try again later"
or "stop and tell the person". Shared by the X, LinkedIn and YouTube connectors and
the Scrive outbox runner that sends through them.

Unlike `github_api.request`, failures are **exceptions** (`SendError`): a send is a
sequence of checkpointed steps, and the runner needs one place to decide whether a step
is retried with backoff (`transient`) or the whole send stops (`transient=False`).

- Network errors, 5xx and 429 are transient. A 429's `Retry-After` / `x-rate-limit-reset`
  is kept so the runner waits that long rather than its own backoff.
- 401 is never transient: the token is gone, and only reconnecting fixes it.
- Other 4xx are permanent, with the platform's own message — it usually says exactly
  what was wrong with the post.

`transport` exists for tests (an `httpx.MockTransport`); nothing else sets it.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

#: Set by tests to route every call through an `httpx.MockTransport`.
transport: httpx.AsyncBaseTransport | None = None


class SendError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        transient: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient
        self.retry_after = retry_after


def client(timeout: float = 60.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=transport)


def _detail(res: httpx.Response) -> str:
    try:
        body = res.json()
    except ValueError:
        return res.text[:300]
    if isinstance(body, dict):
        for key in ("detail", "message", "title", "error_description", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict) and value.get("message"):
                return str(value["message"])
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                return str(first.get("message") or first.get("detail") or first)
    return str(body)[:300]


def _retry_after(res: httpx.Response) -> float | None:
    after = res.headers.get("retry-after")
    if after:
        try:
            return max(0.0, float(after))
        except ValueError:
            pass
    reset = res.headers.get("x-rate-limit-reset")
    if reset:
        try:
            return max(0.0, float(reset) - time.time())
        except ValueError:
            pass
    return None


def check(res: httpx.Response, service: str) -> httpx.Response:
    """`res` if it succeeded (2xx, or YouTube's 308 "resume incomplete"); otherwise a
    `SendError` saying what the platform said."""
    if res.status_code < 400 or res.status_code == 308:
        return res
    detail = _detail(res)
    if res.status_code == 401:
        raise SendError(
            f"{service} rejected the stored sign-in — reconnect {service} from the "
            f"home page. ({detail})",
            status=401,
        )
    if res.status_code == 429:
        raise SendError(
            f"{service} rate limit: {detail}",
            status=429,
            transient=True,
            retry_after=_retry_after(res),
        )
    if res.status_code >= 500:
        raise SendError(
            f"{service} returned {res.status_code}: {detail}",
            status=res.status_code,
            transient=True,
        )
    raise SendError(
        f"{service} returned {res.status_code}: {detail}", status=res.status_code
    )


async def call(
    method: str,
    url: str,
    *,
    service: str,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    **kwargs: Any,
) -> httpx.Response:
    """One request. `kwargs` go to httpx (`json`, `data`, `files`, `content`, `params`)."""
    all_headers = dict(headers or {})
    if token:
        all_headers["Authorization"] = f"Bearer {token}"
    try:
        async with client(timeout) as http:
            res = await http.request(method, url, headers=all_headers, **kwargs)
    except httpx.HTTPError as exc:
        raise SendError(f"couldn't reach {service}: {exc}", transient=True) from exc
    return check(res, service)
