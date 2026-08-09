"""Agent tools that reach out to a connected mobile device."""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.network.hub import peer_hub
from backend.modules.network import protocol
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


def _mobile_peers() -> list[Any]:
    return [p for p in peer_hub.list_peers() if "mobile" in p.capabilities]


def _pick_mobile(device: str | None) -> tuple[Any | None, dict[str, Any] | None]:
    """Which phone a `mobile.*` tool acts on. Returns (peer, error).

    These used to take the **first** phone on the fabric, which is fine with one
    device and silently wrong with two — the moment a person links a tablet, "take
    a photo" starts firing whichever peer happened to sort first. So: with one
    phone, it is unambiguous and no argument is needed; with several, the tool
    refuses and lists them rather than guessing. `device` matches a node id or a
    label, case-insensitively.
    """
    peers = _mobile_peers()
    if not peers:
        return None, {"error": "No mobile device connected."}
    if device:
        wanted = device.strip().lower()
        match = [
            p
            for p in peers
            if p.node_id.lower() == wanted
            or str(getattr(p, "name", "")).lower() == wanted
        ]
        if not match:
            return None, {
                "error": f"no connected phone matching {device!r}",
                "devices": [p.node_id for p in peers],
            }
        return match[0], None
    if len(peers) > 1:
        return None, {
            "error": "several phones are connected — pass `device` to say which",
            "devices": [
                {"node_id": p.node_id, "name": getattr(p, "name", None)} for p in peers
            ],
        }
    return peers[0], None


async def capture_photo(args: dict[str, Any]) -> dict[str, Any]:
    """Take a photo using the connected phone's camera."""
    mobile, error = _pick_mobile(args.get("device"))
    if error:
        return error
    assert mobile is not None

    try:
        reply = await peer_hub.request(
            mobile.node_id,
            protocol.REMOTE_COMMAND,
            {"command": "capture_photo", "params": {}},
            timeout=30.0,
        )
        if reply.data.get("ok"):
            return {
                "image_data": reply.data.get("image_data"),
                "note": "Photo captured from phone.",
            }
        return {"error": reply.data.get("error", "Capture failed.")}
    except Exception as e:
        return {"error": str(e)}


async def notify_mobile(args: dict[str, Any]) -> dict[str, Any]:
    """Send a push notification and vibrate the connected phone."""
    text = str(args.get("text") or "Notification from Desktop")
    mobile, error = _pick_mobile(args.get("device"))
    if error:
        return error
    assert mobile is not None

    await peer_hub.send_to(
        mobile.node_id,
        protocol.REMOTE_COMMAND,
        {"command": "notify", "params": {"text": text}},
    )
    return {"ok": True, "note": f"Notification sent: {text}"}


def register_mobile_tools() -> None:
    registry.agent_tools["mobile.capture_photo"] = AgentTool(
        name="mobile.capture_photo",
        description="Take a photo using the connected phone's camera.",
        handler=capture_photo,
        group="mobile",
        parameters={
            "device": {
                "type": "string",
                "description": (
                    "Which phone (node id or name). Optional when only one is "
                    "connected; required when several are."
                ),
            },
        },
        side_effect=True,
    )
    registry.agent_tools["mobile.notify"] = AgentTool(
        name="mobile.notify",
        description="Send a push notification and vibrate the connected phone.",
        handler=notify_mobile,
        group="mobile",
        parameters={
            "text": {"type": "string", "description": "The notification text to show."},
            "device": {
                "type": "string",
                "description": (
                    "Which phone (node id or name). Optional when only one is "
                    "connected; required when several are."
                ),
            },
        },
        required=["text"],
        side_effect=True,
    )
