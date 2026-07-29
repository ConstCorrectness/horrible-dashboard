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
    CreateMatchRequest,
    EntityOut,
    InstallStatus,
    Invitee,
    MapInfo,
    MapSummary,
    MatchInvite,
    MatchSummary,
    SessionInfo,
    WeaponOut,
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
