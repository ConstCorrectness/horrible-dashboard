"""The hosted hub: the one public process of a hosted dashboard.

    uv run uvicorn backend.hub.app:app --host 0.0.0.0 --port 8080

It signs people in with their games account (`auth.py`), runs one isolated backend
per person (`spawner/`), and reverse-proxies that person's `/api` and `/ws` to it
(`proxy.py`). It also serves the built frontend at `/`, unless the frontend is
hosted elsewhere (Vercel), in which case `/hub/ws-ticket` lets a cross-origin page
open the socket. See docs/architecture/hosted-hub.mdx.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.hub import auth, proxy
from backend.hub.config import HubConfig
from backend.hub.spawner import Instance, InstanceSpawner, make_spawner
from backend.hub.store import HubStore, User

logger = logging.getLogger("backend.hub")

_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

#: How long a `/ws` upgrade waits for a booting instance (see the `ws` route).
WS_READY_SECONDS = 8.0


class Hub:
    """Runtime state shared by the routes: config, store, spawner, one HTTP client,
    and who has been active (for the idle reaper)."""

    def __init__(
        self, config: HubConfig, store: HubStore, spawner: InstanceSpawner
    ) -> None:
        self.config = config
        self.store = store
        self.spawner = spawner
        self.http = httpx.AsyncClient(follow_redirects=False)
        self.pending_web: dict[str, dict[str, Any]] = {}
        self._last_seen: dict[str, float] = {}
        #: When each idle user's instance first reported being busy (see reap_idle).
        self._busy_since: dict[str, float] = {}
        #: Users whose instance may still be running from before this hub process
        #: started (see seed_known_instances) — watched by the reaper, but not
        #: activity: a background poll must not count as permission to wake one.
        self._seeded: dict[str, float] = {}
        #: When each user's instance was last found stopped, so a parked tab's polls
        #: do not ask the spawner (a Fly API call) every ten seconds.
        self._parked_checked: dict[str, float] = {}
        self._ready: set[str] = set()
        self._instances: dict[str, Instance] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    # ---- activity --------------------------------------------------------------
    #
    # Activity is input from a person: the keystrokes, clicks and scrolls the page
    # reports through POST /hub/activity (throttled), and signing in. It is NOT
    # traffic: an open tab polls /api/health every 10 s with nobody at it, and an
    # open notebook pane polls every 2 s, so counting requests or open sockets kept
    # every forgotten tab's machine running for good.

    def touch(self, user_id: str) -> None:
        self._last_seen[user_id] = time.monotonic()
        self._busy_since.pop(user_id, None)

    def recently_active(self, user_id: str, now: float | None = None) -> bool:
        seen = self._last_seen.get(user_id)
        now = time.monotonic() if now is None else now
        return seen is not None and now - seen <= self.config.idle_minutes * 60

    def idle_users(self, now: float | None = None) -> list[str]:
        """Users whose instance may be running and who have given no input for
        ``idle_minutes`` (or, if seeded at startup, none since the hub started)."""
        now = time.monotonic() if now is None else now
        limit = self.config.idle_minutes * 60
        idle = [uid for uid in list(self._last_seen) if not self.recently_active(uid, now)]
        idle += [
            uid
            for uid, seeded in list(self._seeded.items())
            if uid not in self._last_seen and now - seeded > limit
        ]
        return idle

    def seed_known_instances(self) -> None:
        """At startup: every user who has an instance may have one still running from
        before this hub process. Start their idle clocks now, so an instance nobody
        returns to is stopped one idle window later instead of never."""
        now = time.monotonic()
        for uid in self.store.instance_user_ids():
            self._seeded.setdefault(uid, now)

    async def busy_reasons(self, user: User, instance: Instance) -> list[str]:
        """What the instance says it is in the middle of (``GET /api/hosted/busy``).
        An instance that cannot answer is treated as idle: a wedged one is exactly
        what should be stopped."""
        try:
            res = await self.http.get(
                f"{instance.base_url}/api/hosted/busy",
                headers=dict(proxy.identity_headers(instance, user)),
                timeout=10.0,
            )
            if res.status_code == 200:
                return [str(r) for r in res.json().get("reasons") or []]
        except (httpx.HTTPError, ValueError):
            pass
        return []

    async def reap_idle(self) -> None:
        now = time.monotonic()
        busy_cap = self.config.busy_max_hours * 3600
        for uid in self.idle_users(now):
            user = self.store.get_user(uid)
            if user is None:
                self._last_seen.pop(uid, None)
                self._seeded.pop(uid, None)
                continue
            try:
                instance = await self.spawner.running(user)
            except Exception:  # noqa: BLE001 - try again on the next tick
                logger.exception("could not check whether %s's instance runs", uid)
                continue
            if instance is not None:
                reasons = await self.busy_reasons(user, instance)
                if reasons:
                    since = self._busy_since.setdefault(uid, now)
                    if now - since < busy_cap:
                        logger.info(
                            "keeping idle %s's instance running: %s",
                            uid,
                            ", ".join(reasons),
                        )
                        continue
                    logger.warning(
                        "stopping %s's instance although busy (%s): over %sh with no input",
                        uid,
                        ", ".join(reasons),
                        self.config.busy_max_hours,
                    )
                logger.info("stopping idle instance for %s", uid)
                try:
                    await self.spawner.stop(uid)
                except Exception:  # noqa: BLE001 - one failure must not stop the reaper
                    logger.exception("failed to stop instance for %s", uid)
                    continue
            self._last_seen.pop(uid, None)
            self._seeded.pop(uid, None)
            self._busy_since.pop(uid, None)
            self.mark_unready(uid)

    def admitted(self, user: User | None) -> User | None:
        """The user, if the allowlist (when there is one) still lets them in.

        Checked on every request, not only at sign-in: an account dropped from
        ``HUB_ALLOWED_ACCOUNTS`` — or one that signed in before the hub went
        invite-only — must lose access at once, not when its 30-day cookie expires.
        """
        if user is None or auth.account_allowed(self.config, user.public()):
            return user
        return None

    # ---- instances -------------------------------------------------------------

    async def instance(self, user: User) -> Instance:
        """The user's instance. Once it has answered a health probe the address is
        reused without asking the spawner again — a Docker round trip on every
        request would be most of the proxy's latency."""
        cached = self._instances.get(user.id)
        if cached is not None and user.id in self._ready:
            return cached
        instance = await self.spawner.ensure(user)
        self._instances[user.id] = instance
        return instance

    async def route(self, user: User) -> tuple[Instance | None, bool]:
        """Where to send a proxied request: ``(instance, parked)``.

        A user whose instance is up, or who has given input recently, gets it —
        started if need be. Anyone else is *parked*: their tab is open but nobody is
        at it, and its background polls must not wake a machine the reaper stopped.
        The one exception is an instance that is in fact still running (this hub
        restarted under an open tab), which is used rather than reported parked.
        """
        if user.id in self._ready or self.recently_active(user.id):
            return await self.try_instance(user), False
        checked = self._parked_checked.get(user.id)
        if checked is not None and time.monotonic() - checked < 60:
            return None, True
        try:
            running = await self.spawner.running(user)
        except Exception:  # noqa: BLE001 - cannot tell; do not wake it to find out
            logger.exception("could not check whether %s's instance runs", user.id)
            running = None
        if running is None:
            self._parked_checked[user.id] = time.monotonic()
            return None, True
        # Use it without counting this as input: the reaper keeps watching it (it
        # was seeded at startup), and it stops unless someone actually returns.
        self._instances[user.id] = running
        return running, False

    async def try_instance(self, user: User) -> Instance | None:
        """:meth:`instance`, with a spawner failure logged and reported as None —
        the caller answers 503 `starting`, which the boot screen keeps polling. A
        500 there would end the boot on what is usually a transient state (a
        machine mid-launch, an API hiccup)."""
        try:
            return await self.instance(user)
        except Exception:  # noqa: BLE001 - any spawner failure is "not up yet"
            logger.exception("could not ensure an instance for %s", user.id)
            return None

    async def wait_ready(self, user: User, instance: Instance, timeout: float) -> bool:
        """Probe the instance's health until it answers or `timeout` passes. The
        first time it comes up, hand it the user's games session."""
        if user.id in self._ready:
            return True
        deadline = time.monotonic() + timeout
        headers = dict(proxy.identity_headers(instance, user))
        while True:
            try:
                res = await self.http.get(
                    f"{instance.base_url}/api/health", headers=headers, timeout=3.0
                )
                if res.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.5)
        try:
            await self.http.put(
                f"{instance.base_url}/api/hosted/games-session",
                headers=headers,
                json={"token": user.games_token, "account": user.public()},
                timeout=5.0,
            )
        except httpx.HTTPError:
            logger.warning("could not hand %s's games session to its instance", user.id)
        self._ready.add(user.id)
        return True

    def mark_unready(self, user_id: str) -> None:
        self._ready.discard(user_id)
        self._instances.pop(user_id, None)

    def warm(self, user: User) -> None:
        """Start the user's instance in the background (right after sign-in)."""

        self.touch(user.id)
        self._parked_checked.pop(user.id, None)

        async def _go() -> None:
            try:
                await self.wait_ready(user, await self.instance(user), timeout=120)
            except Exception:  # noqa: BLE001 - the next request retries for real
                logger.exception("warming instance for %s failed", user.id)

        task = asyncio.get_running_loop().create_task(_go())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def prune_pending_web(self) -> None:
        now = time.time()
        for key in [k for k, v in self.pending_web.items() if v["expires_at"] < now]:
            self.pending_web.pop(key, None)

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.http.aclose()
        await self.spawner.close()
        self.store.close()


