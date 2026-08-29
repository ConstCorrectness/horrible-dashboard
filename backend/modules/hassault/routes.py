"""REST surface for HorribleAssault's map pipeline, mounted at `/api/hassault`.

The split between the two map endpoints is deliberate. A 256×256 map is 65 536
cubes across nine fields; as JSON that is roughly 590 000 numbers and several
megabytes, which is slow to serialize and slower to parse. So metadata and
entities come back as JSON, and the grid comes back as **raw concatenated byte
planes** the browser adopts directly as typed arrays — one copy, no parsing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from backend.paths import data_dir
from backend.version import app_version
from backend.modules.hassault import (
    assets,
    client_install,
    fabric,
    hitbox,
    lore,
    mapsource,
    pickups,
    weapons,
)
from backend.modules.hassault.cgz import PLANE_ORDER, CgzError, write_cgz
from backend.modules.hassault.console import (
    ConsoleDefinitionsResponse,
    ConsoleExecRequest,
    ConsoleExecResponse,
    MacroDefinition,
    console_registry,
)
from backend.modules.hassault.match import MAX_PLAYERS, match_server
from backend.modules.hassault.physics import World as SimWorld
from backend.modules.hassault.models import (
    BrowseMatch,
    BrowsePlayer,
    CreateMatchRequest,
    EntityOut,
    HitboxOut,
    HitboxTuneRequest,
    InstallStatus,
    Invitee,
    ItemOut,
    ItemPlacement,
    ItemReach,
    ItemsResponse,
    LoreOut,
    MapInfo,
    MapSummary,
    MatchInvite,
    MatchSummary,
    ServerBrowse,
    SessionInfo,
    TacticalOut,
    WeaponOut,
    ClientInstallRequest,
    ClientRemoveRequest,
    LaunchNativeRequest,
    LaunchNativeResponse,
    NativeClientStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hassault", tags=["hassault"])


def _load(name: str):
    """The parsed map, as HTTP errors. Caching lives in `assets.load_map`, which
    the match server reads through too — one parse serves both."""
    try:
        world = assets.load_map(name)
    except CgzError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if world is None:
        raise HTTPException(status_code=404, detail=f"no map named {name!r}")
    return world


@router.get("/status", response_model=InstallStatus)
async def get_status() -> InstallStatus:
    """What is playable here. An install is an *addition*, never a prerequisite."""
    root = assets.install_root()
    from backend.modules.settings.routes import get_value

    configured = bool(str(get_value("hassault.installPath", "") or "").strip())
    bundled = len(mapsource.bundled_names())
    total = len(assets.list_maps())
    if root is None:
        return InstallStatus(
            found=False,
            configured=configured,
            map_count=total,
            bundled_count=bundled,
            message=(
                "That path is not an AssaultCube install (no packages/maps inside)."
                if configured
                else "Playing the maps that ship with the app. Point "
                "hassault.installPath at an AssaultCube install to add its maps "
                "too — its content is read from your own copy and never bundled."
            ),
        )
    return InstallStatus(
        found=True,
        path=str(root),
        configured=configured,
        map_count=total,
        bundled_count=bundled,
    )


@router.get("/session", response_model=SessionInfo)
async def get_session(refresh: bool = False) -> SessionInfo:
    """Who this node plays as — the gate the pane checks before it lets anyone in.

    The account is the shared game-server one (the ladder's), so signing in here
    signs you in there and vice versa; the join done backend-side here is the same
    pattern as `/invitees`, keeping the pane clear of a cross-module import.

    `enlisted` is *derived* from having a username rather than stored separately,
    so there is one source of truth and nothing to keep in sync. `refresh=true`
    re-reads it from the game server, which is how a username claimed on another
    machine — or one this node's token predates — shows up here.
    """
    from backend.modules.games import server_auth

    if refresh:
        await server_auth.fetch_account()
    account = server_auth.signed_in_account()
    if account is None:
        return SessionInfo(signed_in=False)
    username = account.get("handle")
    return SessionInfo(
        signed_in=True,
        account_id=account["id"],
        display_name=account["display_name"],
        username=str(username) if username else None,
        suggested_username=str(account.get("suggested_handle") or ""),
        enlisted=bool(username),
    )


@router.get("/maps", response_model=list[MapSummary])
async def get_maps() -> list[MapSummary]:
    return [
        MapSummary(name=m["name"], source=m["source"], size=int(m["size"]))
        for m in assets.list_maps()
    ]


@router.get("/maps/{name}", response_model=MapInfo)
async def get_map(name: str) -> MapInfo:
    world = _load(name)
    spawns = {
        "cla": len(world.spawns(0)),
        "rvsf": len(world.spawns(1)),
        "total": len(world.spawns()),
    }
    return MapInfo(
        name=world.name,
        title=world.title,
        magic=world.magic,
        version=world.version,
        sfactor=world.sfactor,
        ssize=world.ssize,
        cubic_size=world.cubic_size,
        waterlevel=world.waterlevel,
        watercolor=list(world.watercolor),
        maprevision=world.maprevision,
        ambient=world.ambient,
        flags=world.flags,
        timestamp=world.timestamp,
        entity_count=len(world.entities),
        items=[
            ItemPlacement(**item.placement())
            for item in pickups.place(SimWorld.from_map(world), world.entities)
        ],
        entities=[
            EntityOut(
                type=e.type,
                name=e.name,
                x=e.x,
                y=e.y,
                z=e.z,
                yaw=e.yaw,
                attrs=[e.attr1, e.attr2, e.attr3, e.attr4, e.attr5, e.attr6, e.attr7],
            )
            for e in world.entities
        ],
        spawns=spawns,
        truncated=world.truncated,
        legacy_unscaled_attrs=world.legacy_unscaled_attrs,
        plane_order=list(PLANE_ORDER),
    )


@router.get("/matches", response_model=list[MatchSummary])
async def get_matches() -> list[MatchSummary]:
    """Live matches on this node. Cheap and pollable — the authoritative view of
    a match you are *in* arrives on the `/ws` channel, not here."""
    return [MatchSummary(**row) for row in match_server.listing()]


@router.post("/matches", response_model=MatchSummary)
async def post_match(body: CreateMatchRequest) -> MatchSummary:
    """Open a match without joining it, so its id can be handed to a friend.

    Joining is a `/ws` operation because it binds a player to a socket; a match
    created here stays empty (and is retired after a grace period if nobody
    arrives).
    """
    try:
        room = match_server.create(body.map, body.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CgzError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MatchSummary(
        id=room.id,
        map=room.map_name,
        players=0,
        maxPlayers=MAX_PLAYERS,
        createdAt=room.created_at,
    )


@router.get("/weapons", response_model=list[WeaponOut])
async def get_weapons() -> list[WeaponOut]:
    """The loadout, in slot order.

    Served rather than hardcoded in the browser for the same reason `plane_order`
    is: two copies of a number that has to match is a bug waiting for someone to
    tune the rifle.
    """
    return [WeaponOut(**w.to_dict()) for w in weapons.WEAPONS]


@router.get("/items", response_model=ItemsResponse)
async def get_items() -> ItemsResponse:
    """The item table, for the same reason `/weapons` exists.

    The map's item *placements* are not here: they belong to a map, and ride on
    `GET /maps/{name}`. This is what each kind does, and how close you have to
    get to it.
    """
    return ItemsResponse(
        reach=ItemReach(
            radius=pickups.PICKUP_RADIUS,
            below=pickups.PICKUP_BELOW,
            above=pickups.PICKUP_ABOVE,
        ),
        kinds=[ItemOut(**spec) for spec in pickups.specs_payload()],
    )


@router.get("/hitbox", response_model=HitboxOut)
async def get_hitbox() -> HitboxOut:
    """The body a shot is resolved against.

    Both clients read this instead of holding their own copy of the dimensions.
    That is not tidiness: the browser draws an avatar to these numbers, the native
    client draws a box to them, and the server decides hits with them — three
    copies of a figure that is still being tuned is three chances to teach somebody
    to miss.
    """
    spec = hitbox.current()
    return HitboxOut(**spec.to_dict(), overridden=spec != hitbox.DEFAULT)


@router.put("/hitbox", response_model=HitboxOut)
async def tune_hitbox(req: HitboxTuneRequest) -> HitboxOut:
    """Tune the live body, or reset it.

    Deliberately a whole-object PUT rather than a setting per dimension. A hitbox
    is one coherent thing and a half-applied one — a new radius against an old head
    band — is a state no code downstream should ever have to consider. It is also
    why this is not in the settings bag: `SettingValue` is a scalar.

    Not persisted. A tuning session is a session; the shipped body is what
    `hitbox.DEFAULT` says it is, and promoting a tuned one is a code change with a
    regenerated `physics-vectors.json` behind it — which is exactly the friction
    that should stand between "this felt better" and "this is the game now".
    """
    if req.reset:
        spec = hitbox.reset()
        return HitboxOut(**spec.to_dict(), overridden=False)

    # `is None`, never falsiness: a head band of 0 means "no headshots", which is a
    # legitimate thing to try and would otherwise read as "leave it alone".
    changes = {
        name: value
        for name, value in (
            ("radius", req.radius),
            ("eye_height", req.eyeHeight),
            ("above_eye", req.aboveEye),
            ("crouch_eye_scale", req.crouchEyeScale),
            ("head_band", req.headBand),
            ("fit_tolerance", req.fitTolerance),
            ("eye_tolerance", req.eyeTolerance),
        )
        if value is not None
    }
    if not changes:
        spec = hitbox.current()
        return HitboxOut(**spec.to_dict(), overridden=spec != hitbox.DEFAULT)

    spec = hitbox.tune(**changes)
    return HitboxOut(**spec.to_dict(), overridden=spec != hitbox.DEFAULT)


@router.get("/lore", response_model=LoreOut)
async def get_lore() -> LoreOut:
    """The setting: factions, their palette and insignia, rank names, map briefs.

    Served rather than written into the frontend because the same faction colour
    tints an avatar in two renderers and a nameplate in a third surface. The rank
    names are a *display layer* over the game server's ladder tiers — this endpoint
    cannot move a rating.
    """
    return LoreOut(**lore.to_dict())


@router.get("/invitees", response_model=list[Invitee])
async def get_invitees() -> list[Invitee]:
    """Friends who could join a match right now.

    The roster lives in the social module, and the pane must not import across
    that boundary — so the join happens here, backend-side, and the browser sees
    one list from one module.
    """
    from backend.modules.network.hub import peer_hub
    from backend.modules.social import roster, store

    online = roster.online_nodes()
    capable = {
        p.node_id
        for p in peer_hub.list_peers()
        if fabric.CAPABILITY in (p.capabilities or [])
    }
    # Which of our rooms each friend's device is standing in — the only place that
    # can be known, since a remote player is a `PeerPlayerConn` and its node id is
    # all that ties a body in a room to a person on the roster.
    rooms_by_node = fabric.hosted_rooms()
    out: list[Invitee] = []
    for friend in store.list_friends(online):
        if friend.status != "accepted" or friend.is_self:
            continue
        nodes = [d.node_id for d in friend.devices if d.online]
        if not nodes:
            continue
        room = next((rooms_by_node[n] for n in nodes if n in rooms_by_node), "")
        live = match_server.get(room) if room else None
        out.append(
            Invitee(
                name=friend.display_name,
                # Served alongside the display name, not instead of it: a friend
                # added by code before either of you signed in to the game server
                # has no username bound yet, and the UI needs something to fall
                # back to rather than an empty row.
                username=friend.handle or "",
                person_id=friend.person_id,
                friend_code=friend.friend_code,
                can_play=any(n in capable for n in nodes),
                devices_online=len(nodes),
                room=room,
                room_map=live.map_name if live is not None else "",
            )
        )
    return out


@router.get("/invites", response_model=list[MatchInvite])
async def get_invites() -> list[MatchInvite]:
    """Match invitations received from friends and not yet expired."""
    return [MatchInvite(**invite) for invite in fabric.live_invites()]


@router.get("/browse", response_model=ServerBrowse)
async def get_browse() -> ServerBrowse:
    """The server browser: every match we can see, and everyone we could play with.

    There is no master server to query — this game has no central list and is not
    getting one. "Available servers" here means matches on **this node** plus
    matches on the nodes of friends the fabric currently has a session with, asked
    for over the peer wire on a two-second deadline. That is the honest extent of
    it: a stranger's match is not discoverable, because it is also not joinable
    (see `fabric.handle_join`).

    Players are the roster's, and they carry a `room` when one of their devices is
    playing in a match hosted here — which is the only place we can know it from.
    """
    local = [
        BrowseMatch(**row, host="", hostName="this node")
        for row in match_server.listing()
    ]
    remote, asked, answered = await fabric.browse_peers()

    # Which of our own rooms a friend's device is standing in. `player_nodes` is
    # the only view of that: a remote player is a `PeerPlayerConn`, so its node id
    # is all that ties a body in a room to a person on the roster.
    rooms_by_node = fabric.hosted_rooms()

    players: list[BrowsePlayer] = []
    from backend.modules.network.hub import peer_hub
    from backend.modules.social import roster, store

    online = roster.online_nodes()
    capable = {
        p.node_id
        for p in peer_hub.list_peers()
        if fabric.CAPABILITY in (p.capabilities or [])
    }
    for friend in store.list_friends(online):
        if friend.status != "accepted" or friend.is_self:
            continue
        nodes = [d.node_id for d in friend.devices if d.online]
        if not nodes:
            continue
        room = next((rooms_by_node[n] for n in nodes if n in rooms_by_node), "")
        live = match_server.get(room) if room else None
        players.append(
            BrowsePlayer(
                name=friend.display_name,
                username=friend.handle or "",
                person_id=friend.person_id,
                friend_code=friend.friend_code,
                room=room,
                room_map=live.map_name if live is not None else "",
                can_play=any(n in capable for n in nodes),
                devices_online=len(nodes),
            )
        )

    return ServerBrowse(
        matches=local + [BrowseMatch(**row) for row in remote],
        players=players,
        peers_asked=asked,
        peers_answered=answered,
    )


@router.get("/maps/{name}/cubes")
async def get_map_cubes(name: str) -> Response:
    """The cube grid as nine concatenated planes of `ssize * ssize` bytes.

    Plane order is reported by `/maps/{name}` as `plane_order`; `floor` and `ceil`
    are signed bytes (read them as an Int8Array), the rest unsigned.
    """
    world = _load(name)
    body = b"".join(getattr(world, plane) for plane in PLANE_ORDER)
    return Response(
        content=body,
        media_type="application/octet-stream",
        headers={
            "X-Map-Ssize": str(world.ssize),
            "X-Map-Planes": ",".join(PLANE_ORDER),
            # Immutable for a given revision, so the browser need not refetch.
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/maps/{name}/download")
async def download_map(name: str) -> Response:
    """A bundled map as a real `.cgz`, openable in AssaultCube's own editor.

    Only bundled maps. An install's maps are already `.cgz` files on the user's
    own disk, and re-serving copyright content over HTTP is exactly the thing
    this module does not do.
    """
    if name not in mapsource.bundled_names():
        raise HTTPException(
            status_code=404,
            detail=f"{name!r} is not a bundled map; only maps this app ships can be exported",
        )
    world = _load(name)
    return Response(
        content=write_cgz(world),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}.cgz"'},
    )


@router.get("/tacticals", response_model=list[TacticalOut])
async def list_tacticals() -> list[TacticalOut]:
    """The four grenades, in slot order.

    Reads `grenades.GRENADES` — the table the simulation actually runs on — and
    not the old `weapons.TACTICALS`, which was numbers with nothing behind them:
    a client that laid out its HUD from that list was showing a loadout the
    server had never heard of.
    """
    from backend.modules.hassault import grenades

    return [TacticalOut(**g.to_dict()) for g in grenades.GRENADES]


def _local_client_candidates(repo_root: Path) -> list[str]:
    """Every place a *locally built* native client can be, newest wins.

    One list, because `launch_native` and `/client/status` both need it and two
    copies would disagree the first time a build output moved — the status route
    would then offer to download a client that the launch route is about to find
    on disk.

    Deliberately excludes the downloaded install under `$HORRIBLE_DATA_DIR`: see
    the tier-3 note in `launch_native` for why that must not compete on mtime.
    """
    crate = repo_root / "apps" / "native-fps"
    return [
        str(crate / "target" / "release" / "hassault-native.exe"),
        str(crate / "target" / "release" / "hassault-native"),
        str(crate / "target" / "debug" / "hassault-native.exe"),
        str(crate / "target" / "debug" / "hassault-native"),
        str(crate / "bin" / "hassault.exe"),
        str(crate / "bin" / "hassault"),
    ]


def pick_binary(custom: str, candidates: list[str]) -> str | None:
    """Which native binary to launch.

    **The newest build wins, not the first one on the list.** The candidates used
    to be tried in order — `target/release`, then `target/debug` — which is the
    wrong order the moment anybody edits the client: a release binary from before
    the change is silently launched over a debug one built from the current
    source. The symptom is a game missing whatever was just added, with nothing
    anywhere saying an old binary was run. That is how a native client with no
    weapon view model in it kept starting after the view model was written.

    An explicit `hassault.nativeBinaryPath` still wins outright, because that is
    somebody naming the binary they mean.
    """
    import os
    import shutil

    if custom and (os.path.isfile(custom) or shutil.which(custom)):
        return custom
    on_disk = [c for c in candidates if c and os.path.isfile(c)]
    if on_disk:
        return max(on_disk, key=os.path.getmtime)
    # Not a path we can stat: something on PATH.
    return next((c for c in candidates if c and shutil.which(c)), None)


#: What makes a built native client stale. `tests/` is deliberately absent: a
#: test edit changes nothing the game runs, and rebuilding on one would charge a
#: player minutes of `wgpu` compile for a file the binary does not contain.
_NATIVE_SOURCE_GLOBS = ("src/**/*", "Cargo.toml", "Cargo.lock")

#: A cold `cargo build --release` of this crate is minutes (it builds `wgpu`), so
#: the ceiling is generous. It exists at all so a build that wedges — a held
#: `target/` lock, a cargo waiting on a network registry — fails with a message
#: instead of hanging the request forever.
_BUILD_TIMEOUT_SECONDS = 900.0


def newest_source_mtime(crate_root: Path) -> float | None:
    """When the native client's source was last touched, or `None` off a checkout.

    `None` is a real answer and not a failure: a packaged install ships the binary
    with no crate beside it, and there is nothing to be stale *against* there. It
    is the reason this returns an option rather than `0.0` — a zero would compare
    as "the source is ancient", which is a different claim from "there is no
    source".
    """
    newest: float | None = None
    for pattern in _NATIVE_SOURCE_GLOBS:
        for path in crate_root.glob(pattern):
            try:
                if not path.is_file():
                    continue
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest:
                newest = mtime
    return newest


def build_native_client(crate_root: Path, profile: str) -> tuple[bool, str]:
    """Compile the native client, returning success and something to show.

    Blocking on purpose — the caller runs it on a thread. `subprocess.run` rather
    than asyncio's spawn because `uvicorn --reload` puts a `SelectorEventLoop`
    under this backend on Windows, where asyncio cannot spawn a subprocess at all.

    The profile is **the one already built**, not always release: a developer who
    has been iterating with `cargo build` (debug) would otherwise be handed a
    minutes-long optimised build they did not ask for, every time they changed a
    line.
    """
    import subprocess

    argv = ["cargo", "build", "--manifest-path", str(crate_root / "Cargo.toml")]
    if profile == "release":
        argv.insert(2, "--release")

    log_path = data_dir() / "hassault" / "native-build.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_BUILD_TIMEOUT_SECONDS,
            cwd=str(crate_root),
        )
    except FileNotFoundError:
        return False, (
            "cargo is not on PATH, so the client cannot be rebuilt. Install Rust "
            "(https://rustup.rs), or point 'hassault.nativeBinaryPath' at a binary "
            "you build yourself."
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"The build did not finish within {int(_BUILD_TIMEOUT_SECONDS // 60)} "
            "minutes and was given up on."
        )

    output = (proc.stdout or "") + (proc.stderr or "")
    try:
        log_path.write_text(output, encoding="utf-8", errors="replace")
    except OSError:
        pass

    if proc.returncode == 0:
        return True, ""
    # cargo puts the diagnosis in the last lines, and the first are "Compiling"
    # noise for every dependency in the graph.
    tail = "\n".join(line for line in output.strip().splitlines() if line.strip())
    tail = "\n".join(tail.splitlines()[-6:])
    return False, (
        f"The native client failed to compile (cargo exited {proc.returncode}).\n"
        f"{tail}\nFull output: {log_path}"
    )


def _account_id() -> str:
    """The signed-in account, or the local stand-in.

    One spelling of it: this expression appeared verbatim in five endpoints, and
    a match filed under one id and read back under another is a debrief that
    never appears — with nothing anywhere reporting a failure.
    """
    from backend.modules.games import server_auth

    account = server_auth.signed_in_account()
    return str((account or {}).get("account_id") or "local_player")


ACTIVE_GAME_PROCESSES: dict[str, Any] = {}

#: How long the watchdog waits for the match result to land after the client
#: exits. The process dying and its websocket closing are two different events on
#: two different paths, and the socket is usually a beat behind — long enough
#: that reading the database the instant `wait()` returns finds the *previous*
#: match, or nothing.
RESULT_GRACE_SECONDS = 5.0


def _watchdog_game_process(account_id: str, map_name: str, proc: Any) -> None:
    """Wait for the native client to exit, then hand its match a skin drop.

    **This no longer decides what happened.** It used to invent the entire card —
    kills, deaths, headshots, damage, XP, rating, tier, level, all
    `random.randint` — and file it in a dict that a restart emptied. The match
    itself now records the result when the player leaves
    (`channel._record_result`), from the simulation's own counters, into
    `app.db`. What is left for a watchdog is the one thing that genuinely belongs
    to *finishing*: rolling the drop, and attaching it to the row.

    Train produces no result at all, which is correct: there is no match, nobody
    to play against, and nothing to be MVP of. A card for it would be a card
    about a room that did not exist.
    """
    import time
    from backend.modules.hassault import results
    from backend.modules.hassault.skins import skin_manager

    proc.wait()

    deadline = time.monotonic() + RESULT_GRACE_SECONDS
    summary: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        summary = results.latest(account_id)
        # Only a result from *this* session: an older undismissed card is not
        # evidence that the match just played produced one.
        if summary and summary["timestamp"] >= _LAUNCHED_AT.get(account_id, 0.0):
            break
        summary = None
        time.sleep(0.2)

    if summary is None:
        return

    # The drop is the reward for finishing, so it is rolled here rather than in
    # the leave path — and **persisted**, which the old one was not: it went into
    # a dict on the skin manager and was gone on the next restart.
    try:
        drop = skin_manager.roll_drop(account_id)
        # The id only. The card wants the skin's name, rarity colour and wear, and
        # `GET /match/latest_summary` resolves those against the inventory —
        # copying them onto the row would mean a renamed skin showing its old name
        # forever.
        results.attach_drop(summary["matchId"], drop.instance_id)
    except Exception:
        logger.exception("hassault: could not roll a drop for the finished match")


#: When each account's client was launched, so a stale undismissed card is not
#: mistaken for the match that just ended.
_LAUNCHED_AT: dict[str, float] = {}

#: How long the launch route watches the client before calling it launched.
#: Long enough to catch a startup that dies (a panic, an unreachable node, no
#: usable GPU backend), short enough that a launch still feels immediate — the
#: window itself opens well after this, so it is not a wait for the game.
_LAUNCH_SETTLE_SECONDS = 0.8


def _tail(path: Path, lines: int = 3) -> str:
    """The last few lines the client printed, for a launch that did not survive.

    Best-effort by design: this runs on the failure path, and a message that says
    only "it exited" is still better than one that raises while trying to explain
    why.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not text:
        return ""
    return " / ".join(text.splitlines()[-lines:])


#: The launch in flight, per account. A launch is **not** a request-shaped thing:
#: when the client has been edited since it was last built it compiles first, and
#: a cold `cargo build --release` of this crate is minutes. Held here so the work
#: outlives the HTTP request that started it — the pane that started a launch is
#: unmounted the moment its tab is switched, and a browser that has gone away
#: must not be able to cancel a build that a second tab is about to ask about.
_LAUNCH_JOBS: dict[str, dict[str, Any]] = {}

#: How long the route waits for a launch before answering "still going".
#: Deliberately longer than `_LAUNCH_SETTLE_SECONDS`, so the ordinary case — a
#: build that is already current — still answers with the real result inline and
#: no client ever has to poll for it. Only a launch that is actually compiling
#: crosses this line.
_LAUNCH_INLINE_SECONDS = 3.0

#: What each unfinished phase says while it is unfinished. A launch that is
#: compiling has to *say* it is compiling: "Launching…" for four minutes is
#: indistinguishable from a hang, which is exactly what it was read as.
_LAUNCH_PENDING: dict[str, str] = {
    "building": (
        "Compiling the native client — you have edited it since it was last "
        "built. This takes minutes on a cold build; it keeps going if you leave "
        "this tab."
    ),
    "starting": "Starting the native client…",
}


def _launch_job_response(job: dict[str, Any]) -> LaunchNativeResponse:
    """This job as the browser sees it, finished or not.

    One spelling of it, because both the POST and `GET /launch_native/status`
    answer with it and a second copy is how the two would come to disagree about
    what "still building" looks like.
    """
    result = job.get("result")
    phase = str(job.get("phase") or "idle")
    if isinstance(result, LaunchNativeResponse):
        return result.model_copy(update={"phase": phase})
    task = job.get("task")
    if task is not None and task.done():
        # Finished without leaving a result: the loop it was running on went
        # away under it (a cancelled task). Reported as a failure rather than
        # left saying "building" forever — a phase that can never change again
        # is the hang wearing a different word.
        return LaunchNativeResponse(
            launched=False,
            phase="failed",
            message="The launch was interrupted before it finished.",
        )
    return LaunchNativeResponse(
        launched=False,
        phase=phase,
        message=_LAUNCH_PENDING.get(phase, "Starting the native client…"),
    )


@router.post("/launch_native", response_model=LaunchNativeResponse)
async def launch_native_client(
    req: LaunchNativeRequest, request: Request
) -> LaunchNativeResponse:
    """Start the native client and point it back at this node.

    The arguments used to be fiction. It was handed `--connect=127.0.0.1:4000`
    (nothing has ever listened there), `--room`, and `--raw-input`, and the binary
    parsed **none** of them — it was a self-contained demo with no networking at
    all, so the flags were decoration on a launch that could not have used them.

    What the client actually needs is one thing: **the node's own HTTP origin**.
    Everything else follows from it — the map catalogue, the cube planes, the
    weapon table, and the `/ws` socket, whose address the client derives rather
    than being told separately (two addresses that must agree is two addresses
    that can disagree).

    The origin is read off *this request* rather than assembled from a host and a
    port, because the port is not knowable from in here: `HORRIBLE_DEV_BACKEND_PORT`
    moves it, Hyper-V's reserved ranges on Windows force that regularly, and a
    packaged build binds somewhere else again. The request arrived at the right
    address by definition.

    The other half is **the intent**: which of Train, Host and Join was pressed,
    and how many bots. Without it every launch was the same launch — "a match on
    this map, or open one" — so Train dropped a learner into whatever firefight was
    already running on that map, and the bot count the menu had just collected went
    nowhere. Train is answered off the wire entirely rather than by a room of one,
    because `match_server.join` with no room id is join-*or*-create and there is no
    way to ask it for solitude.
    """

    # **The work is a job, not a request.** It used to run inline, which is fine
    # for the ordinary launch and a trap for the one that matters: an edited
    # client is compiled first, and a cold `cargo build --release` of this crate
    # is minutes. The browser sat on "Launching…" for all of it with nothing to
    # read, and switching tabs unmounted the pane that was awaiting the promise —
    # so the launch appeared to stop, while a build nobody could see carried on.
    #
    # Started with `ensure_future` and parked in `_LAUNCH_JOBS` for exactly that
    # reason: the task outlives this coroutine, so a client that goes away
    # cancels nothing, and `GET /launch_native/status` hands the same job back to
    # whoever asks next — including the same pane after it is remounted.
    account_id = _account_id()
    # This node as the caller reached it, read here rather than in the worker:
    # the request is gone by the time a build finishes.
    origin = str(request.base_url).rstrip("/")

    running = _LAUNCH_JOBS.get(account_id)
    if running is not None and not running["task"].done():
        # Never two builds of the same crate at once — cargo would block on its
        # own `target/` lock and the second launch would look like the hang this
        # exists to end.
        return _launch_job_response(running)

    job: dict[str, Any] = {"phase": "starting", "result": None, "task": None}
    job["task"] = asyncio.ensure_future(_perform_launch(req, origin, job))
    _LAUNCH_JOBS[account_id] = job

    # A launch that needs no build finishes well inside this, so the common case
    # still answers with the real result and nothing has to poll. Only a
    # compiling one crosses the line and answers "building".
    await asyncio.wait({job["task"]}, timeout=_LAUNCH_INLINE_SECONDS)
    return _launch_job_response(job)


@router.get("/launch_native/status", response_model=LaunchNativeResponse)
async def launch_native_status() -> LaunchNativeResponse:
    """Where this account's launch has got to.

    The half of the fix that survives a tab switch: a pane reads this on mount,
    so a build started before it was unmounted is still reported — with its own
    message — rather than the pane coming back showing an idle button and a build
    still running behind it.

    `idle` is a real answer and not an error: nothing has been launched from this
    account this run.
    """
    job = _LAUNCH_JOBS.get(_account_id())
    if job is None:
        return LaunchNativeResponse(launched=False, phase="idle")
    return _launch_job_response(job)


async def _perform_launch(
    req: LaunchNativeRequest, origin: str, job: dict[str, Any]
) -> LaunchNativeResponse:
    """Resolve a binary, build it if it is stale, start it, and watch it settle.

    Everything the route used to do inline. It reports into `job` as it goes
    rather than only at the end, because the phase *is* the information while it
    is running: "building" and "starting" want different words on screen and take
    different amounts of time.
    """
    try:
        result = await _launch_now(req, origin, job)
    except Exception as exc:  # noqa: BLE001 - a job has nobody to raise to
        logger.exception("hassault: the native client launch failed")
        result = LaunchNativeResponse(
            launched=False,
            connect_args=[],
            message=f"Could not launch native client: {exc}",
        )
    job["result"] = result
    job["phase"] = "launched" if result.launched else "failed"
    return result


async def _launch_now(
    req: LaunchNativeRequest, origin: str, job: dict[str, Any]
) -> LaunchNativeResponse:
    import subprocess

    from backend.modules.settings.routes import get_value

    repo_root = Path(__file__).resolve().parents[3]
    custom_bin = str(get_value("hassault.nativeBinaryPath", "") or "").strip()
    local_bins = _local_client_candidates(repo_root)

    bin_path = pick_binary(custom_bin, local_bins)

    # **Tier 3: the downloaded client, and only when the first two found nothing.**
    #
    # It is deliberately *not* another entry in `local_bins`. `pick_binary`
    # takes the **newest** build on disk — which is the right rule among build
    # outputs and exactly the wrong one here: a client downloaded today beats a
    # developer's `target/debug` from an hour ago, so the first launch after an
    # install would silently run the release instead of the edit being worked on.
    # That is the same class of bug `pick_binary` was written to end, arriving by
    # a different door. A checkout's own build always wins; the download is what
    # a machine with no toolchain gets.
    downloaded = None
    if not bin_path:
        installed = client_install.installed_binary()
        downloaded = str(installed) if installed else None

    # **The binary is checked against its own source before it is launched.**
    #
    # `pick_binary` takes the newest of the builds on disk, which closed one trap
    # — a stale `release` preferred over a fresh `debug` — but not this one: the
    # newest build on disk is still older than the source the moment anybody edits
    # the client. Nothing said so. The game started, ran perfectly, and simply did
    # not contain the change, which reads as a change that did not work rather
    # than as a build that never happened.
    #
    # An explicit `hassault.nativeBinaryPath` is exempt, and so is a machine with
    # no crate beside the binary (a packaged install): in the first somebody named
    # the binary they mean, and in the second there is no source to be stale
    # against.
    crate_root = repo_root / "apps" / "native-fps"
    rebuilt = False
    build_seconds: float | None = None
    stale = False
    # `not downloaded` as well as `not custom_bin`: a downloaded client is not a
    # build output and has no source to be stale against. Without it, a checkout
    # whose `target/` has been cleaned would look infinitely stale and answer an
    # install by starting a surprise `cargo build` — minutes, on the machine least
    # likely to have a toolchain at all.
    if not custom_bin and not downloaded:
        src_mtime = newest_source_mtime(crate_root)
        built_at = 0.0
        if bin_path:
            try:
                built_at = Path(bin_path).stat().st_mtime
            except OSError:
                built_at = 0.0
        if src_mtime is not None and built_at < src_mtime:
            auto_build = bool(get_value("hassault.autoBuildNative", True))
            if auto_build:
                # The profile already on disk, so a debug iteration loop is not
                # silently upgraded into an optimised build every launch.
                profile = (
                    "debug"
                    if bin_path and Path(bin_path).parent.name == "debug"
                    else "release"
                )
                # Said before it starts, not after it finishes: this is the
                # minutes-long branch, and the whole point of the job is that
                # somebody can be told which branch they are in while they are
                # in it.
                job["phase"] = "building"
                started = time.monotonic()
                ok, detail = await asyncio.to_thread(
                    build_native_client, crate_root, profile
                )
                build_seconds = round(time.monotonic() - started, 1)
                if not ok:
                    return LaunchNativeResponse(
                        launched=False,
                        connect_args=[],
                        rebuilt=False,
                        build_seconds=build_seconds,
                        message=detail,
                    )
                rebuilt = True
                job["phase"] = "starting"
                # Re-resolved: the build just changed which binary is newest, and
                # a first-ever build created one where `pick_binary` found none.
                bin_path = pick_binary("", local_bins)
            else:
                # Auto-build is off, so this is somebody running what is on disk on
                # purpose — launched, but never silently.
                stale = True

    # A remote room is the one combination that cannot work: the channel refuses a
    # join carrying a host and no room ("a remote match needs a room id"), and the
    # refusal would arrive inside a window that had already opened. Caught here,
    # where there is still a browser listening to say it to.
    if req.host and not req.room_id:
        return LaunchNativeResponse(
            launched=False,
            connect_args=[],
            message="A match on a friend's node needs a room id.",
        )

    # `origin` is this node as the caller reached it — read off the request that
    # started the job, which is the only address known to be right, and captured
    # there rather than here because the request is long gone by the time a build
    # finishes.
    connect_args = [
        f"--server={origin}",
        f"--map={req.map_name}",
        f"--mode={req.mode}",
        f"--max-fps={req.max_fps}",
    ]
    # Absent, not empty: an empty `--room` would be a request to join a room whose
    # id is the empty string. Same for `--host`, which the client forwards as "this
    # match is on a friend's node".
    if req.room_id:
        connect_args.append(f"--room={req.room_id}")
    if req.host:
        connect_args.append(f"--host={req.host}")
    if req.mode == "host" and req.bots > 0:
        # Clamped against the match server's own ceiling, and only sent for the one
        # mode that can act on it — `add_bot` is host-only on the channel, so a bot
        # count on a join is an instruction the server would refuse.
        count = max(0, min(int(req.bots), MAX_PLAYERS - 1))
        if count:
            connect_args.append(f"--bots={count}")
            connect_args.append(f"--bot-skill={req.bot_skill}")
    if req.username:
        # A wire label only. The backend takes the real name from the signed-in
        # account and ignores this, which is why it is not worth defaulting to
        # "Player" — a placeholder that never reaches anything is just noise.
        connect_args.append(f"--name={req.username}")

    bin_path = bin_path or downloaded
    if not bin_path:
        # Two different failures, and telling somebody to run `cargo` when they
        # have no toolchain is the one that reads as "this game is not for you".
        # The message names the button that fixes it in both cases; the cargo line
        # survives only for the checkout, where it is the faster answer.
        has_crate = (crate_root / "Cargo.toml").is_file()
        install_hint = "Install it from the main menu's Native client row"
        return LaunchNativeResponse(
            launched=False,
            connect_args=connect_args,
            message=(
                f"The native client is not built. {install_hint}, run `cargo build "
                "--release --manifest-path apps/native-fps/Cargo.toml`, or set "
                "'hassault.nativeBinaryPath' in Settings."
                if has_crate
                else f"The native client is not installed. {install_hint}."
            ),
        )

    try:
        import threading

        account_id = _account_id()

        # **The client is never handed this process's stdio.**
        #
        # It used to inherit it, and that is a launch that fails for a reason
        # having nothing to do with the game: the backend is routinely an orphan
        # (`pnpm dev` exits, or a `--reload` parent dies) whose stdout and stderr
        # are pipes with no reader left. The client's first act is an
        # `eprintln!` — "hassault: loading <map> from <origin>" — so on Windows
        # that write fails, Rust panics on a failed print to stderr, and the
        # process is gone (exit 101) before a window is ever created. Nothing
        # said so: `Popen` had returned a pid, so the pane reported a successful
        # launch of a client that no longer existed.
        #
        # A file also keeps the diagnostics, which `DEVNULL` would throw away —
        # and the client's startup lines are the only place the map, the GPU
        # backend and a missing loadout are reported. Truncated per launch, so
        # its tail is *this* run.
        # Deliberately **not** `log_dir()`: a second file in `logs/` breaks the
        # dev backend outright, because `--reload-exclude "logs/*"` is expanded by
        # the shell that launches it and uvicorn is handed the extra filename as a
        # positional argument. A log nobody asked for is not worth a backend that
        # will not boot.
        log_path = data_dir() / "hassault" / "native-client.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            child_log: Any = open(log_path, "wb")
        except OSError:
            child_log = subprocess.DEVNULL

        try:
            proc = subprocess.Popen(
                [bin_path, *connect_args],
                stdin=subprocess.DEVNULL,
                stdout=child_log,
                stderr=subprocess.STDOUT,
            )
        finally:
            # Our copy of the handle; the child has its own.
            if child_log is not subprocess.DEVNULL:
                child_log.close()

        # **Did it survive?** A pid is not a running game. Everything that kills
        # this client kills it in the first moments — a GPU with no usable
        # backend, a node it cannot read, a panic on startup — so a short wait
        # turns "launched" from an assumption into an observation, and the tail
        # of the log says which of those it was.
        await asyncio.sleep(_LAUNCH_SETTLE_SECONDS)
        code = proc.poll()
        if code is not None:
            detail = _tail(log_path)
            return LaunchNativeResponse(
                launched=False,
                connect_args=connect_args,
                message=(
                    f"The native client exited immediately (code {code})."
                    + (f" {detail}" if detail else "")
                    + f" Full output: {log_path}"
                ),
            )

        ACTIVE_GAME_PROCESSES[account_id] = proc
        _LAUNCHED_AT[account_id] = time.time()

        t = threading.Thread(
            target=_watchdog_game_process,
            args=(account_id, req.map_name, proc),
            daemon=True,
        )
        t.start()

        # Which build ran, and how old it is.
        #
        # Both stale-build traps are closed above — `pick_binary` takes the newest
        # candidate, and a binary older than its source is rebuilt first — so this
        # is no longer the only defence. It is the receipt: "is the window I am
        # looking at the code I just compiled?" answered on the launch itself,
        # because the failure it disambiguates is silent by nature.
        built = "age unknown"
        try:
            age = time.time() - Path(bin_path).stat().st_mtime
            if age < 120:
                built = "built just now"
            elif age < 3600:
                built = f"built {int(age // 60)}m ago"
            else:
                built = f"built {age / 3600:.1f}h ago"
        except OSError:
            pass
        note = ""
        if rebuilt:
            note = f", rebuilt in {build_seconds:.0f}s"
        elif stale:
            note = (
                " — WARNING: this build predates your latest source change, and "
                "'hassault.autoBuildNative' is off, so it does not contain it"
            )
        return LaunchNativeResponse(
            launched=True,
            pid=proc.pid,
            connect_args=connect_args,
            rebuilt=rebuilt,
            build_seconds=build_seconds,
            stale=stale,
            message=(
                f"Launched native FPS client (PID: {proc.pid}) "
                f"from {Path(bin_path).parent.name}/, {built}{note}"
            ),
        )
    except Exception as exc:
        return LaunchNativeResponse(
            launched=False,
            connect_args=connect_args,
            message=f"Could not launch native client: {exc}",
        )


@router.get("/client/status", response_model=NativeClientStatus)
async def native_client_status() -> NativeClientStatus:
    """Which of the three tiers would answer a launch right now.

    Served rather than inferred by the pane, because the tiers are resolved in
    Python and a second copy of the ordering in TypeScript is a second chance to
    get it backwards — the browser would then offer to install a client over a
    local build that is about to win anyway.
    """
    from backend.modules.settings.routes import get_value

    repo_root = Path(__file__).resolve().parents[3]
    crate_root = repo_root / "apps" / "native-fps"
    custom_bin = str(get_value("hassault.nativeBinaryPath", "") or "").strip()
    version = app_version()
    install = client_install.read_install(version)

    local = pick_binary(custom_bin, _local_client_candidates(repo_root))
    if custom_bin and local == custom_bin:
        source, binary = "setting", local
    elif local:
        source, binary = "build", local
    elif install:
        source, binary = "download", str(install.binary)
    else:
        source, binary = "none", None

    return NativeClientStatus(
        source=source,
        binary=binary,
        version=version,
        installed=install is not None,
        verified=bool(install and install.verified),
        installed_size_bytes=install.size_bytes if install else None,
        has_crate=(crate_root / "Cargo.toml").is_file(),
    )


@router.post("/client/install")
async def install_native_client(req: ClientInstallRequest) -> StreamingResponse:
    """Download the prebuilt client, streaming progress as NDJSON.

    Streamed for the same reason `llamacpp/install` is: this is tens of megabytes
    over somebody's connection, and a request that simply takes a minute to return
    is indistinguishable from one that has hung.
    """

    async def gen() -> AsyncIterator[str]:
        async for event in client_install.install_client(req.version or None):
            yield json.dumps(event) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.post("/client/remove")
def remove_native_client(req: ClientRemoveRequest) -> dict[str, bool]:
    return {"removed": client_install.remove_install(req.version or app_version())}


@router.get("/match/process_status")
async def get_process_status() -> dict[str, Any]:
    """Check if the native game client is currently running."""
    proc = ACTIVE_GAME_PROCESSES.get(_account_id())
    running = proc is not None and proc.poll() is None
    return {"running": running, "pid": proc.pid if running and proc else None}


@router.get("/match/latest_summary")
async def get_latest_match_summary() -> dict[str, Any] | None:
    """The most recent match this account has not dismissed, or `null`.

    Read from `app.db` rather than from a process-global dict, which is what
    makes a debrief survive the backend restarting under it — and what makes a
    match history a thing that exists at all.
    """
    from backend.modules.hassault import results
    from backend.modules.hassault.skins import skin_dict, skin_manager

    summary = results.latest(_account_id())
    if summary is None:
        return None
    # The drop is stored as an id and resolved here: the card wants the
    # definition (name, rarity colour, wear), and duplicating that into the match
    # row would mean a renamed skin showing its old name forever.
    drop_id = summary.pop("dropId", None)
    if drop_id:
        instance = skin_manager.find_instance(_account_id(), str(drop_id))
        if instance is not None:
            summary["earnedDrop"] = instance.to_dict(skin_dict().get(instance.skin_id))
    return summary


@router.post("/match/dismiss_summary")
async def dismiss_match_summary() -> dict[str, bool]:
    """Mark the outstanding debrief as seen.

    A column on the row, not a delete: the row *is* the match history, and
    closing a card is not a claim that the match did not happen.
    """
    from backend.modules.hassault import results

    results.dismiss(_account_id())
    return {"ok": True}


@router.get("/ranked/maps")
async def get_ranked_maps() -> dict[str, list[str]]:
    """Maps the game server will adjudicate, asked of the game server.

    Proxied rather than derived locally from `source == "bundled"`. The two agree
    today and the server's answer is the one that decides — so a map added on
    either side needs no matching change on the other, and a server that is down
    greys the button out instead of failing at the socket after a map has loaded.
    """
    import httpx

    from backend.modules.games.client import resolve_server_url

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(f"{resolve_server_url().rstrip('/')}/hassault/maps")
            res.raise_for_status()
            maps = res.json().get("maps") or []
    except Exception as exc:
        # Not an error to the caller: "no ranked maps" is exactly what an
        # unreachable server means to a menu.
        logger.info("hassault: could not read the ranked map list: %s", exc)
        return {"maps": []}
    return {"maps": [str(m) for m in maps]}


@router.get("/match/history")
async def get_match_history(limit: int = 20) -> list[dict[str, Any]]:
    """Recent matches, newest first — the debrief card shows one, this is the rest.

    Worth existing the moment results became rows: a history that only the card
    could read would be a table nothing queries.
    """
    from backend.modules.hassault import results

    return results.history(_account_id(), limit)


@router.get("/skins/catalog")
async def get_skin_catalog() -> list[dict[str, Any]]:
    """Master catalog of all available skin designs, rarities and collections.

    The built-ins **and** every installed pack — see `skinpacks.py`. A pack skin
    carries the extra `packId` and `textureUrl` keys; a built-in carries neither,
    which is what tells a client there is nothing to fetch rather than something
    that failed to load.
    """
    from backend.modules.hassault.skinpacks import installed_skins
    from backend.modules.hassault.skins import SKIN_CATALOG

    return [s.to_dict() for s in SKIN_CATALOG] + [
        s.to_dict() for s in installed_skins()
    ]


@router.get("/skins/inventory")
async def get_skin_inventory() -> list[dict[str, Any]]:
    """Get the active player's skin inventory with float values, pattern seeds and wear."""
    from backend.modules.hassault.skins import skin_dict, skin_manager

    account_id = _account_id()

    # Atlas is consulted **only when this node has never seen this account** —
    # picking up an inventory earned on another machine. It used to run on every
    # read, which is a Mongo round trip per poll for a document that changes only
    # when this node changes it (and every mutation below syncs back).
    if not skin_manager.has_local(account_id):
        await skin_manager.load_from_atlas(account_id)
    items = skin_manager.get_inventory(account_id)
    known = skin_dict()
    return [item.to_dict(known.get(item.skin_id)) for item in items]


# -----------------------------------------------------------------------------
# Skin packs: skins that are not in this repo
# -----------------------------------------------------------------------------
#
# See `backend/modules/hassault/skinpacks.py` for the format and for the rules
# that are silent if broken. These routes are deliberately thin — every check
# lives in that module, so the agent tools and a future CLI get the same one.


class SkinPackInstall(BaseModel):
    """Where to fetch a pack from, and optionally what it should hash to."""

    url: str
    #: Hex sha256. Optional, and its absence is recorded rather than assumed
    #: away: a pack installed without one is reported `verified: false`.
    sha256: str | None = None


@router.get("/skins/packs")
async def list_skin_packs() -> list[dict[str, Any]]:
    """Every skin pack installed on this node."""
    from backend.modules.hassault.skinpacks import installed_packs

    return [p.to_dict() for p in installed_packs()]


@router.post("/skins/packs/install")
async def install_skin_pack(body: SkinPackInstall) -> dict[str, Any]:
    """Download a skin pack to this node and install it.

    The fetch is SSRF-guarded (`browser.fetch`), the archive is validated before
    a byte is written, and the pack lands via a staging directory and a rename —
    so a failure here leaves the node exactly as it was.
    """
    # Imported here, like every other heavy dependency in this file: reaching
    # `browser.fetch` pulls the extraction stack in behind it, and a module that
    # every hassault route pays for at import time to serve one of them is the
    # kind of cost that only shows up as a slow boot.
    from backend.modules.browser.fetch import UnsafeUrlError
    from backend.modules.hassault.skinpacks import PackError, install_from_url
    from backend.modules.hassault.skins import invalidate_pack_cache

    try:
        pack = await install_from_url(body.url, sha256=body.sha256)
    except PackError as exc:
        # 400, not 500: every one of these is something about the *pack*, and the
        # message names which rule it broke. A 500 would file a user's malformed
        # manifest as a bug in the node.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=f"unsafe url: {exc}") from exc
    invalidate_pack_cache()
    return pack.to_dict()


@router.delete("/skins/packs/{pack_id}")
async def remove_skin_pack(pack_id: str) -> dict[str, bool]:
    """Delete an installed pack and everything in it."""
    from backend.modules.hassault.skinpacks import PackError, remove_pack
    from backend.modules.hassault.skins import invalidate_pack_cache

    try:
        removed = remove_pack(pack_id)
    except PackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"no pack '{pack_id}' is installed")
    invalidate_pack_cache()
    return {"removed": True}


