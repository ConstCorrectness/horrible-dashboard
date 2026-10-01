"""One authenticated GitHub REST call, for everything that acts on the connector's token.

Shared by the agent tools (`github_tools.py`) and notebook publishing, so there is one
place that knows how GitHub reports a bad token, a rate limit, or a missing resource.

Errors come back as **values** (`{"error": ..., "status": ...}`), not exceptions: an
agent tool hands the dict straight to the model, and a publisher needs to tell "not
found" (create it) apart from "forbidden" (stop and say why). `status` carries the HTTP
code for exactly that; it is absent when GitHub was never reached.
"""

from __future__ import annotations

from typing import Any

from backend.modules.connectors.providers import github

API = "https://api.github.com"

NOT_CONNECTED = {
    "error": "GitHub isn't connected — connect it from the home page, then try again."
}


async def request(
    method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
) -> Any:
    """Call `API + path`. Returns the decoded body, `{}` for an empty one, or an error dict."""
    import httpx

    token = await github.token()
    if not token:
        return NOT_CONNECTED
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.request(
                method,
                f"{API}{path}",
                params=params,
                json=json,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach GitHub: {exc}"}

    if res.status_code == 401:
        return {
            "error": "GitHub rejected the stored token — reconnect GitHub from the home page.",
            "status": 401,
        }
    if res.status_code == 403 and "rate limit" in res.text.lower():
        return {
            "error": "GitHub rate limit hit — wait a minute and try again.",
            "status": 403,
        }
    if res.status_code >= 400:
        detail = ""
        try:
            detail = str(res.json().get("message") or "")
        except ValueError:
            detail = res.text[:200]
        return {
            "error": f"GitHub returned {res.status_code}: {detail}",
            "status": res.status_code,
        }
    # DELETE and some PUTs answer 204 with no body; `res.json()` would raise on it.
    if res.status_code == 204 or not res.content:
        return {}
    return res.json()


async def public_get(
    path: str, *, params: dict[str, Any] | None = None
) -> tuple[Any, dict[str, str]]:
    """A read of public GitHub data, authenticated when connected and anonymous otherwise.

    Returns `(body or error dict, response headers)`. Separate from `request` because
    that one's contract is "acts on the connector's token" — publishing and the agent
    tools must not silently fall back to anonymous. A *browse* should: searching
    public repos needs no account, only a lower rate limit (10 searches/min
    anonymously, 30 with a token), which the headers report so the caller can say
    exactly when it resets.
    """
    import httpx

    token = await github.token()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(f"{API}{path}", params=params, headers=headers)
    except httpx.HTTPError as exc:
        return {"error": f"couldn't reach GitHub: {exc}"}, {}
    meta = dict(res.headers)
    limited = res.status_code == 429 or (
        res.status_code == 403 and meta.get("x-ratelimit-remaining") == "0"
    )
    if limited:
        return {"error": "GitHub rate limit reached", "status": 429}, meta
    if res.status_code == 401 and token:
        return {
            "error": "GitHub rejected the stored token — reconnect GitHub from the home page.",
            "status": 401,
        }, meta
    if res.status_code >= 400:
        detail = ""
        try:
            detail = str(res.json().get("message") or "")
        except ValueError:
            detail = res.text[:200]
        return {
            "error": f"GitHub returned {res.status_code}: {detail}",
            "status": res.status_code,
        }, meta
    return (res.json() if res.content else {}), meta
