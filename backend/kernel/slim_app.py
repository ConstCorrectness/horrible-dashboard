"""A backend that hosts only a few modules: what `horrible run <id>` serves.

It is the dashboard's backend with everything a module does not need taken out. What
is left:

- `/api/settings` and `/api/modules`, which the module shell reads at boot;
- a `/ws` that greets, dispatches the channels modules register, and receives the
  `broadcast_event` frames their sessions send;
- the module manager, loading only the named modules and what they depend on;
- the bus node (`/bus/mcp`), so this process's tools reach the dashboard's agent and
  every other runner, and theirs reach it;
- the module shell's built page at `/`, when it has been built.

Settings, the data dir and the bus directory are the node's own (the same
`HORRIBLE_DATA_DIR` as the dashboard), so a runner sees the dashboard's settings and
the two find each other without configuration.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from backend import origins
from backend.kernel import bus_node
from backend.kernel.module_manager import ModuleManager
from backend.sdk.registry import PluginRegistry

logger = logging.getLogger(__name__)

SHELL_DIST = Path(__file__).resolve().parents[2] / "apps" / "module-shell" / "dist"


def build_app(
    module_ids: list[str],
    *,
    port: int,
    name: str | None = None,
    manager: ModuleManager | None = None,
    reg: PluginRegistry | None = None,
    shell_dist: Path | None = SHELL_DIST,
) -> FastAPI:
    from backend.modules.plugins import modules_routes
    from backend.modules.settings import router as settings_router
    from backend.sdk.registry import registry as global_registry

    reg = reg if reg is not None else global_registry
    if manager is None:
        from backend.kernel.module_manager import module_manager as manager
    title = name or "+".join(module_ids)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await reg.run_startup()
        await manager.start()
        try:
            if bus_node.node is None:
                yield
            else:
                async with bus_node.node.run():
                    yield
        finally:
            await manager.stop()
            await reg.run_shutdown()

    app = FastAPI(title=f"horrible run {title}", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins.UI_ORIGINS),
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Range", "Accept-Ranges"],
    )
    app.include_router(settings_router, prefix="/api")
    app.include_router(modules_routes.router, prefix="/api")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        # `modules`: what this runner was started for, in the order given, whether or
        # not each is loaded yet (a peer may hold it). The shell shows the first.
        return {
            "ok": True,
            "standalone": True,
            "modules": list(module_ids),
            "loaded": manager.live_ids(),
        }

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        from backend.modules.ws import (
            WsConnection,
            register_connection,
            unregister_connection,
        )

        if not origins.ws_origin_allowed(
            websocket.headers.get("origin"), websocket.headers.get("host")
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_json(
            {"channel": "system", "event": "hello", "standalone": True}
        )
        conn = WsConnection(websocket)
        register_connection(conn)
        try:
            while True:
                msg = await websocket.receive_json()
                channel = msg.get("channel") if isinstance(msg, dict) else None
                if channel:
                    await reg.dispatch_ws(conn, str(channel), msg)
        except WebSocketDisconnect:
            pass
        finally:
            unregister_connection(conn)
            await reg.run_ws_disconnect(conn)

    manager.only = set(module_ids)
    manager.attach(app)
    manager.discover()
    unknown = [
        m
        for m in module_ids
        if m not in {d["id"] for d in manager.describe_all()["modules"]}
    ]
    if unknown:
        raise SystemExit(f"no module package named {', '.join(unknown)}")
    bus_node.install(app, manager, reg, kind="standalone", name=title, port=port)
    manager.load_all()

    if shell_dist is not None and (shell_dist / "index.html").is_file():
        _serve_shell(app, shell_dist)

    return app


def _serve_shell(app: FastAPI, dist: Path) -> None:
    """Serve the built module shell, as a 404 fallback rather than a catch-all route.

    A catch-all route would sit in front of module routes that `RouteMounter` appends
    when a module is re-enabled at runtime, and swallow their GETs. A 404 handler
    only answers what nothing else did.
    """
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    root = dist.resolve()

    @app.exception_handler(StarletteHTTPException)
    async def fallback(request: Request, exc: StarletteHTTPException) -> Any:
        path = request.url.path
        if (
            exc.status_code != 404
            or request.method != "GET"
            or path.startswith(("/api/", "/bus/", "/ws"))
        ):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        candidate = (root / path.lstrip("/")).resolve()
        if candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(root / "index.html")
