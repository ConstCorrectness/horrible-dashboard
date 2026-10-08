"""The instrumented client survives redirects.

A redirect hop's request is an unread stream (there is no body to send); the
response hook used to read `request.content` and raise `RequestNotRead`, which failed
every redirected call made through `instrumented_client` — Hub file downloads among
them (they redirect to a CDN).
"""

from __future__ import annotations

import asyncio

import httpx

from backend.modules.telemetry.instrument import instrumented_client


def test_a_followed_redirect_does_not_break_the_hook() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://cdn.example/file"})
        return httpx.Response(200, content=b"payload")

    async def go() -> httpx.Response:
        async with instrumented_client(transport=httpx.MockTransport(handler)) as client:
            return await client.get("https://hub.example/start", follow_redirects=True)

    res = asyncio.run(go())
    assert res.status_code == 200 and res.content == b"payload"
