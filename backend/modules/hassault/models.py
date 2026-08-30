"""Pydantic models for the HorribleAssault map API."""

from __future__ import annotations

from typing import Any, Literal

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
    """Who this node plays as. The pane's gate: no username, no match.

    `username` is the game server's globally unique handle — the account is shared
    with the games ladder, so there is one sign-in for the whole app rather than a
    second one for this game. `enlisted` is derived from holding a username, not
    stored, so the two can never disagree.
    """

    signed_in: bool
    account_id: str | None = None
    display_name: str | None = None
    username: str | None = None
    #: What to pre-fill the chooser with when there is no username yet — the
    #: provider login folded into the handle charset. A suggestion, not a
    #: reservation: it is not held for anyone and two people can be offered the
    #: same one.
    suggested_username: str = ""
    enlisted: bool = False


class EntityOut(BaseModel):
    type: int
    name: str
    x: int
    y: int
    z: int
    yaw: float | None = None
    attrs: list[int] = Field(default_factory=list)


class ItemReach(BaseModel):
    """How close a body has to get. Served for the `interval`/`zoomLevels` reason:
    Train resolves its own pickups locally, and a second copy of these numbers
    would be a range where items come off the floor at a different distance than
    they do in a match."""

    radius: float
    below: float
    above: float


class ItemsResponse(BaseModel):
    """The item table and the one geometric rule that goes with it."""

    reach: ItemReach
    kinds: list[ItemOut]


class ItemPlacement(BaseModel):
    """One item, where it actually rests. See `pickups.place`."""

    id: int
    kind: str
    x: float
    y: float
    z: float


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
    #: The map's items, already **resolved onto the floor** by `pickups.place`.
    #:
    #: Served rather than derived in the browser from `entities` above, for the
    #: same reason `plane_order` is served: resolving an item's height is a real
    #: rule (an entity's `z` is the mapper's eye, not the ground), and a second
    #: implementation of it in TypeScript would be a Train mode whose items sit
    #: somewhere a match's do not.
    items: list[ItemPlacement] = Field(default_factory=list)


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
    #: Their `@username`, when the roster has resolved one.
    #:
    #: Empty is a real answer, not a bug: a friend added by friend code before
    #: either of you signed in to the game server has no account bound to their
    #: person key yet, and inventing one would be worse than showing the display
    #: name. It is served alongside `name` rather than replacing it for that
    #: reason — the UI prefers this and falls back.
    username: str = ""
    person_id: str = ""
    friend_code: str = ""
    """Where they are, when we can tell: a room id on this node, else empty."""
    room: str = ""
    """The map of that room, so a roster row can say what they are playing rather
    than only that they are busy."""
    room_map: str = ""
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


class ItemOut(BaseModel):
    """One item kind's numbers, served for the same reason `WeaponOut` is.

    The pane draws the label and — the part that actually needs this — a respawn
    countdown on an item it watched somebody take. A copy of `respawn` in
    TypeScript would be a timer that says the armour is back before it is.
    """

    kind: str
    name: str
    """Seconds from being taken to being available again."""
    respawn: float
    health: float = 0.0
    armour: float = 0.0
    armourCap: float = 0.0  # noqa: N815 — read verbatim by the browser
    """Reserve rounds added per weapon, as a multiple of that weapon's magazine.
    A multiple rather than a count because a shotgun magazine and a rifle
    magazine are not the same amount of gun."""
    mags: float = 0.0
    """Grenade id topped up, or `None` for every item that gives none."""
    nade: str | None = None


class Invitee(BaseModel):
    """A friend who could be invited to a match right now.

    Assembled by the hassault backend from the social roster so the pane never
    has to reach into another module — it only ever calls `/api/hassault`.
    """

    name: str
    #: Their `@username` — see `BrowsePlayer.username` for why it can be empty.
    username: str = ""
    person_id: str
    friend_code: str
    """Whether any of their online machines advertised the `hassault` capability.
    False means an older build, and the invite would land nowhere."""
    can_play: bool
    devices_online: int
    """A room on this node they are standing in, and its map. Presence beyond
    online/offline: "playing hd_crossing" is what makes a roster worth opening."""
    room: str = ""
    room_map: str = ""


