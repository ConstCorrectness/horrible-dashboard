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

from backend import paths

# `<repo>/logs` in a checkout, the per-OS log directory in a packaged install —
# resolved from this file's location rather than the cwd, so it does not move when
# the launcher does. See `backend/paths.py`.
_LOG_DIR = paths.log_dir()
_LOG_DIR.mkdir(parents=True, exist_ok=True)
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
from backend.modules.clubhouse import voice_router as clubhouse_voice_router
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
from backend.modules.connectors import (
    huggingface_router as huggingface_connector_router,
)
from backend.modules.connectors import register_connectors
from backend.modules.connectors import router as connectors_router
from backend.modules.artifacts import router as artifacts_router
from backend.modules.arxiv import register_arxiv_tools
from backend.modules.arxiv import router as arxiv_router
from backend.modules.research import register_research_tools
from backend.modules.research import router as research_router
from backend.modules.research.broadcast import push_research_events
from backend.modules.research.runner import research_runner
from backend.modules.search import init_search_db, register_search_tools
from backend.modules.search import router as search_router
from backend.modules.search.broadcast import push_crawl_events
from backend.modules.search.crawl import queue_handlers as _crawl_queue_handlers  # noqa: F401 — registers the crawl task handler on import (see its docstring)
from backend.modules.library import push_library_events
from backend.modules.library import queue_handlers as _library_queue_handlers  # noqa: F401 — registers the ingest task handlers on import (see its docstring)
from backend.modules.audio import register_agent_tools as register_audio_tools
from backend.modules.audio import router as audio_router
from backend.modules.audio import shutdown_voicemeeter
from backend.modules.hardware import router as hardware_router
from backend.modules.interpretability import (
    register_agent_tools as register_model_designer_tools,
)
from backend.modules.interpretability import router as interpretability_router
from backend.modules.evals import register_agent_tools as register_evals_tools
from backend.modules.evals import router as evals_router
from backend.modules.trajectories import init_trajectories_db
from backend.modules.trajectories import (
    register_agent_tools as register_trajectories_tools,
)
from backend.modules.trajectories import router as trajectories_router
from backend.modules.karaoke import register_agent_tools as register_karaoke_tools
from backend.modules.karaoke import router as karaoke_router
from backend.modules.library import router as library_router
from backend.modules.llamacpp import router as llamacpp_router
from backend.modules.llamacpp.agent_tools import register_llamacpp_tools
from backend.modules.llamacpp.dash_facade import (
    register_dash_facade as register_lens_facade,
)
from backend.modules.llamacpp.locus import push_lens_events
from backend.modules.llamacpp.trace_catalog import sync as sync_trace_catalog
from backend.modules.llamacpp.server import llama_manager
from backend.modules.records import (
    init_records_db,
    push_records_events,
    register_records_tools,
)
from backend.modules.records import router as records_router
from backend.modules.lsp import LspManager
from backend.modules.lsp import router as lsp_router
from backend.modules.mcp import router as mcp_router
from backend.modules.skills import router as skills_router
from backend.modules.mcp import server as mcp_export
from backend.modules.mcp.client import manager as mcp_manager
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
from backend.modules.hassault import handle_hassault_message, hassault_on_disconnect
from backend.modules.hassault import router as hassault_router
from backend.modules.share import handle_share_message
from backend.modules.share import router as share_router
from backend.modules.social import handle_social_message, subscribe_social_conn
from backend.modules.social import router as social_router
from backend.modules.notebook import handle_notebook_message, notebook_manager
from backend.modules.docs import router as docs_router
from backend.modules.notebook import router as notebook_router
from backend.modules.keymap import router as keymap_router
from backend.modules.notes import router as notes_router
from backend.modules.plugins import router as plugins_router
from backend.modules.repl import ReplManager
from backend.modules.settings import router as settings_router
from backend.modules.secrets import router as secrets_router
from backend.modules.telemetry import push_telemetry
from backend.modules.notifications.routes import router as notifications_router
from backend.modules.telemetry import router as telemetry_router
from backend.modules.telemetry.instrument import record_ws_frame, telemetry_middleware
from backend.modules.terminal import TerminalManager
from backend.modules.symdex import push_symdex_events
from backend.modules.symdex import register_agent_tools as register_symdex_tools
from backend.modules.symdex import router as symdex_router
from backend.modules.training import register_agent_tools as register_training_tools
from backend.modules.training import router as training_router
from backend.modules.training import subscribe_training_conn
from backend.modules.training.kernels import (
    handle_training_message,
    training_kernels,
)
from backend.modules.localtrack import (
    register_agent_tools as register_localtrack_tools,
    router as localtrack_router,
    stream as localtrack_stream,
)
from backend.modules.desktop import router as desktop_router
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
    # Search caches + crawl tables, and the built-in seed list on first run.
    init_search_db()
    # Records catalog + proposal queue (the per-schema data tables are created on
    # demand, when a schema is defined).
    init_records_db()
    # Trajectory tables. Created eagerly rather than on first use so `traj_*`
    # shows up in the database console's built-in `app` connection on a fresh
    # install, before anyone has opened the pane that would create them lazily.
    init_trajectories_db()
    # Reconcile the activation-trace catalog with the trace directory. Eager for
    # the same reason as the line above, plus one of its own: a node that traced
    # before this table existed has directories and no rows, and an empty catalog
    # reads as "no traces" rather than as "not indexed yet".
    await asyncio.to_thread(sync_trace_catalog)
    # LocalTrack's live channel needs the loop captured before the first writer
    # runs, and the first writer is very likely a worker thread (the training
    # metrics pump), which has no running loop of its own to discover.
    localtrack_stream.init_loop()
    # Deep-research runner: resumes any run that was in flight when the process
    # last died (steps stuck `running` reset to `pending`), then works the queue.
    research_runner.start()
    # Connect enabled MCP servers and bridge their tools into the agent. Failures
    # are recorded as per-server status, so a broken server never blocks boot.
    await mcp_manager.start_enabled()
    try:
        # Required whenever the exported MCP server is mounted: Starlette does not
        # give lifespan events to mounted sub-apps, so without this its session
        # manager never starts and every request 500s. No-op when export is disabled.
        async with mcp_export.session_lifespan():
            yield
    finally:
        # Close any training run still mirroring into localtrack, so a run that was
        # in flight at shutdown is recorded as finished rather than left `running`
        # forever.
        from backend.modules.training.metrics import finish_all as _finish_training

        _finish_training()
        research_runner.stop()
        queue.stop()
        # MCP servers are child processes (stdio transport); leaving them behind on
        # reload would strand orphaned node/python servers.
        await mcp_manager.stop_all()
        await plugin_registry.run_shutdown()
        # Kernels are child processes; leaving them behind on reload/shutdown
        # would strand orphaned ipykernels.
        await training_kernels.shutdown_all()
        await notebook_manager.shutdown_all()
        # Same reasoning: llama-server is a child process holding gigabytes of
        # mapped weights and a bound port. An orphan survives a reload and then
        # makes the next spawn fail on a port that looks free.
        llama_manager.stop()
        # The Voicemeeter Remote API is a per-process login. An unbalanced one
        # leaves the DLL holding a client slot for a process that is gone, and
        # the next login returns -2 instead of connecting.
        shutdown_voicemeeter()
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