@router.get("/skins/packs/{pack_id}/files/{name}")
async def get_skin_pack_file(pack_id: str, name: str) -> FileResponse:
    """Serve one file out of an installed pack — a skin's texture.

    `texture_path` resolves and containment-checks the path; a `..` or a symlink
    pointing out of the pack is a 400 here rather than an arbitrary-file read.
    """
    from backend.modules.hassault.skinpacks import PackError, texture_path

    try:
        path = texture_path(pack_id, name)
    except PackError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(path)


@router.post("/skins/equip")
async def equip_skin(instance_id: str) -> dict[str, bool]:
    """Equip an item instance to its weapon loadout slot."""
    from backend.modules.hassault.skins import skin_manager

    account_id = _account_id()

    ok = skin_manager.equip_skin(account_id, instance_id)
    if ok:
        await skin_manager.sync_to_atlas(account_id)
    return {"ok": ok}


@router.get("/skins/drops")
async def get_drop_status() -> dict[str, Any]:
    """How many level-up drops are waiting, and what earns the next one.

    Served rather than derived in the browser — the level and the ledger both
    live here, and a client that computed "you have 3" from a level it had
    would be a second opinion on the one thing the claim route enforces.
    """
    from backend.modules.hassault.skins import skin_manager

    return skin_manager.drop_status(_account_id())


