"""Agent tools that reach out to a connected mobile device."""

from __future__ import annotations

import logging
from typing import Any

from backend.modules.network.hub import peer_hub
from backend.modules.network import protocol
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)


async def capture_photo(_args: dict[str, Any]) -> dict[str, Any]:
    """Take a photo using the connected phone's camera."""
    peers = peer_hub.list_peers()
    mobile = next((p for p in peers if "mobile" in p.capabilities), None)
    if not mobile:
        return {"error": "No mobile device connected."}

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
    peers = peer_hub.list_peers()
    mobile = next((p for p in peers if "mobile" in p.capabilities), None)
    if not mobile:
        return {"error": "No mobile device connected."}

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
        side_effect=True,
    )
    registry.agent_tools["mobile.notify"] = AgentTool(
        name="mobile.notify",
        description="Send a push notification and vibrate the connected phone.",
        handler=notify_mobile,
        group="mobile",
        parameters={
            "text": {"type": "string", "description": "The notification text to show."}
        },
        required=["text"],
        side_effect=True,
    )