class MatchInvite(BaseModel):
    """An invitation received from a friend."""

    room: str
    map: str
    host: str  # the inviting node id — authenticated by the fabric
    #: Who invited you, as `@username` — a **person**. This used to be the sender's
    #: node name, so an invite read "horribleComputer invited you": it is assembled
    #: on the fabric side, where only the device label is in scope, and the account
    #: username was never joined in. Resolved roster-first in
    #: `fabric._invite_display_name`. Still a label, never used to decide anything.
    hostName: str  # noqa: N815
    #: Which of their machines it came from — secondary, since an invite fans out
    #: to every device a person has online.
    hostDevice: str = ""  # noqa: N815
    #: The inviting person, when the roster knows them. Carried so a per-person
    #: mute can apply to their invites.
    personId: str = ""  # noqa: N815
    ts: float
    #: When it stops being joinable. A room does not outlive the process hosting
    #: it, so an invite has a shelf life.
    expiresAt: float = 0.0  # noqa: N815


class CreateMatchRequest(BaseModel):
    map: str
    """An explicit id, for handing a friend an invite that resolves to *this*
    match rather than to whatever happens to be open on the map."""
    id: str | None = None


class TacticalOut(BaseModel):
    """One thrown grenade's numbers, served rather than duplicated in TypeScript.

    The `interval` / `zoom_levels` / `plane_order` precedent: the HUD shows the
    carry count and the name, and the renderer draws a cloud at `radius` — a
    second copy of that number is a smoke drawn a different size from the one
    actually blocking sight.

    Note every field here has to be **added to this model as well as returned by
    `to_dict`**: a Pydantic response model silently drops what it does not
    declare, so a new field reaches the browser as `undefined` with nothing
    saying why.
    """

    id: str
    name: str
    type: str
    fuseTime: float
    impact: bool = False
    radius: float
    duration: float
    maxDamage: float
    damagePerSecond: float = 0.0
    bounceDamping: float = 0.55
    carried: int = 1


class LaunchNativeRequest(BaseModel):
    """What to launch the native client into.

    The node's own address is deliberately **not** a field: the route reads it off
    the request, which is the one address known to be correct. `HORRIBLE_DEV_BACKEND_PORT`
    moves the port, Windows' reserved ranges force that regularly, and a browser
    that guessed would be guessing about the machine it is already talking to.
    """

    #: What the player pressed. `train` is **not a match**: the client stays off
    #: the socket entirely, exactly as the browser's Train does, because a mode
    #: that quietly opened a room would put a learner in a stranger's firefight —
    #: `match_server.join` with no room id is join-*or*-create, so "alone on this
    #: map" is not something the wire can ask for. `host` opens (or joins) a match
    #: here and fills it with `bots`; `join` enters one that already exists.
    mode: Literal["train", "host", "join", "ranked"] = "join"
    #: A specific room; empty means "any match on this map, or open one".
    room_id: str = ""
    map_name: str
    #: A friend's node id, when the room is on their machine.
    host: str = ""
    #: Bots to field, `host` only — the client sends `add_bot` once its welcome
    #: lands, because a bot needs a room to be added to and the room is only ours
    #: at that point. Clamped to the match server's own ceiling rather than
    #: trusted: it arrives from a browser.
    bots: int = 0
    bot_skill: str = "normal"
    #: A wire label only — the backend plays you as your account's username.
    username: str | None = None
    fullscreen: bool = False
    #: Accepted for compatibility with the old request shape and otherwise unused:
    #: raw input is not an option the client offers, it is how `winit` reads a
    #: mouse (B2). Kept rather than removed so an older browser build's request
    #: still validates.
    raw_input: bool = True
    max_fps: int = 240


class LaunchNativeResponse(BaseModel):
    """The launch, which is a **job** and not a request — see `_LAUNCH_JOBS`.

    `phase` is what makes that visible. A launch that has to compile the client
    first takes minutes, and the response that used to be the only signal simply
    did not arrive for all of them; the pane read that as a hang, and switching
    tabs (which unmounts it) lost even the promise it was waiting on. So the POST
    answers with a phase either way, and `GET /launch_native/status` hands the
    same shape back to a pane that has just been remounted.
    """

    #: `starting`, `building`, `launched`, `failed`, or `idle` — the last only
    #: from the status route, and meaning "nothing has been launched", which is a
    #: different fact from a launch that failed.
    phase: str = ""
    launched: bool
    pid: int | None = None
    connect_args: list[str] = []
    message: str | None = None
    #: Whether the route compiled the client before starting it, and how long
    #: that took. Served rather than inferred from the message text, because the
    #: pane greys its buttons for the duration and a build is the one launch that
    #: takes minutes.
    rebuilt: bool = False
    build_seconds: float | None = None
    #: The built client was older than its own source and was started anyway —
    #: only reachable with `hassault.autoBuildNative` off. The pane says so in
    #: place of a launch message, because this is the exact failure that reads as
    #: "my change did not work" rather than as "an old build ran".
    stale: bool = False


