"""Pydantic models for the HorribleAssault map API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MapSummary(BaseModel):
    name: str
    """Where the map came from: `bundled` for one this app ships, otherwise the
    install directory it was found in (official / servermaps / …)."""
    source: str
    size: int


class InstallStatus(BaseModel):
    """What this node can play, and whether an AssaultCube install adds to it.

    `found` is about the *install* only — it is deliberately not a gate on
    playing, because the bundled maps need no install at all. The pane decides
    whether it can start a match from `map_count`, and treats `found` as the
    reason a number is smaller than it could be.

    `configured` distinguishes "you have not set this" from "the path you set is
    not an install" — the two need different advice.
    """

    found: bool
    path: str | None = None
    configured: bool = False
    """Every playable map, bundled and installed together — the count the map
    list will actually show."""
    map_count: int = 0
    bundled_count: int = 0
    message: str | None = None


class SessionInfo(BaseModel):
    """Who this node plays as. The pane's gate: no callsign, no match.

    `callsign` is the game server's globally unique handle — the account is shared
    with the games ladder, so there is one sign-in for the whole app rather than a
    second one for this game. `enlisted` is derived from holding a callsign, not
    stored, so the two can never disagree.
    """

    signed_in: bool
    account_id: str | None = None
    display_name: str | None = None
    callsign: str | None = None
    enlisted: bool = False


class EntityOut(BaseModel):
    type: int
    name: str
    x: int
    y: int
    z: int
    yaw: float | None = None
    attrs: list[int] = Field(default_factory=list)


class MapInfo(BaseModel):
    """A map's header and entities. The cube grid is fetched separately as binary."""

    name: str
    title: str
    magic: str
    version: int
    sfactor: int
    ssize: int
    cubic_size: int
    waterlevel: float
    watercolor: list[int]
    maprevision: int
    ambient: int
    flags: int
    timestamp: int
    entity_count: int
    entities: list[EntityOut] = Field(default_factory=list)
    spawns: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False
    legacy_unscaled_attrs: bool = False
    # Byte offsets of each plane inside the `/cubes` payload, so the client can
    # slice one download into typed arrays without guessing the field order.
    plane_order: list[str] = Field(default_factory=list)


class MatchSummary(BaseModel):
    """One live match. Membership and positions are `/ws` traffic, not REST —
    this is only enough to render a lobby row and decide what to join."""

    id: str
    map: str
    players: int
    """How many of `players` are bots, so a lobby row can say "1 + 3 bots"
    rather than advertising a busy match that nobody is in."""
    bots: int = 0
    maxPlayers: int  # noqa: N815 — the browser reads this verbatim
    createdAt: float  # noqa: N815


class BrowseMatch(MatchSummary):
    """A match in the server browser, wherever it is running.

    Extends the lobby row with *whose* it is: `host` is the empty string for a
    match on this node and a node id for a friend's, which is exactly what the
    browser hands back to `join` — so a row is joinable without the pane having to
    know which of the two it is looking at.
    """

    host: str = ""
    hostName: str = ""  # noqa: N815 — the browser reads this verbatim


class BrowsePlayer(BaseModel):
    """Someone who could be in a match: a friend, or a player already in one.

    Presence is *derived* — a friend is here because the fabric has a live session
    with one of their devices — so this is assembled per request and never stored.
    """

    name: str
    person_id: str = ""
    friend_code: str = ""
    """Where they are, when we can tell: a room id on this node, else empty."""
    room: str = ""
    """False means their build predates matches, so inviting them lands nowhere."""
    can_play: bool = True
    devices_online: int = 0


class ServerBrowse(BaseModel):
    """One refresh of the browser: everything running, and everyone reachable."""

    matches: list[BrowseMatch] = Field(default_factory=list)
    players: list[BrowsePlayer] = Field(default_factory=list)
    """Peers asked but not answering, so the pane can say the list is partial."""
    peers_asked: int = 0
    peers_answered: int = 0


class WeaponOut(BaseModel):
    """One weapon's numbers, served rather than duplicated in TypeScript.

    The client needs the fire interval (so it does not send input the server will
    only discard), the magazine size, and a name to put on the HUD. A second copy
    of those constants in the frontend is a drift trap for no gain — the same
    reasoning as `plane_order` on `MapInfo`.
    """

    id: str
    name: str
    damage: float
    headMultiplier: float  # noqa: N815 — read verbatim by the browser
    rpm: float
    """Seconds between shots. Derived from `rpm` here so both sides cannot round
    it differently."""
    interval: float
    mag: int
    reserve: int
    reloadTime: float  # noqa: N815
    spread: float
    pellets: int
    range: float
    auto: bool
    """Cubes per second the shot shoves the shooter, opposite their aim — AC's
    recoil push, and the whole of shoot-jumping. Served rather than duplicated in
    TypeScript because the client predicts the identical impulse."""
    kickback: float
    """Magnifications the scope steps through, in order; empty means no scope.

    The client divides both its field of view *and* its mouse sensitivity by the
    current magnification, so a copy of these in TypeScript would be an aim that
    is wrong only while scoped."""
    zoomLevels: list[float] = []  # noqa: N815
    """Cone half-angle while not scoped. Equal to `spread` for every weapon
    without a scope, so the client can read it unconditionally."""
    hipfireSpread: float = 0.0  # noqa: N815


class Invitee(BaseModel):
    """A friend who could be invited to a match right now.

    Assembled by the hassault backend from the social roster so the pane never
    has to reach into another module — it only ever calls `/api/hassault`.
    """

    name: str
    person_id: str
    friend_code: str
    """Whether any of their online machines advertised the `hassault` capability.
    False means an older build, and the invite would land nowhere."""
    can_play: bool
    devices_online: int


class MatchInvite(BaseModel):
    """An invitation received from a friend."""

    room: str
    map: str
    host: str  # the inviting node id — authenticated by the fabric
    hostName: str  # noqa: N815 — a label, never used to decide anything
    ts: float


class CreateMatchRequest(BaseModel):
    map: str
    """An explicit id, for handing a friend an invite that resolves to *this*
    match rather than to whatever happens to be open on the map."""
    id: str | None = None
