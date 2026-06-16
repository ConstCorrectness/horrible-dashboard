"""horrible-dashboard backend: the app's brain, serving both layouts."""

import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.modules.agent import router as agent_router
from backend.modules.agent.orchestrator import handle_agent_message
from backend.modules.clubhouse import router as clubhouse_router
from backend.modules.files import router as files_router
from backend.modules.notes import router as notes_router
from backend.modules.plugins import router as plugins_router
from backend.modules.settings import router as settings_router
from backend.modules.telemetry import push_telemetry
from backend.modules.telemetry import router as telemetry_router
from backend.modules.telemetry.instrument import telemetry_middleware
from backend.modules.terminal import TerminalManager
from backend.modules.workspace import router as workspace_router
from backend.modules.ws import WsConnection

APP_VERSION = "0.1.0"

app = FastAPI(title="horrible-dashboard")

# Browser layout dev server and Tauri webview origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Observe every inbound /api request (metadata only) — see modules/telemetry.
app.middleware("http")(telemetry_middleware)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "horrible-dashboard", "version": APP_VERSION}


app.include_router(agent_router, prefix="/api")
app.include_router(workspace_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(clubhouse_router, prefix="/api")
app.include_router(telemetry_router, prefix="/api")
app.include_router(plugins_router, prefix="/api")
app.include_router(settings_router, prefix="/api")


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """Shared multiplexed socket: greets, pushes telemetry, and routes inbound
    channel messages (currently the `agent` orchestrator). One receive loop owns
    reads; outbound work runs as tasks that send through the connection lock."""
    await websocket.accept()
    await websocket.send_json(
        {"channel": "system", "event": "hello", "version": APP_VERSION}
    )
    conn = WsConnection(websocket)
    terminals = TerminalManager(conn)
    telemetry_task = asyncio.create_task(push_telemetry(conn))
    try:
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            channel = msg.get("channel")
            if channel == "agent":
                await handle_agent_message(conn, msg)
            elif channel == "terminal":
                await terminals.handle(msg)
    except WebSocketDisconnect:
        pass
    finally:
        await terminals.close_all()
        telemetry_task.cancel()
