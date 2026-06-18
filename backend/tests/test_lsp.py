"""Tests for the LSP transport manager.

The manager is a dumb JSON-RPC pipe, so the deterministic surface worth covering
without a real language server is the **gate** (only known, installed servers
spawn) and the stdio **framing**. The full pipe is exercised at runtime against
pylsp/typescript-language-server.
"""

import asyncio
import json
from typing import Any

from backend.modules.lsp import manager as lsp_mod
from backend.modules.lsp.manager import LspManager, _frame


class FakeConn:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


def _events(conn: FakeConn, event: str) -> list[dict[str, Any]]:
    return [
        m["data"]
        for m in conn.sent
        if m.get("channel") == "lsp" and m.get("event") == event
    ]


def test_unknown_language_errors_without_spawning() -> None:
    conn = FakeConn()
    mgr = LspManager(conn)

    async def go() -> None:
        await mgr.handle(
            {
                "channel": "lsp",
                "event": "start",
                "data": {"sessionId": "s1", "languageId": "cobol"},
            }
        )

    asyncio.run(go())
    errors = _events(conn, "error")
    assert errors and errors[0]["sessionId"] == "s1"
    assert "cobol" in errors[0]["message"]
    assert not mgr.sessions  # nothing spawned


def test_missing_server_binary_errors(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(lsp_mod.shutil, "which", lambda _cmd: None)
    conn = FakeConn()
    mgr = LspManager(conn)

    async def go() -> None:
        await mgr.handle(
            {
                "channel": "lsp",
                "event": "start",
                "data": {"sessionId": "s2", "languageId": "python"},
            }
        )

    asyncio.run(go())
    assert _events(conn, "error")
    assert not mgr.sessions


def test_frame_uses_content_length_header() -> None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    framed = _frame(payload)
    head, _, body = framed.partition(b"\r\n\r\n")
    assert head == b"Content-Length: " + str(len(body)).encode()
    assert json.loads(body) == payload


def test_python_maps_to_pylsp() -> None:
    assert lsp_mod.LSP_SERVERS["python"][0] == "pylsp"
