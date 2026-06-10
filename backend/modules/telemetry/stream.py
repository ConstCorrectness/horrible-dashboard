import asyncio

from fastapi import WebSocket, WebSocketDisconnect

from backend.modules.telemetry.recorder import recorder


def _envelope(event_dict: dict) -> dict:
    return {"channel": "telemetry", "event": "io", "data": event_dict}


async def stream_telemetry(websocket: WebSocket) -> None:
    """Send the recent backlog, then stream new I/O events until disconnect.

    Assumes the socket is already accepted (the shared /ws handler greets first).
    """
    for event in recorder.recent():
        await websocket.send_json(_envelope(event.model_dump()))

    queue = recorder.subscribe()

    async def watch_disconnect() -> None:
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return

    receiver = asyncio.create_task(watch_disconnect())
    try:
        while True:
            getter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {getter, receiver}, return_when=asyncio.FIRST_COMPLETED
            )
            if receiver in done:
                getter.cancel()
                break
            await websocket.send_json(_envelope(getter.result().model_dump()))
    finally:
        recorder.unsubscribe(queue)
        if not receiver.done():
            receiver.cancel()