class ClientInstallRequest(BaseModel):
    """Which client build to download. Empty means this node's own version."""

    version: str = ""


class ClientRemoveRequest(BaseModel):
    version: str = ""


class NativeClientStatus(BaseModel):
    """Where the native client would come from if it were launched right now.

    `source` is the whole point of this route: three tiers resolve the binary and
    which one won is invisible from the outside, so a developer whose `target/`
    build is being used and a player running a download look identical until
    something behaves differently.
    """

    #: `setting`, `build`, `download`, or `none`.
    source: str
    binary: str | None = None
    #: This node's version — the release a download is taken from.
    version: str
    #: A client is installed under the data dir for this version.
    installed: bool = False
    #: Whether GitHub published a digest for the asset that was installed. False on
    #: an install that could not be verified, and meaningless when `installed` is
    #: False — the same honesty `binaries.Install` carries.
    verified: bool = False
    installed_size_bytes: int | None = None
    #: A checkout is present, so building is an option and a download is a
    #: convenience rather than the only route.
    has_crate: bool = False


class HitboxOut(BaseModel):
    """The body a shot is resolved against, served so no client holds a copy.

    Every field is spelled out because a `response_model` *filters*: a dimension
    added to `HitboxSpec` and forgotten here would be silently dropped on the way
    out, and the client would fall back to whatever it did before — which for a
    hitbox means two implementations quietly disagreeing about where a body is.

    The derived heights are served alongside the primitives rather than left to
    each client, because three implementations of `crouchHeight` is three chances
    to round it differently.
    """

    #: Content hash of the hit-deciding dimensions. The client shows it in the
    #: tuning lab and `physics-vectors.json` is stamped with it.
    specId: str  # noqa: N815 — read verbatim by the browser and the native client
    shape: str
    radius: float
    eyeHeight: float  # noqa: N815
    aboveEye: float  # noqa: N815
    standingHeight: float  # noqa: N815
    crouchEyeScale: float  # noqa: N815
    crouchEyeHeight: float  # noqa: N815
    crouchHeight: float  # noqa: N815
    crouchScale: float  # noqa: N815
    headBand: float  # noqa: N815
    fitTolerance: float  # noqa: N815
    eyeTolerance: float  # noqa: N815
    #: Whether a tuning override is in force. The lab needs to distinguish "this is
    #: the shipped body" from "this is what somebody dragged a slider to", because
    #: only one of those is worth keeping.
    overridden: bool = False


class HitboxTuneRequest(BaseModel):
    """A tuning change. Every dimension is nullable and resolved with `is None`,
    never falsiness — `0` is a meaningful head band (no headshots at all) and a
    slider dragged to zero must not read as "leave it alone"."""

    radius: float | None = None
    eyeHeight: float | None = None  # noqa: N815
    aboveEye: float | None = None  # noqa: N815
    crouchEyeScale: float | None = None  # noqa: N815
    headBand: float | None = None  # noqa: N815
    fitTolerance: float | None = None  # noqa: N815
    eyeTolerance: float | None = None  # noqa: N815
    #: Discard the override and go back to the shipped body.
    reset: bool = False


class FactionOut(BaseModel):
    id: str
    name: str
    short: str
    motto: str
    blurb: str
    primary: str
    secondary: str
    insignia: str
    callsigns: list[str]


class MapBriefOut(BaseModel):
    mapName: str  # noqa: N815
    site: str
    tagline: str
    brief: str


class LoreOut(BaseModel):
    """The setting. Served for the same reason the weapon table is — the menu, the
    loading screen, the avatar tint and the scoreboard all need it, and four copies
    of a faction colour is four places for it to drift."""

    premise: str
    longPremise: str  # noqa: N815
    factions: list[FactionOut]
    #: Team index → faction id. The index itself comes from the map's spawns.
    teamFactions: list[str]  # noqa: N815
    #: Ladder tier id → display name. Keyed by the game server's `TIERS`.
    ranks: dict[str, str]
    #: Keyed by map name, and only for the maps this repo ships.
    mapBriefs: dict[str, MapBriefOut]  # noqa: N815


# ---- the map designer -------------------------------------------------------------


