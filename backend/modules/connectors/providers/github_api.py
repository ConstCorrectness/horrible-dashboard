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
