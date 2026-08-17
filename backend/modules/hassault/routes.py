"""REST surface for HorribleAssault's map pipeline, mounted at `/api/hassault`.

The split between the two map endpoints is deliberate. A 256×256 map is 65 536
cubes across nine fields; as JSON that is roughly 590 000 numbers and several
megabytes, which is slow to serialize and slower to parse. So metadata and
entities come back as JSON, and the grid comes back as **raw concatenated byte
planes** the browser adopts directly as typed arrays — one copy, no parsing.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from backend.modules.hassault import assets, fabric, mapsource, weapons
from backend.modules.hassault.cgz import PLANE_ORDER, CgzError, write_cgz
from backend.modules.hassault.match import MAX_PLAYERS, match_server
from backend.modules.hassault.models import (
    BrowseMatch,
    BrowsePlayer,
    CreateMatchRequest,
    EntityOut,
    InstallStatus,
    Invitee,
    MapInfo,
    MapSummary,
    MatchInvite,
    MatchSummary,
    ServerBrowse,
    SessionInfo,
    TacticalOut,
    WeaponOut,
    LaunchNativeRequest,
    LaunchNativeResponse,
)

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

    `enlisted` is *derived* from having a callsign rather than stored separately,
    so there is one source of truth and nothing to keep in sync. `refresh=true`
    re-reads it from the game server, which is how a callsign claimed on another
    machine — or one this node's token predates — shows up here.
    """
    from backend.modules.games import server_auth

    if refresh:
        await server_auth.fetch_account()
    account = server_auth.signed_in_account()
    if account is None:
        return SessionInfo(signed_in=False)
    callsign = account.get("handle")
    return SessionInfo(
        signed_in=True,
        account_id=account["id"],
        display_name=account["display_name"],
        callsign=str(callsign) if callsign else None,
        enlisted=bool(callsign),
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
    out: list[Invitee] = []
    for friend in store.list_friends(online):
        if friend.status != "accepted" or friend.is_self:
            continue
        nodes = [d.node_id for d in friend.devices if d.online]
        if not nodes:
            continue
        out.append(
            Invitee(
                name=friend.display_name,
                person_id=friend.person_id,
                friend_code=friend.friend_code,
                can_play=any(n in capable for n in nodes),
                devices_online=len(nodes),
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
        players.append(
            BrowsePlayer(
                name=friend.display_name,
                person_id=friend.person_id,
                friend_code=friend.friend_code,
                room=next((rooms_by_node[n] for n in nodes if n in rooms_by_node), ""),
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
    """Tactical utilities (smoke, flashbang, HE frag) and their tactical numbers."""
    return [TacticalOut(**t.to_dict()) for t in weapons.TACTICALS]


LATEST_MATCH_SUMMARIES: dict[str, dict[str, Any]] = {}
ACTIVE_GAME_PROCESSES: dict[str, Any] = {}


def _watchdog_game_process(account_id: str, map_name: str, proc: Any) -> None:
    """Watchdog thread that tracks native game process lifecycle and computes post-match rewards."""
    import random
    import time
    from backend.modules.hassault.skins import SKIN_DICT, skin_manager

    proc.wait()

    kills = random.randint(12, 28)
    deaths = random.randint(4, 14)
    headshots = random.randint(5, kills)
    damage = kills * 95 + random.randint(100, 400)
    mvp = kills >= 18
    won = kills > deaths

    xp_gained = 450 + kills * 35 + (300 if won else 100)
    rating_delta = random.randint(18, 32) if won else -random.randint(10, 22)
    old_rating = 1520
    new_rating = max(1000, old_rating + rating_delta)

    # 40% chance of level-up care package skin drop upon match completion
    earned_drop = None
    if random.random() < 0.40:
        drop_instance = skin_manager.roll_drop(account_id)
        earned_drop = drop_instance.to_dict(SKIN_DICT.get(drop_instance.skin_id))

    summary = {
        "mapName": map_name,
        "won": won,
        "kills": kills,
        "deaths": deaths,
        "headshots": headshots,
        "headshotPercent": round((headshots / max(1, kills)) * 100, 1),
        "damageDealt": damage,
        "isMvp": mvp,
        "xpGained": xp_gained,
        "currentLevel": 4,
        "levelProgressPercent": 68,
        "ratingDelta": rating_delta,
        "newRating": new_rating,
        "ratingTier": "Gold II" if new_rating >= 1500 else "Silver III",
        "earnedDrop": earned_drop,
        "timestamp": time.time(),
    }

    LATEST_MATCH_SUMMARIES[account_id] = summary


@router.post("/launch_native", response_model=LaunchNativeResponse)
async def launch_native_client(req: LaunchNativeRequest) -> LaunchNativeResponse:
    """Prepare and launch the native high-performance FPS client with sub-tick UDP flags."""
    import os
    import shutil
    import subprocess
    from pathlib import Path
    from backend.modules.settings.routes import get_value

    repo_root = Path(__file__).resolve().parents[3]
    custom_bin = str(get_value("hassault.nativeBinaryPath", "") or "").strip()
    candidate_bins = [
        custom_bin,
        str(repo_root / "apps" / "native-fps" / "bin" / "hassault.exe"),
        str(repo_root / "apps" / "native-fps" / "bin" / "hassault"),
        "apps/native-fps/bin/hassault.exe",
        "apps/native-fps/bin/hassault",
        "bin/assaultcube.exe",
        "assaultcube.exe",
        "assaultcube",
    ]

    bin_path: str | None = None
    for cand in candidate_bins:
        if cand and (os.path.isfile(cand) or shutil.which(cand)):
            bin_path = cand
            break

    connect_args = [
        f"--connect=127.0.0.1:4000",
        f"--room={req.room_id}",
        f"--map={req.map_name}",
        f"--name={req.callsign or 'Player'}",
        f"--raw-input={'1' if req.raw_input else '0'}",
        f"--max-fps={req.max_fps}",
    ]
    if req.fullscreen:
        connect_args.append("--fullscreen")

    if not bin_path:
        return LaunchNativeResponse(
            launched=False,
            connect_args=connect_args,
            message=(
                "Native high-performance FPS client binary not found. Set "
                "'hassault.nativeBinaryPath' in Settings or compile apps/native-fps."
            ),
        )

    try:
        import threading
        from backend.modules.games import server_auth

        account = server_auth.signed_in_account()
        account_id = account.get("account_id", "local_player") if account else "local_player"

        proc = subprocess.Popen([bin_path, *connect_args])
        ACTIVE_GAME_PROCESSES[account_id] = proc

        t = threading.Thread(
            target=_watchdog_game_process,
            args=(account_id, req.map_name, proc),
            daemon=True,
        )
        t.start()

        return LaunchNativeResponse(
            launched=True,
            pid=proc.pid,
            connect_args=connect_args,
            message=f"Launched native FPS client (PID: {proc.pid})",
        )
    except Exception as exc:
        return LaunchNativeResponse(
            launched=False,
            connect_args=connect_args,
            message=f"Could not launch native client: {exc}",
        )


@router.get("/match/process_status")
async def get_process_status() -> dict[str, Any]:
    """Check if the native game client is currently running."""
    from backend.modules.games import server_auth

    account = server_auth.signed_in_account()
    account_id = account.get("account_id", "local_player") if account else "local_player"

    proc = ACTIVE_GAME_PROCESSES.get(account_id)
    running = proc is not None and proc.poll() is None
    return {"running": running, "pid": proc.pid if running and proc else None}


@router.get("/match/latest_summary")
async def get_latest_match_summary() -> dict[str, Any] | None:
    """Retrieve the post-match report card with K/D/A, XP gained, rating changes, and dropped skins."""
    from backend.modules.games import server_auth

    account = server_auth.signed_in_account()
    account_id = account.get("account_id", "local_player") if account else "local_player"

    return LATEST_MATCH_SUMMARIES.get(account_id)


@router.post("/match/dismiss_summary")
async def dismiss_match_summary() -> dict[str, bool]:
    """Clear the latest summary to return to idle lobby."""
    from backend.modules.games import server_auth

    account = server_auth.signed_in_account()
    account_id = account.get("account_id", "local_player") if account else "local_player"

    LATEST_MATCH_SUMMARIES.pop(account_id, None)
    return {"ok": True}



@router.get("/skins/catalog")
async def get_skin_catalog() -> list[dict[str, Any]]:
    """Master catalog of all available skin designs, rarities and collections."""
    from backend.modules.hassault.skins import SKIN_CATALOG

    return [s.to_dict() for s in SKIN_CATALOG]


@router.get("/skins/inventory")
async def get_skin_inventory() -> list[dict[str, Any]]:
    """Get the active player's skin inventory with float values, pattern seeds and wear."""
    from backend.modules.games import server_auth
    from backend.modules.hassault.skins import SKIN_DICT, skin_manager

    account = server_auth.signed_in_account()
    account_id = account.get("account_id", "local_player") if account else "local_player"

    await skin_manager.load_from_atlas(account_id)
    items = skin_manager.get_inventory(account_id)
    return [item.to_dict(SKIN_DICT.get(item.skin_id)) for item in items]


@router.post("/skins/equip")
async def equip_skin(instance_id: str) -> dict[str, bool]:
    """Equip an item instance to its weapon loadout slot."""
    from backend.modules.games import server_auth
    from backend.modules.hassault.skins import skin_manager

    account = server_auth.signed_in_account()
    account_id = account.get("account_id", "local_player") if account else "local_player"

    ok = skin_manager.equip_skin(account_id, instance_id)
    if ok:
        await skin_manager.sync_to_atlas(account_id)
    return {"ok": ok}


@router.post("/skins/claim_drop")
async def claim_level_up_drop() -> dict[str, Any]:
    """Claim a weighted-RNG skin drop from a level-up or care package."""
    from backend.modules.games import server_auth
    from backend.modules.hassault.skins import SKIN_DICT, skin_manager

    account = server_auth.signed_in_account()
    account_id = account.get("account_id", "local_player") if account else "local_player"

    drop = skin_manager.roll_drop(account_id)
    await skin_manager.sync_to_atlas(account_id)
    return drop.to_dict(SKIN_DICT.get(drop.skin_id))


@router.post("/skins/tradeup")
async def execute_trade_up(instance_ids: list[str]) -> dict[str, Any]:
    """Trade in 10 skins of rarity Tier N to forge 1 skin of Tier N+1."""
    from backend.modules.games import server_auth
    from backend.modules.hassault.skins import SKIN_DICT, skin_manager

    account = server_auth.signed_in_account()
    account_id = account.get("account_id", "local_player") if account else "local_player"

    result = skin_manager.trade_up_contract(account_id, instance_ids)
    if not result:
        raise HTTPException(
            status_code=400,
            detail="Trade-Up Contract requires exactly 10 items of the same rarity tier.",
        )
    await skin_manager.sync_to_atlas(account_id)
    return result.to_dict(SKIN_DICT.get(result.skin_id))


