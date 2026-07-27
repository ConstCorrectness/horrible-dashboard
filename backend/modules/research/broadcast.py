"""Live deep-research progress on the `research` `/ws` channel.

Cloned from the library's recorder/stream split: a process-global broadcaster the
runner publishes to; each `/ws` connection runs `push_research_events` to forward.
Events: `run` and `step` are full snapshots (frontend upserts by id, ordering
doesn't matter) and `delta` carries streamed synthesis text, throttled at the
producer (~4 Hz) so a fast model doesn't flood the socket.
"""

from __future__ import annotations

import asyncio
from typing import Any


class ResearchBroadcaster:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass


research_events = ResearchBroadcaster()


def publish_run(run: dict[str, Any]) -> None:
    research_events.publish({"event": "run", "data": run})


def publish_step(step: dict[str, Any]) -> None:
    research_events.publish({"event": "step", "data": step})


def publish_delta(run_id: str, step_id: str, text: str) -> None:
    research_events.publish(
        {"event": "delta", "data": {"run_id": run_id, "step_id": step_id, "text": text}}
    )


def publish_tool_call(run_id: str, step_id: str, payload: dict[str, Any]) -> None:
    """One subagent tool call, live.

    A step's transcript is only persisted when the step *finishes*, so without this
    the console shows nothing for a subagent for minutes — exactly the window in
    which you want to know whether it's searching sensibly or looping.
    """
    research_events.publish(
        {"event": "tool", "data": {"run_id": run_id, "step_id": step_id, **payload}}
    )


async def push_research_events(conn: Any) -> None:
    queue = research_events.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(
                {"channel": "research", "event": event["event"], "data": event["data"]}
            )
    finally:
        research_events.unsubscribe(queue)
