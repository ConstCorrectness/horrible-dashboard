"""The `audio` agent tool group.

Backend tools, so they work with no mixer pane open — the routing lives in the
database and every pane reconciles to it (`events.publish_mixer`). "Send the
video to my microphone but keep my voice out of my headphones" is one sentence
and should not require finding a pane first.

**Four tools, and the split is by what someone asks for, not by HTTP route.**
Tools cost context on every turn their group is loaded, so `audio.route` absorbs
every "send X to Y" phrasing across both mixers rather than being two tools, and
`audio.level` absorbs gain and mute together because "turn the music down" and
"mute the music" are the same intent at different amounts.

**Why `target` exists.** There are genuinely two mixers on a Windows box and they
are not interchangeable: the *dashboard* mixer routes audio this app produces,
the *host* mixer (Voicemeeter) routes every application on the machine. Guessing
between them silently would mean "route Spotify to my headphones" quietly doing
nothing. The tool asks, and `audio.status` tells the model which exist.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.modules.audio import events, providers, store, voicemeeter
from backend.sdk.registry import registry
from backend.sdk.types import AgentTool

logger = logging.getLogger(__name__)

_GROUP = "audio"


def _dashboard_summary(state: dict[str, Any]) -> dict[str, Any]:
    """The routing matrix, flattened for a language model.

    A nested strip/bus structure makes a model re-derive which sends are on. This
    reports each strip's *enabled* sends as a list, which is the form the answer
    is usually given in ("the mic goes to B1").
    """
    buses = [
        {
            "id": bus.get("id"),
            "label": bus.get("label"),
            "device": bus.get("deviceLabel") or "system default",
            "virtual": bool(bus.get("virtual")),
            "muted": bool(bus.get("muted")),
        }
        for bus in state.get("buses", [])
    ]
    strips = []
    for strip in state.get("strips", []):
        sends = strip.get("sends") or {}
        strips.append(
            {
                "id": strip.get("id"),
                "label": strip.get("label"),
                "muted": bool(strip.get("muted")),
                "goesTo": sorted(name for name, on in sends.items() if on),
            }
        )
    return {"buses": buses, "strips": strips}


async def _status(_args: dict[str, Any]) -> dict[str, Any]:
    provider = await asyncio.to_thread(lambda: providers.get_provider().status())
    state = await asyncio.to_thread(store.load_state)

    out: dict[str, Any] = {
        "platform": provider.platform,
        "dashboardMixer": _dashboard_summary(state),
        # Spelled out rather than left implicit: a model that cannot tell
        # "no virtual device" from "we could not check" will confidently tell
        # the user they need to install something they already have.
        "virtualAudio": {
            "provider": provider.provider,
            "installed": provider.installed if provider.certain else "unknown",
            "running": provider.running,
            "canRouteOtherApps": provider.can_control,
            "canCreateDevices": provider.can_create,
            "note": provider.note,
            "devices": [d.name for d in provider.devices],
        },
    }
    if provider.can_control:
        try:
            host = await asyncio.to_thread(voicemeeter.read_state)
            out["hostMixer"] = {
                "kind": host.kind,
                "strips": [
                    {
                        "index": s.index,
                        "label": s.label,
                        "virtual": s.is_virtual,
                        "muted": s.muted,
                        "goesTo": sorted(n for n, on in s.sends.items() if on),
                    }
                    for s in host.strips
                ],
                "buses": [
                    {"index": b.index, "name": b.name, "label": b.label}
                    for b in host.buses
                ],
            }
        except voicemeeter.VoicemeeterError as exc:
            out["hostMixer"] = {"error": str(exc)}
    return out


async def _route(args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("target") or "dashboard").lower()
    bus = str(args.get("bus") or "").strip()
    strip = str(args.get("strip") or "").strip()
    enabled = bool(args.get("enabled", True))
    if not bus or not strip:
        return {"error": "both `strip` and `bus` are required"}

    if target == "host":
        try:
            index = int(strip)
        except ValueError:
            return {
                "error": f"host strips are numbered; got {strip!r}. Call audio.status for the list."
            }
        try:
            await asyncio.to_thread(voicemeeter.set_send, index, bus, enabled)
            state = await asyncio.to_thread(voicemeeter.read_state)
        except voicemeeter.VoicemeeterError as exc:
            return {"error": str(exc)}
        await events.publish_host(state.to_dict())
        return {
            "ok": True,
            "target": "host",
            "strip": index,
            "bus": bus,
            "enabled": enabled,
        }

    state = await asyncio.to_thread(store.load_state)
    bus_ids = {str(b.get("id")) for b in state.get("buses", [])}
    if bus not in bus_ids:
        return {
            "error": f"no bus {bus!r}; this mixer has {', '.join(sorted(bus_ids)) or 'none'}"
        }
    for entry in state.get("strips", []):
        if str(entry.get("id")) == strip:
            entry.setdefault("sends", {})[bus] = enabled
            break
    else:
        return {"error": f"no audio source {strip!r}. Call audio.status for the list."}

    saved = await asyncio.to_thread(store.save_state, state)
    await events.publish_mixer(saved)
    return {
        "ok": True,
        "target": "dashboard",
        "strip": strip,
        "bus": bus,
        "enabled": enabled,
    }


async def _level(args: dict[str, Any]) -> dict[str, Any]:
    target = str(args.get("target") or "dashboard").lower()
    kind = str(args.get("kind") or "strip").lower()
    ident = str(args.get("id") or "").strip()
    gain = args.get("gainDb")
    muted = args.get("muted")
    if not ident:
        return {"error": "`id` is required"}
    if gain is None and muted is None:
        return {"error": "give `gainDb`, `muted`, or both"}

    if target == "host":
        try:
            index = int(ident)
        except ValueError:
            return {"error": f"host strips and buses are numbered; got {ident!r}"}
        try:
            if gain is not None:
                setter = (
                    voicemeeter.set_strip_gain
                    if kind == "strip"
                    else voicemeeter.set_bus_gain
                )
                await asyncio.to_thread(setter, index, float(gain))
            if muted is not None:
                setter = (
                    voicemeeter.set_strip_mute
                    if kind == "strip"
                    else voicemeeter.set_bus_mute
                )
                await asyncio.to_thread(setter, index, bool(muted))
            state = await asyncio.to_thread(voicemeeter.read_state)
        except voicemeeter.VoicemeeterError as exc:
            return {"error": str(exc)}
        await events.publish_host(state.to_dict())
        return {"ok": True, "target": "host", "kind": kind, "id": index}

    state = await asyncio.to_thread(store.load_state)
    collection = state.get("strips" if kind == "strip" else "buses", [])
    for entry in collection:
        if str(entry.get("id")) == ident:
            if gain is not None:
                # Clamped to the same range the faders offer. An unclamped +40 dB
                # from a model is a painful noise, not a louder one.
                entry["gain"] = max(-60.0, min(12.0, float(gain)))
            if muted is not None:
                entry["muted"] = bool(muted)
            break
    else:
        return {"error": f"no {kind} {ident!r}. Call audio.status for the list."}

    saved = await asyncio.to_thread(store.save_state, state)
    await events.publish_mixer(saved)
    return {"ok": True, "target": "dashboard", "kind": kind, "id": ident}


async def _start_host(_args: dict[str, Any]) -> dict[str, Any]:
    """Launch the host mixer.

    A side effect worth its own tool rather than an implicit step inside
    `audio.route`: starting Voicemeeter takes over the machine's default audio
    devices, which every running application hears immediately.
    """
    if not voicemeeter.is_installed():
        status = await asyncio.to_thread(lambda: providers.get_provider().status())
        return {
            "error": "Voicemeeter is not installed on this machine",
            "install": status.install_url,
        }
    try:
        await asyncio.to_thread(voicemeeter.launch, None)
        state = await asyncio.to_thread(voicemeeter.read_state)
    except voicemeeter.VoicemeeterError as exc:
        return {"error": str(exc)}
    await events.publish_host(state.to_dict())
    return {"ok": True, "kind": state.kind}


_TOOLS = [
    AgentTool(
        name="audio.status",
        description=(
            "List audio routing: the dashboard's own mixer (which app sounds go to which output), "
            "and — where available — the machine-wide mixer that routes every application. "
            "Call this before routing anything: it names the strips and buses the other audio tools take."
        ),
        handler=_status,
        group=_GROUP,
    ),
    AgentTool(
        name="audio.route",
        description=(
            "Send one audio source to one output, or stop sending it. A source can go to several outputs at once — "
            "that is how you play something into a microphone while still hearing it yourself. "
            "target='dashboard' routes sounds this app makes; target='host' routes every application on the machine "
            "(needs Voicemeeter running)."
        ),
        handler=_route,
        parameters={
            "strip": {
                "type": "string",
                "description": "The audio source, from audio.status (e.g. 'mic', 'media'). For target='host', its number.",
            },
            "bus": {
                "type": "string",
                "description": "The output, from audio.status (e.g. 'A1' for speakers, 'B1' for a virtual microphone).",
            },
            "enabled": {
                "type": "boolean",
                "description": "True to send, false to stop sending. Default true.",
            },
            "target": {
                "type": "string",
                "enum": ["dashboard", "host"],
                "description": "Which mixer. Default 'dashboard'.",
            },
        },
        required=["strip", "bus"],
        side_effect=True,
        specifier_template="{strip} → {bus}",
        group=_GROUP,
    ),
    AgentTool(
        name="audio.level",
        description="Set the volume (in dB, 0 is unity) and/or mute of one audio source or output.",
        handler=_level,
        parameters={
            "id": {
                "type": "string",
                "description": "The source or output id from audio.status.",
            },
            "kind": {
                "type": "string",
                "enum": ["strip", "bus"],
                "description": "'strip' (a source) or 'bus' (an output). Default 'strip'.",
            },
            "gainDb": {
                "type": "number",
                "description": "Volume in dB, -60 to +12. 0 is unchanged loudness.",
            },
            "muted": {"type": "boolean", "description": "Mute or unmute."},
            "target": {
                "type": "string",
                "enum": ["dashboard", "host"],
                "description": "Which mixer. Default 'dashboard'.",
            },
        },
        required=["id"],
        side_effect=True,
        specifier_template="{id}",
        group=_GROUP,
    ),
    AgentTool(
        name="audio.start_host_mixer",
        description=(
            "Start the machine-wide audio mixer (Voicemeeter) so other applications' audio can be routed. "
            "This changes the machine's default audio devices, so every running application is affected."
        ),
        handler=_start_host,
        side_effect=True,
        group=_GROUP,
    ),
]


def register_agent_tools() -> None:
    """Register the group. Called from `backend/app.py` at startup."""
    for tool in _TOOLS:
        registry.agent_tools[tool.name] = tool
