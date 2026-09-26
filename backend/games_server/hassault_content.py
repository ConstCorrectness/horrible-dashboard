"""The read-only half of `/api/hassault`, served by the game server.

A hassault client cannot draw a frame without the server's numbers: the cube
planes, the map's item placements, the weapon table, the grenade constants. On a
node those come from `backend/modules/hassault/routes.py`. The standalone web
client (`apps/assault-web`) talks to **this** server instead — there is no node
behind it — so this server has to answer the same paths or the client 404s
before its first frame. (In dev that was invisible: Vite proxied `/api` to a
node and only `/hassault-ws` here.)

### The same handlers, not a copy

Every route is the node's own handler function, registered on a second router.
The reason is the one the node's docstrings give over and over — `plane_order`,
`/weapons`, `/throw`: a number served from two implementations is two chances to
disagree, and the disagreement here would be between the client's prediction and
this server's simulation of the same shot.

### Deliberately less

- **Bundled maps only.** `assets.load_map` also resolves an AssaultCube install
  and `draft:<id>` documents. This server should have neither, and this is a
  public endpoint, so the gate is explicit rather than an accident of what is on
  disk — the same rule `hassault_rooms.playable` applies to a join.
- **GET only.** No hitbox tuning, no drafts, no console, no skins inventory:
  everything that writes, or that means "this node's account", stays on a node.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.modules.hassault import mapsource
from backend.modules.hassault import routes as node
from backend.modules.hassault.models import (
    HitboxOut,
    InstallStatus,
    ItemsResponse,
    LoreOut,
    MapInfo,
    MapSummary,
    ModeOut,
    TacticalOut,
    TextureOut,
    ThrowPhysicsOut,
    WeaponOut,
)

router = APIRouter(prefix="/api/hassault", tags=["hassault"])


def _bundled(name: str) -> None:
    if name not in mapsource.bundled_names():
        raise HTTPException(status_code=404, detail=f"no map named {name!r}")


@router.get("/status", response_model=InstallStatus)
async def get_status() -> InstallStatus:
    bundled = len(mapsource.bundled_names())
    return InstallStatus(
        found=False,
        configured=False,
        map_count=bundled,
        bundled_count=bundled,
        message="Playing the maps that ship with the game.",
    )


@router.get("/maps", response_model=list[MapSummary])
async def get_maps() -> list[MapSummary]:
    return [m for m in await node.get_maps() if m.source == "bundled"]


@router.get("/maps/{name}", response_model=MapInfo)
async def get_map(name: str) -> MapInfo:
    _bundled(name)
    return await node.get_map(name)


@router.get("/maps/{name}/cubes")
async def get_map_cubes(name: str):
    _bundled(name)
    response = await node.get_map_cubes(name)
    # `private` on a node, where the response is one user's; here every client
    # gets the same bytes for a given deploy, so a CDN in front may keep them.
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@router.get("/maps/{name}/mesh")
async def get_map_mesh(name: str):
    _bundled(name)
    return await node.get_map_mesh(name)


# Pure catalogs: no argument, no state, identical on every server running this
# build. Registered straight from the node's router module.
for _path, _handler, _model in (
    ("/modes", node.get_modes, list[ModeOut]),
    ("/weapons", node.get_weapons, list[WeaponOut]),
    ("/weapons/attachments", node.get_weapon_attachments, None),
    ("/items", node.get_items, ItemsResponse),
    ("/tacticals", node.list_tacticals, list[TacticalOut]),
    ("/throw", node.throw_physics, ThrowPhysicsOut),
    ("/hitbox", node.get_hitbox, HitboxOut),
    ("/lore", node.get_lore, LoreOut),
    ("/textures", node.get_textures, list[TextureOut]),
):
    router.add_api_route(_path, _handler, methods=["GET"], response_model=_model)
