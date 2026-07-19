"""horrible-dashboard backend: the app's brain, serving both layouts."""

import asyncio
import logging
from pathlib import Path
import os

# Auto-enable full browser engine if Playwright is installed and not explicitly disabled.
if "HORRIBLE_ENABLE_SERVER_BROWSER" not in os.environ:
    try:
        import playwright

        os.environ["HORRIBLE_ENABLE_SERVER_BROWSER"] = "1"
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            content = env_path.read_text()
            if "HORRIBLE_ENABLE_SERVER_BROWSER" not in content:
                with open(env_path, "a") as f:
                    f.write("\nHORRIBLE_ENABLE_SERVER_BROWSER=1\n")
    except ImportError:
        pass

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_LOG_PATH = _LOG_DIR / "backend.log"

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
from backend.modules.browser import router as browser_router
from backend.modules.chat import router as chat_router
from backend.modules.clubhouse import router as clubhouse_router
from backend.modules.code import handle_code_message, push_code_events
from backend.modules.code import router as code_router
from backend.modules.git import router as git_router
from backend.modules.files import router as files_router
from backend.modules.files.watcher import push_file_events
from backend.modules.flow import handle_flow_message
from backend.modules.flow import router as flow_router
from backend.modules.games import drop_games_conn, handle_games_message
from backend.modules.games import register_agent_tools as register_games_tools
from backend.modules.games import router as games_router
from backend.modules.connectors import github_router as github_connector_router
from backend.modules.connectors import google_router as google_connector_router
from backend.modules.connectors import register_connectors
from backend.modules.connectors import router as connectors_router
from backend.modules.library import push_library_events
from backend.modules.library import router as library_router
from backend.modules.lsp import LspManager
from backend.modules.lsp import router as lsp_router
from backend.modules.network import (
    chat_manager,
    collab_manager,
    handle_chat_message,
    handle_collab_message,
    handle_commons_message,
    handle_lobby_message,
    handle_network_message,
    subscribe_commons_conn,
    subscribe_conn,
    subscribe_lobby_conn,
)
from backend.modules.network import router as network_router
from backend.modules.network.hub import peer_hub
from backend.modules.network.setup import start_network, stop_network
from backend.modules.network.transport.direct import ServerPeerLink
from backend.modules.notebook import handle_notebook_message, notebook_manager
from backend.modules.notebook import router as notebook_router
from backend.modules.notes import router as notes_router
from backend.modules.plugins import router as plugins_router
from backend.modules.repl import ReplManager
from backend.modules.settings import router as settings_router
from backend.modules.secrets import router as secrets_router
from backend.modules.telemetry import push_telemetry
from backend.modules.telemetry import router as telemetry_router
from backend.modules.telemetry.instrument import record_ws_frame, telemetry_middleware
from backend.modules.terminal import TerminalManager
from backend.modules.training import register_agent_tools as register_training_tools
from backend.modules.training import router as training_router
from backend.modules.training import subscribe_training_conn
from backend.modules.training.kernels import (
    handle_training_message,
    training_kernels,
)
from backend.modules.workspace import router as workspace_router
from backend.modules.database import router as database_router
from backend.modules.visualizer import visualizer_manager
from backend.modules.browser.session import browser_manager
from backend.modules.ws import WsConnection, set_ws_send_observer
from backend.sdk import load_plugins
from backend.sdk import registry as plugin_registry
from backend.modules.tasks import queue