@app.get("/api/paths")
def resolved_paths() -> dict[str, object]:
    """Every root this node resolved and *why*, so "where did my models go" is
    answerable from inside the app rather than from `backend/paths.py`. `repo` is
    empty in a packaged install. Rendered by the Storage settings section."""
    return paths.describe_roots()


app.include_router(agent_router, prefix="/api")
app.include_router(workspace_router, prefix="/api")
app.include_router(desktop_router, prefix="/api")
app.include_router(database_router, prefix="/api")
app.include_router(library_router, prefix="/api")
app.include_router(karaoke_router, prefix="/api")
app.include_router(evals_router, prefix="/api")
app.include_router(trajectories_router, prefix="/api")
app.include_router(records_router, prefix="/api")
app.include_router(artifacts_router, prefix="/api")
app.include_router(research_router, prefix="/api")
app.include_router(search_router, prefix="/api")
app.include_router(arxiv_router, prefix="/api")
app.include_router(interpretability_router, prefix="/api")
app.include_router(hardware_router, prefix="/api")
app.include_router(audio_router, prefix="/api")
app.include_router(llamacpp_router, prefix="/api")
app.include_router(connectors_router, prefix="/api")
app.include_router(google_connector_router, prefix="/api")
app.include_router(github_connector_router, prefix="/api")
app.include_router(huggingface_connector_router, prefix="/api")
app.include_router(browser_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(files_router, prefix="/api")
app.include_router(notebook_router, prefix="/api")
app.include_router(docs_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(clubhouse_router, prefix="/api")
app.include_router(clubhouse_voice_router, prefix="/api")
app.include_router(notifications_router, prefix="/api")
app.include_router(telemetry_router, prefix="/api")
app.include_router(plugins_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(keymap_router, prefix="/api")
app.include_router(secrets_router, prefix="/api")
app.include_router(flow_router, prefix="/api")
app.include_router(network_router, prefix="/api")
app.include_router(social_router, prefix="/api")
app.include_router(share_router, prefix="/api")
app.include_router(hassault_router, prefix="/api")
app.include_router(training_router, prefix="/api")
app.include_router(lsp_router, prefix="/api")
app.include_router(mcp_router, prefix="/api")
app.include_router(skills_router, prefix="/api")
# The MCP server this node *exports* (read-only trajectories + telemetry). Mounts only
# when HORRIBLE_ENABLE_MCP_SERVER=1 — it serves the user's prompts and the node's I/O
# metadata, so it stays behind an explicit opt-in. See backend/modules/mcp/server.py.
mcp_export.mount(app)
app.include_router(games_router, prefix="/api")
app.include_router(code_router, prefix="/api")
app.include_router(git_router, prefix="/api")
app.include_router(symdex_router, prefix="/api")
app.include_router(localtrack_router)

# Register the training module's backend agent tools into the sdk registry (the
# training module is a first-party consumer of the same registry backend plugins
# write to). Grouped under `training`, disclosed progressively by the orchestrator.
register_training_tools()

# Register the LocalTrack experiment tracking agent tools (grouped under `localtrack`)
register_localtrack_tools()

# The `llamacpp` group: list the node's GGUFs, serve one, stop it, and record or
# list an activation trace. Without these the fine-tuning agent could convert a
# checkpoint and then had no way to serve it. Also registers the `lens` group,
# which reads a trace as words.
register_llamacpp_tools()

# `dash.lens` — scripting sweeps over traces from the REPL. A backend-local
# facade: it reads this node's own disk and needs no browser attached.
register_lens_facade()

# Register the model designer's agent tools (grouped under `model`): read a saved
# design, fork the inspected model into one, retune its hyperparameters, emit its
# PyTorch. Node-level surgery is deliberately not offered — see agent_tools.py.
register_model_designer_tools()

# Register the games module's backend agent tools (grouped under `games`); the
# manual-play seat drives its move through game.getObservation/game.chooseAction.
register_games_tools()

# Register the built-in connectors (GitHub, …) and the agent tools they unlock. Each
# connector's tools are grouped under its id and disclosed progressively.
register_connectors()

# Register the symdex module's backend agent tools (grouped under `symbols`): the
# semantic symbol/docs/schema retrieval the coder and dba agents preload.
register_symdex_tools()

# Register the karaoke agent tools (grouped under `karaoke`): the session lives on
# the server, so these run the room with no karaoke pane open anywhere.
register_karaoke_tools()

# Register the evals agent tools (grouped under `evals`): sweeps run detached on
# the backend, so these read the scoreboard with no evals pane open.
register_evals_tools()
# Trajectory tools (grouped under `trajectories`, so progressively disclosed and
# free when unloaded). `search` is the continual-learning read path: the agent
# looks up how a similar task went before.
register_trajectories_tools()

# Register the `audio` agent tools. Grouped, and backend-side because the routing
# is server state: "put the video through my microphone" works from the ask bar
# with no mixer pane open anywhere.
register_audio_tools()

# Register the research module's backend agent tools (grouped under `research`):
# page capture and PDF filing that work with no browser pane attached.
register_research_tools()

# Register the arXiv backend agent tools (grouped under `arxiv`): search/get are
# read-only; download files a paper into the library.
register_arxiv_tools()

# Register the search module (grouped under `search`): the provider registry, the
# `search` connector that holds each provider's API key, and the five agent tools.
# The group's catalog blurb comes from that connector rather than a duplicate entry
# in the orchestrator's `_GROUP_DESCRIPTIONS`.
register_search_tools()

# Register the records agent tools (grouped under `records`): read + propose are
# free, commit and createSchema go through the permission gate.
register_records_tools()

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
    from backend.modules.ws import register_connection, unregister_connection

    register_connection(conn)
    terminals = TerminalManager(conn)
    repl = ReplManager(conn)
    lsp = LspManager(conn)
    telemetry_task = asyncio.create_task(push_telemetry(conn))
    files_task = asyncio.create_task(push_file_events(conn))
    # Fan library ingestion status (queued→…→ready/failed) to this browser.
    library_task = asyncio.create_task(push_library_events(conn))
    # Fan record proposals + committed rows to this browser, so an open form shows
    # the agent's extraction the moment it files one.
    records_task = asyncio.create_task(push_records_events(conn))
    # Fan deep-research run/step progress + synthesis deltas to this browser.
    research_task = asyncio.create_task(push_research_events(conn))
    # Fan focused-crawl progress (seed status, per-page results) to this browser.
    crawl_task = asyncio.create_task(push_crawl_events(conn))
    # Fan code-locus updates (dash/agent-set, and cross-window sync) to this browser.
    code_task = asyncio.create_task(push_code_events(conn))
    # Fan symdex index progress (packages/schema/docs builds) to this browser.
    symdex_task = asyncio.create_task(push_symdex_events(conn))
    # Fan model-locus updates (set by `dash.lens` / the `lens` tools) to this
    # browser. Outbound only — there is deliberately no inbound `lens` channel;
    # a browser's own grid clicks are local and never cross the socket.
    lens_task = asyncio.create_task(push_lens_events(conn))
    # Fan peer/presence events from the process-global hub out to this browser.
    network_unsub = subscribe_conn(conn)
    # Fan lobby (directory/rooms) events out to this browser.
    lobby_unsub = subscribe_lobby_conn(conn)
    # Fan commons (profiles/search) events out to this browser.
    commons_unsub = subscribe_commons_conn(conn)
    # Fan friends-roster + presence changes out to this browser.
    social_unsub = subscribe_social_conn(conn)
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
            elif channel == "social":
                await handle_social_message(conn, msg)
            elif channel == "share":
                await handle_share_message(conn, msg)
            elif channel == "hassault":
                await handle_hassault_message(conn, msg)
            else:
                # Unknown built-in channel — offer it to backend plugins.
                await plugin_registry.dispatch_ws(conn, str(channel), msg)
    except WebSocketDisconnect:
        pass
    finally:
        unregister_connection(conn)
        await terminals.close_all()
        await repl.close_all()
        await lsp.close_all()
        visualizer_manager.stop_for(conn)
        browser_manager.stop_for(conn)
        telemetry_task.cancel()
        files_task.cancel()
        library_task.cancel()
        records_task.cancel()
        research_task.cancel()
        crawl_task.cancel()
        code_task.cancel()
        symdex_task.cancel()
        lens_task.cancel()
        network_unsub()  # type: ignore[operator]
        lobby_unsub()  # type: ignore[operator]
        commons_unsub()  # type: ignore[operator]
        social_unsub()  # type: ignore[operator]
        training_unsub()
        training_kernels.detach(conn)
        notebook_manager.detach(conn)
        collab_manager.drop(conn)
        chat_manager.drop(conn)
        await hassault_on_disconnect(conn)
        drop_games_conn(conn)