@router.post("/skins/claim_drop")
async def claim_level_up_drop() -> dict[str, Any]:
    """Spend one level-up entitlement on a weighted-RNG skin drop.

    409 when there is nothing to spend. This used to roll unconditionally, which
    made the banner's button an infinite skin dispenser: every press produced a
    new item, the button never went away, and a Covert was a matter of clicking
    for a minute.
    """
    from backend.modules.hassault.skins import skin_dict, skin_manager

    account_id = _account_id()

    drop = skin_manager.claim_level_drop(account_id)
    if drop is None:
        status = skin_manager.drop_status(account_id)
        raise HTTPException(
            status_code=409,
            detail=(
                f"No level-up drop available. You are level {status['level']}; "
                f"{status['xpToNextDrop']} XP earns the next one."
            ),
        )
    await skin_manager.sync_to_atlas(account_id)
    payload = drop.to_dict(skin_dict().get(drop.skin_id))
    payload["remaining"] = skin_manager.drop_status(account_id)["available"]
    return payload


@router.post("/skins/tradeup")
async def execute_trade_up(instance_ids: list[str]) -> dict[str, Any]:
    """Trade in 10 skins of rarity Tier N to forge 1 skin of Tier N+1."""
    from backend.modules.hassault.skins import skin_dict, skin_manager

    account_id = _account_id()

    result = skin_manager.trade_up_contract(account_id, instance_ids)
    if not result:
        raise HTTPException(
            status_code=400,
            detail="Trade-Up Contract requires exactly 10 items of the same rarity tier.",
        )
    await skin_manager.sync_to_atlas(account_id)
    return result.to_dict(skin_dict().get(result.skin_id))


