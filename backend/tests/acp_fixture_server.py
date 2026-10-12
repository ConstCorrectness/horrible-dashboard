"""A tiny stdio ACP server used as a fixture by test_acp_client.py.

Runs as a subprocess by the ACP integration tests so the transport, JSON-RPC
handshake, session streaming, and fs read/write operations are exercised
against real stdio pipes.
"""

import json
import sys


def reply(req_id: int | str, result: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n")
    sys.stdout.flush()


def notify(method: str, params: dict) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue

        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}

        if method == "initialize":
            reply(req_id, {
                "protocolVersion": 1,
                "agentInfo": {"name": "fixture-acp-agent", "version": "1.0.0"},
                "capabilities": {"fs": True},
            })
        elif method == "session/new":
            reply(req_id, {"sessionId": "test-acp-session-42"})
        elif method == "session/prompt":
            # Stream reasoning chunk
            notify("session/update", {
                "sessionId": params.get("sessionId"),
                "update": {"sessionUpdate": "thought_chunk", "delta": "Thinking about it..."},
            })
            # Stream text chunk
            notify("session/update", {
                "sessionId": params.get("sessionId"),
                "update": {"sessionUpdate": "agent_message_chunk", "delta": "Hello from ACP agent!"},
            })
            reply(req_id, {"stopReason": "end_turn"})
        elif method == "session/cancel":
            pass


if __name__ == "__main__":
    main()
