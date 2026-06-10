"""horrible-dashboard backend: the app's brain, serving both layouts."""

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from backend.modules.agent import router as agent_router
from backend.modules.clubhouse import router as clubhouse_router
from backend.modules.dashboard import router as dashboard_router
from backend.modules.telemetry import router as telemetry_router
from backend.modules.telemetry import stream_telemetry
from backend.modules.telemetry.instrument import telemetry_middleware
from backend.modules.workspace import router as workspace_router

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


app.include_router(dashboard_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(workspace_router, prefix="/api")
app.include_router(clubhouse_router, prefix="/api")
app.include_router(telemetry_router, prefix="/api")


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """Shared multiplexed socket: greets, then streams the telemetry channel."""
    await websocket.accept()
    await websocket.send_json(
        {"channel": "system", "event": "hello", "version": APP_VERSION}
    )
    await stream_telemetry(websocket)
