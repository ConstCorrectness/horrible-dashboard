"""REST surface for HorribleAssault's map pipeline, mounted at `/api/hassault`.

The split between the two map endpoints is deliberate. A 256×256 map is 65 536
cubes across nine fields; as JSON that is roughly 590 000 numbers and several
megabytes, which is slow to serialize and slower to parse. So metadata and
entities come back as JSON, and the grid comes back as **raw concatenated byte
planes** the browser adopts directly as typed arrays — one copy, no parsing.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from backend.modules.hassault import assets, fabric, hitbox, lore, mapsource, weapons
from backend.modules.hassault.cgz import PLANE_ORDER, CgzError, write_cgz
from backend.modules.hassault.match import MAX_PLAYERS, match_server
from backend.modules.hassault.models import (
    BrowseMatch,
    BrowsePlayer,
    CreateMatchRequest,
    EntityOut,
    HitboxOut,
    HitboxTuneRequest,
    InstallStatus,
    Invitee,
    LoreOut,
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
    """Tactical utilities (smoke, flashbang, HE frag) and their tactical numbers."""
    return [TacticalOut(**t.to_dict()) for t in weapons.TACTICALS]


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
    from backend.modules.hassault.skins import SKIN_DICT, skin_manager

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
        results.attach_drop(summary["matchId"], drop.instance_id)
        _ = SKIN_DICT.get(drop.skin_id)
    except Exception:
        logger.exception("hassault: could not roll a drop for the finished match")


#: When each account's client was launched, so a stale undismissed card is not
#: mistaken for the match that just ended.
_LAUNCHED_AT: dict[str, float] = {}


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
    import subprocess
    from pathlib import Path

    from backend.modules.settings.routes import get_value

    repo_root = Path(__file__).resolve().parents[3]
    custom_bin = str(get_value("hassault.nativeBinaryPath", "") or "").strip()
    candidate_bins = [
        custom_bin,
        str(
            repo_root
            / "apps"
            / "native-fps"
            / "target"
            / "release"
            / "hassault-native.exe"
        ),
        str(
            repo_root / "apps" / "native-fps" / "target" / "release" / "hassault-native"
        ),
        str(
            repo_root
            / "apps"
            / "native-fps"
            / "target"
            / "debug"
            / "hassault-native.exe"
        ),
        str(repo_root / "apps" / "native-fps" / "target" / "debug" / "hassault-native"),
        str(repo_root / "apps" / "native-fps" / "bin" / "hassault.exe"),
        str(repo_root / "apps" / "native-fps" / "bin" / "hassault"),
    ]

    bin_path = pick_binary(custom_bin, candidate_bins[1:])

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

    # `request.base_url` is this node as the caller reached it, which is the only
    # address known to be right.
    origin = str(request.base_url).rstrip("/")
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

    if not bin_path:
        return LaunchNativeResponse(
            launched=False,
            connect_args=connect_args,
            message=(
                "The native client is not built. Run "
                "`cargo build --release --manifest-path apps/native-fps/Cargo.toml`, "
                "or set 'hassault.nativeBinaryPath' in Settings."
            ),
        )

    try:
        import threading

        account_id = _account_id()

        proc = subprocess.Popen([bin_path, *connect_args])
        ACTIVE_GAME_PROCESSES[account_id] = proc
        _LAUNCHED_AT[account_id] = time.time()

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
    from backend.modules.hassault.skins import SKIN_DICT, skin_manager

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
            summary["earnedDrop"] = instance.to_dict(SKIN_DICT.get(instance.skin_id))
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
    """Master catalog of all available skin designs, rarities and collections."""
    from backend.modules.hassault.skins import SKIN_CATALOG

    return [s.to_dict() for s in SKIN_CATALOG]


@router.get("/skins/inventory")
async def get_skin_inventory() -> list[dict[str, Any]]:
    """Get the active player's skin inventory with float values, pattern seeds and wear."""
    from backend.modules.hassault.skins import SKIN_DICT, skin_manager

    account_id = _account_id()

    # Atlas is consulted **only when this node has never seen this account** —
    # picking up an inventory earned on another machine. It used to run on every
    # read, which is a Mongo round trip per poll for a document that changes only
    # when this node changes it (and every mutation below syncs back).
    if not skin_manager.has_local(account_id):
        await skin_manager.load_from_atlas(account_id)
    items = skin_manager.get_inventory(account_id)
    return [item.to_dict(SKIN_DICT.get(item.skin_id)) for item in items]


@router.post("/skins/equip")
async def equip_skin(instance_id: str) -> dict[str, bool]:
    """Equip an item instance to its weapon loadout slot."""
    from backend.modules.hassault.skins import skin_manager

    account_id = _account_id()

    ok = skin_manager.equip_skin(account_id, instance_id)
    if ok:
        await skin_manager.sync_to_atlas(account_id)
    return {"ok": ok}


@router.post("/skins/claim_drop")
async def claim_level_up_drop() -> dict[str, Any]:
    """Claim a weighted-RNG skin drop from a level-up or care package."""
    from backend.modules.hassault.skins import SKIN_DICT, skin_manager

    account_id = _account_id()

    drop = skin_manager.roll_drop(account_id)
    await skin_manager.sync_to_atlas(account_id)
    return drop.to_dict(SKIN_DICT.get(drop.skin_id))


@router.post("/skins/tradeup")
async def execute_trade_up(instance_ids: list[str]) -> dict[str, Any]:
    """Trade in 10 skins of rarity Tier N to forge 1 skin of Tier N+1."""
    from backend.modules.hassault.skins import SKIN_DICT, skin_manager

    account_id = _account_id()

    result = skin_manager.trade_up_contract(account_id, instance_ids)
    if not result:
        raise HTTPException(
            status_code=400,
            detail="Trade-Up Contract requires exactly 10 items of the same rarity tier.",
        )
    await skin_manager.sync_to_atlas(account_id)
    return result.to_dict(SKIN_DICT.get(result.skin_id))
