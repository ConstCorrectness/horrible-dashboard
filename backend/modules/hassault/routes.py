"""REST surface for HorribleAssault's map pipeline, mounted at `/api/hassault`.

The split between the two map endpoints is deliberate. A 256×256 map is 65 536
cubes across nine fields; as JSON that is roughly 590 000 numbers and several
megabytes, which is slow to serialize and slower to parse. So metadata and
entities come back as JSON, and the grid comes back as **raw concatenated byte
planes** the browser adopts directly as typed arrays — one copy, no parsing.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from backend.modules.hassault import assets, fabric
from backend.modules.hassault.cgz import PLANE_ORDER, CgzError
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
    root = assets.install_root()
    from backend.modules.settings.routes import get_value

    configured = bool(str(get_value("hassault.installPath", "") or "").strip())
    if root is None:
        return InstallStatus(
            found=False,
            configured=configured,
            message=(
                "That path is not an AssaultCube install (no packages/maps inside)."
                if configured
                else "No AssaultCube install found. Set hassault.installPath to "
                "your copy — game content is never bundled with this app."
            ),
        )
    return InstallStatus(
        found=True,
        path=str(root),
        configured=configured,
        map_count=len(assets.list_maps()),
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
