"""horrible-dashboard backend: the app's brain, serving both layouts."""

import asyncio
import logging
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent.parent / "backend.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(_LOG_PATH), logging.StreamHandler()],
)

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend.modules.agent import router as agent_router
from backend.modules.agent.orchestrator import handle_agent_message
from backend.modules.chat import router as chat_router
from backend.modules.clubhouse import router as clubhouse_router
from backend.modules.files import router as files_router
from backend.modules.files.watcher import push_file_events
from backend.modules.lsp import LspManager
from backend.modules.network import (
    chat_manager,
    collab_manager,
    handle_chat_message,
    handle_collab_message,
    handle_network_message,
    subscribe_conn,
)
from backend.modules.network import router as network_router
from backend.modules.network.hub import peer_hub
from backend.modules.network.setup import start_network, stop_network
from backend.modules.network.transport.direct import ServerPeerLink
from backend.modules.notes import router as notes_router
from backend.modules.plugins import router as plugins_router
from backend.modules.repl import ReplManager
from backend.modules.settings import router as settings_router
from backend.modules.telemetry import push_telemetry
from backend.modules.telemetry import router as telemetry_router
from backend.modules.telemetry.instrument import telemetry_middleware
from backend.modules.terminal import TerminalManager
from backend.modules.workspace import router as workspace_router
from backend.modules.vectordb import router as vectordb_router
from backend.modules.visualizer import visualizer_manager
from backend.modules.ws import WsConnection

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bring the peer fabric up so this node can reach (and be reached by) others.
    await start_network()
    try:
        yield
    finally:
        await stop_network()


app = FastAPI(title="horrible-dashboard", lifespan=lifespan)

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
app.include_router(vectordb_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(clubhouse_router, prefix="/api")
app.include_router(telemetry_router, prefix="/api")
app.include_router(plugins_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(network_router, prefix="/api")


@app.websocket("/peer-ws")
async def peer_ws(websocket: WebSocket) -> None:
    """Inbound peer connections. Distinct from the user-facing `/ws`: this socket
    speaks the signed `PeerEnvelope` protocol between backend nodes, not the
    browser channel protocol. The handshake + read pump live in `PeerHub`."""
    await websocket.accept()
    link = ServerPeerLink(websocket)
    session = await peer_hub.accept_link(link)
    if session is None:
        return  # handshake/trust rejected; link already closed
    # accept_link started the read pump; block until the link closes so FastAPI
    # keeps the socket open.
    try:
        await session.closed.wait()
    except Exception:
        pass


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    """Shared multiplexed socket: greets, pushes telemetry, and routes inbound
    channel messages (the `agent` orchestrator, `network` peer control, …). One
    receive loop owns reads; outbound work runs as tasks that send through the
    connection lock."""
    await websocket.accept()
    await websocket.send_json(
        {"channel": "system", "event": "hello", "version": APP_VERSION}
    )
    conn = WsConnection(websocket)
    terminals = TerminalManager(conn)
    repl = ReplManager(conn)
    lsp = LspManager(conn)
    telemetry_task = asyncio.create_task(push_telemetry(conn))
    files_task = asyncio.create_task(push_file_events(conn))
    # Fan peer/presence events from the process-global hub out to this browser.
    network_unsub = subscribe_conn(conn)
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
            elif channel == "repl":
                await repl.handle(msg)
            elif channel == "lsp":
                await lsp.handle(msg)
            elif channel == "visualizer":
                await visualizer_manager.handle(conn, msg)
            elif channel == "network":
                await handle_network_message(conn, msg)
            elif channel == "collab":
                await handle_collab_message(conn, msg)
            elif channel == "peerchat":
                await handle_chat_message(conn, msg)
    except WebSocketDisconnect:
        pass
    finally:
        await terminals.close_all()
        await repl.close_all()
        await lsp.close_all()
        visualizer_manager.stop_for(conn)
        telemetry_task.cancel()
        files_task.cancel()
        network_unsub()  # type: ignore[operator]
        collab_manager.drop(conn)
        chat_manager.drop(conn)