class LintFinding(BaseModel):
    """One thing wrong with a map, and where.

    `cells` is the field that matters: it is what lets a client paint the failure
    onto the floor it happens on, which is the whole reason a live validator beats
    a test run. "37 cells are cut off" is a number; the same thing drawn in red is
    an answer. It is capped, so `cellCount` is the real total.
    """

    code: str
    severity: Literal["error", "warn"]
    message: str
    cells: list[list[int]] = Field(default_factory=list)
    cellCount: int = 0
    entity: int | None = None


class DraftInfo(BaseModel):
    """A map being edited.

    `mapName` is the draft addressed as a map — pass it to `GET /maps/{name}` and
    `/maps/{name}/cubes` and they serve this document. That is the whole designer
    read path; there are no designer-specific read routes because there did not
    need to be any.
    """

    id: str
    #: The map this was seeded from, and where `save` puts it by default.
    name: str
    mapName: str
    doc: dict[str, Any]
    #: Bumped by every accepted edit. A client re-fetches cubes when it changes.
    revision: int
    canUndo: bool = False
    canRedo: bool = False
    lint: list[LintFinding] = Field(default_factory=list)


class DraftCreateRequest(BaseModel):
    #: A bundled map to seed from. Absent means a blank document.
    from_map: str | None = Field(default=None, alias="from")

    model_config = {"populate_by_name": True}


class MapEdit(BaseModel):
    """One edit, which is also its own undo record.

    Deliberately not a whole-document write. A document write cannot be inverted,
    so an editor built on one has to grow a second, separate history — and the two
    disagree the first time an edit does something the history did not model. Here
    the inverse of an edit is another edit of the same eight kinds, so undo replays
    through the code that made the change rather than through a mirror of it.
    """

    op: Literal[
        "brush.add",
        "brush.remove",
        "brush.update",
        "brush.replace",
        "brush.reorder",
        "ent.add",
        "ent.remove",
        "ent.update",
        "ent.replace",
        "map.set",
    ]
    #: Which brush or entity, for everything but `brush.reorder` and `map.set`.
    index: int | None = None
    brush: dict[str, Any] | None = None
    entity: dict[str, Any] | None = None
    #: For `.update`: fields to merge. An explicit null clears one.
    patch: dict[str, Any] | None = None
    #: For `brush.reorder`.
    from_index: int | None = Field(default=None, alias="from")
    to_index: int | None = Field(default=None, alias="to")
    #: For `map.set`.
    key: str | None = None
    value: Any = None

    model_config = {"populate_by_name": True}


class DraftSaveRequest(BaseModel):
    #: Defaults to the map the draft was opened from. An `hd_` prefix is added
    #: if it is missing — every map this project ships carries one.
    name: str | None = None
    overwrite: bool = False


class DraftSaveResponse(BaseModel):
    name: str
    path: str
    lint: list[LintFinding] = Field(default_factory=list)


class FieldSpec(BaseModel):
    """One editable field on a brush or an entity.

    Served rather than written out twice, the `plane_order` / `zoomLevels`
    precedent — and for the reason the Model Designer's inspector gives: a form
    hand-maintained beside a schema is a form that eventually describes a field
    the backend no longer has.
    """

    name: str
    type: Literal["int", "number", "string", "bool", "texture", "color", "enum", "rect"]
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    choices: list[str] | None = None
    required: bool = False
    description: str = ""


class OpSpec(BaseModel):
    name: str
    description: str = ""
    fields: list[FieldSpec] = Field(default_factory=list)


class MapSchema(BaseModel):
    """What a map document may contain: three brush ops, the entity types, and the
    document's own fields."""

    brushes: list[OpSpec]
    entities: list[OpSpec]
    map_fields: list[FieldSpec] = Field(default_factory=list)
    #: The whole entity vocabulary, by on-disk type index. Ones with no `OpSpec`
    #: are placeable only through raw `attrs`.
    entity_names: list[str] = Field(default_factory=list)


class TextureOut(BaseModel):
    """One slot in the texture palette.

    A slot is a bare integer in the map format, and AssaultCube resolves it
    through map `.cfg` files this project does not read — pointing at content it
    could not ship anyway. So the palette is ours: named, procedural, and served
    so that neither client holds a second copy of the table.
    """

    id: int
    name: str
    group: str
    #: `#rrggbb`. The colour both renderers already tint this slot with, so a
    #: slot with no catalogue entry keeps exactly the look it has today.
    color: str
    #: Which procedural generator draws it. `flat` is no pattern at all.
    pattern: Literal["flat", "grid", "brick", "plate", "concrete", "grate", "panel"]
    roughness: float = 0.8