# Observe every outbound `/ws` frame for the observability panel (inbound frames
# are recorded in the receive loop below). One global observer covers all sockets.
set_ws_send_observer(record_ws_frame)

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bring the peer fabric up so this node can reach (and be reached by) others.
    await start_network()
    await plugin_registry.run_startup()  # backend plugins' startup hooks
    queue.start()
    try:
        yield
    finally:
        queue.stop()
        await plugin_registry.run_shutdown()
        # Kernels are child processes; leaving them behind on reload/shutdown
        # would strand orphaned ipykernels.
        await training_kernels.shutdown_all()
        await notebook_manager.shutdown_all()
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
app.include_router(database_router, prefix="/api")
app.include_router(library_router, prefix="/api")
app.include_router(connectors_router, prefix="/api")
app.include_router(google_connector_router, prefix="/api")
app.include_router(github_connector_router, prefix="/api")
app.include_router(browser_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(notebook_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(clubhouse_router, prefix="/api")
app.include_router(telemetry_router, prefix="/api")
app.include_router(plugins_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(secrets_router, prefix="/api")
app.include_router(flow_router, prefix="/api")
app.include_router(network_router, prefix="/api")
app.include_router(training_router, prefix="/api")
app.include_router(lsp_router, prefix="/api")
app.include_router(games_router, prefix="/api")
app.include_router(code_router, prefix="/api")
app.include_router(git_router, prefix="/api")

# Register the training module's backend agent tools into the sdk registry (the
# training module is a first-party consumer of the same registry backend plugins
# write to). Grouped under `training`, disclosed progressively by the orchestrator.
register_training_tools()

# Register the games module's backend agent tools (grouped under `games`); the
# manual-play seat drives its move through game.getObservation/game.chooseAction.
register_games_tools()

# Register the built-in connectors (GitHub, …) and the agent tools they unlock. Each
# connector's tools are grouped under its id and disclosed progressively.
register_connectors()

# Discover and mount backend plugins (bundled, HORRIBLE_PLUGINS_DIR, and pip entry
# points). Ships empty; each plugin's routes mount under /api + its prefix. Agent
# tools, /ws channels, dash facades, and lifespan hooks are read from the registry
# where they're used (orchestrator, /ws loop, repl, lifespan).
load_plugins()
for _mounted in plugin_registry.routers:
    app.include_router(_mounted.router, prefix=f"/api{_mounted.prefix}")


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
    # Fan library ingestion status (queued→…→ready/failed) to this browser.
    library_task = asyncio.create_task(push_library_events(conn))
    # Fan code-locus updates (dash/agent-set, and cross-window sync) to this browser.
    code_task = asyncio.create_task(push_code_events(conn))
    # Fan peer/presence events from the process-global hub out to this browser.
    network_unsub = subscribe_conn(conn)
    # Fan lobby (directory/rooms) events out to this browser.
    lobby_unsub = subscribe_lobby_conn(conn)
    # Fan commons (profiles/search) events out to this browser.
    commons_unsub = subscribe_commons_conn(conn)
    # Fan training events (venv/fetch progress, metrics, frames) to this browser.
    training_unsub = subscribe_training_conn(conn)
    try:
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            record_ws_frame("in", msg)
            channel = msg.get("channel")
            if channel == "agent":
                await handle_agent_message(conn, msg)
            elif channel == "flow":
                await handle_flow_message(conn, msg)
            elif channel == "terminal":
                await terminals.handle(msg)
            elif channel == "repl":
                await repl.handle(msg)
            elif channel == "lsp":
                await lsp.handle(msg)
            elif channel == "visualizer":
                await visualizer_manager.handle(conn, msg)
            elif channel == "browser":
                await browser_manager.handle(conn, msg)
            elif channel == "training":
                await handle_training_message(conn, msg)
            elif channel == "notebook":
                await handle_notebook_message(conn, msg)
            elif channel == "network":
                await handle_network_message(conn, msg)
            elif channel == "collab":
                await handle_collab_message(conn, msg)
            elif channel == "games":
                await handle_games_message(conn, msg)
            elif channel == "code":
                await handle_code_message(conn, msg)
            elif channel == "lobby":
                await handle_lobby_message(conn, msg)
            elif channel == "commons":
                await handle_commons_message(conn, msg)
            elif channel == "peerchat":
                await handle_chat_message(conn, msg)
            else:
                # Unknown built-in channel — offer it to backend plugins.
                await plugin_registry.dispatch_ws(conn, str(channel), msg)
    except WebSocketDisconnect:
        pass
    finally:
        await terminals.close_all()
        await repl.close_all()
        await lsp.close_all()
        visualizer_manager.stop_for(conn)
        browser_manager.stop_for(conn)
        telemetry_task.cancel()
        files_task.cancel()
        library_task.cancel()
        code_task.cancel()
        network_unsub()  # type: ignore[operator]
        lobby_unsub()  # type: ignore[operator]
        commons_unsub()  # type: ignore[operator]
        training_unsub()
        training_kernels.detach(conn)
        notebook_manager.detach(conn)
        collab_manager.drop(conn)
        chat_manager.drop(conn)
        drop_games_conn(conn)