def _origin_ok(config: HubConfig, headers: Any) -> bool:
    """WebSocket upgrades are not covered by CORS, so the browser's Origin is checked
    here: an allowlisted origin, or the hub's own (same-origin)."""
    origin = headers.get("origin")
    if origin is None:
        return True  # not a browser: no ambient cookie to have been ridden
    if origin in config.allowed_origins:
        return True
    return urlsplit(origin).netloc == headers.get("host")


def create_app(
    config: HubConfig | None = None, spawner: InstanceSpawner | None = None
) -> FastAPI:
    config = config or HubConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = HubStore(config.db_path)
        hub = Hub(config, store, spawner or make_spawner(config, store))
        app.state.hub = hub
        hub.seed_known_instances()

        async def reaper() -> None:
            while True:
                await asyncio.sleep(60)
                await hub.reap_idle()

        reaper_task = asyncio.create_task(reaper())
        try:
            yield
        finally:
            reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper_task
            await hub.close()

    # No /docs or /openapi.json: they would shadow those client-side routes of the
    # SPA, and the hub's surface is not something to advertise.
    app = FastAPI(
        title="horrible-dashboard hub",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    if config.allowed_origins:
        # A frontend on another origin (Vercel calling directly rather than through
        # its rewrites) needs credentialed CORS; same-origin needs none.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(config.allowed_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(auth.router)

    def current_user(request: Request) -> User | None:
        hub: Hub = request.app.state.hub
        return hub.admitted(
            hub.store.session_user(request.cookies.get(auth.SESSION_COOKIE))
        )

    @app.get("/hub/health")
    async def health() -> dict[str, str]:
        """Liveness for the host's checks (Fly, compose). Says nothing about users."""
        return {"status": "ok"}

    @app.get("/hub/me")
    async def me(request: Request) -> Response:
        user = current_user(request)
        if user is None:
            return JSONResponse({"signed_in": False}, status_code=401)
        return JSONResponse({"signed_in": True, "account": user.public()})

    @app.post("/hub/logout")
    async def logout(request: Request) -> Response:
        hub: Hub = request.app.state.hub
        hub.store.delete_session(request.cookies.get(auth.SESSION_COOKIE))
        res = JSONResponse({"signed_in": False})
        res.delete_cookie(auth.SESSION_COOKIE, path="/")
        return res

    @app.post("/hub/activity")
    async def activity(request: Request) -> Response:
        """The page saw input (throttled client-side). This, not traffic, is what
        keeps an instance running — and what wakes a parked one."""
        user = current_user(request)
        if user is None:
            return JSONResponse({"detail": "sign in required"}, status_code=401)
        hub: Hub = request.app.state.hub
        resumed = user.id not in hub._ready
        if resumed:
            hub.warm(user)  # touches, and starts the instance in the background
        else:
            hub.touch(user.id)
        return JSONResponse({"resumed": resumed})

    @app.post("/hub/ws-ticket")
    async def ws_ticket(request: Request) -> Response:
        user = current_user(request)
        if user is None:
            return JSONResponse({"detail": "sign in required"}, status_code=401)
        hub: Hub = request.app.state.hub
        return JSONResponse({"ticket": hub.store.create_ticket(user.id)})

    @app.api_route("/api/{path:path}", methods=_PROXY_METHODS)
    async def api_proxy(path: str, request: Request) -> Response:
        user = current_user(request)
        if user is None:
            return JSONResponse({"detail": "sign in required"}, status_code=401)
        hub: Hub = request.app.state.hub
        instance, parked = await hub.route(user)
        if parked:
            # The page shows "paused" and wakes it through /hub/activity on input.
            return JSONResponse({"parked": True}, status_code=503)
        if instance is None:
            return JSONResponse({"starting": True}, status_code=503)
        # Short wait: a booting instance answers 503 `{starting}` and the frontend's
        # boot screen polls, rather than every request hanging for the whole boot.
        if not await hub.wait_ready(user, instance, timeout=2.0):
            return JSONResponse({"starting": True}, status_code=503)
        res = await proxy.proxy_http(hub, request, user, instance)
        if res.status_code == 503:
            hub.mark_unready(user.id)
        return res

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        hub: Hub = websocket.app.state.hub
        if not _origin_ok(config, websocket.headers):
            await websocket.close(code=4403)
            return
        ticket = websocket.query_params.get("ticket")
        if ticket:
            user = hub.admitted(hub.store.redeem_ticket(ticket))
        else:
            user = hub.admitted(
                hub.store.session_user(websocket.cookies.get(auth.SESSION_COOKIE))
            )
        if user is None:
            await websocket.close(code=4401)
            return
        instance, parked = await hub.route(user)
        if parked:
            await websocket.close(code=4408)  # parked: input wakes it, not a retry
            return
        # Under ten seconds on purpose: Fly's proxy drops an upgrade that has had no
        # answer for ~10 s, the browser retries, and a longer wait here then accepted
        # every one of those dead upgrades at once when the instance finally came up.
        # Not ready in time → 1013 "try again later", and ws.ts's backoff retries.
        if instance is None or not await hub.wait_ready(
            user, instance, timeout=WS_READY_SECONDS
        ):
            await websocket.close(code=1013)
            return
        await proxy.proxy_ws(hub, websocket, user, instance)

    _mount_frontend(app, config)
    return app


def _mount_frontend(app: FastAPI, config: HubConfig) -> None:
    """Serve the built SPA at `/`, with client-side routes falling back to
    `index.html`. Skipped when there is no build — the frontend lives elsewhere.

    With ``HUB_FRONTEND_URL`` set, page requests redirect there instead. Serving a
    second copy as well meant two front doors and two builds to keep in step, and a
    fix shipped to one of them left the other showing the bug."""
    if config.frontend_url:

        @app.get("/{path:path}", include_in_schema=False)
        async def to_frontend(path: str, request: Request) -> RedirectResponse:
            target = f"{config.frontend_url}/{path}"
            if request.url.query:
                target += "?" + request.url.query
            return RedirectResponse(target, status_code=308)

        return
    dist = config.web_dist
    index = dist / "index.html"
    if not index.is_file():
        logger.info("no frontend build at %s; serving the API only", dist)
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        # Revalidated every load: it names the content-hashed bundles, so a cached
        # copy keeps running the previous deploy's frontend indefinitely.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