# -----------------------------------------------------------------------------
# Developer Console & Macro Endpoints
# -----------------------------------------------------------------------------


@router.get("/console/definitions", response_model=ConsoleDefinitionsResponse)
async def get_console_definitions() -> ConsoleDefinitionsResponse:
    """Return all registered CVars, ConCommands, and Macros for the console."""
    return ConsoleDefinitionsResponse(
        cvars=list(console_registry.cvars.values()),
        commands=list(console_registry.commands.values()),
        macros=list(console_registry.macros.values()),
    )


@router.post("/console/exec", response_model=ConsoleExecResponse)
async def exec_console_command(req: ConsoleExecRequest) -> ConsoleExecResponse:
    """Execute a developer console command, CVar query/assignment, or Python script."""
    return await console_registry.execute(req)


@router.get("/console/macros", response_model=list[MacroDefinition])
async def list_console_macros() -> list[MacroDefinition]:
    """List all available developer console macros."""
    return list(console_registry.macros.values())


class SaveMacroRequest(BaseModel):
    name: str
    code: str
    description: str = ""


@router.post("/console/macros", response_model=MacroDefinition)
async def save_console_macro(req: SaveMacroRequest) -> MacroDefinition:
    """Save or update a developer console macro."""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Macro name cannot be empty")
    return console_registry.save_macro(req.name, req.code, req.description)


@router.delete("/console/macros/{name}")
async def delete_console_macro(name: str) -> dict[str, bool]:
    """Delete a user-created developer console macro."""
    ok = console_registry.delete_macro(name)
    if not ok:
        raise HTTPException(status_code=404, detail="Macro not found or is builtin")
    return {"ok": True}
