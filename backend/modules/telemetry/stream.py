from backend.modules.telemetry.recorder import recorder
from backend.modules.ws import WsConnection


def _envelope(event_dict: dict) -> dict:
    return {"channel": "telemetry", "event": "io", "data": event_dict}


async def push_telemetry(conn: WsConnection) -> None:
    """Send the recent backlog, then push new I/O events until cancelled.

    Push-only: the shared `/ws` handler owns the single inbound receive loop and
    cancels this task on disconnect. Sends go through the connection's lock so
    they don't interleave with the agent channel.
    """
    for event in recorder.recent():
        await conn.send_json(_envelope(event.model_dump()))

    queue = recorder.subscribe()
    try:
        while True:
            event = await queue.get()
            await conn.send_json(_envelope(event.model_dump()))
    finally:
        recorder.unsubscribe(queue)
