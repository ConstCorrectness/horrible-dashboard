"""Pydantic models for the `share` module — the API and `/ws` boundary.

The backend is the source of truth for these shapes; the frontend client in
`packages/core/src/modules/share/api.ts` mirrors them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: What a session carries. `semantic` mirrors the workspace as structured state;
#: `pixels` is the encoded video stream (Phase 3); `both` runs them together.
ShareMode = Literal["semantic", "pixels", "both"]

#: The grant ladder. A participant sits at exactly one rung, and a rung answers
#: every capability question below it — see `gate.allows`.
#:
#: A ladder rather than a set of independent flags on purpose: the interesting
#: failure here is granting `terminal` to somebody you meant to give `edit`, and
#: an ordered ladder makes "more than I meant" visible in one glance instead of
#: hiding it in a checkbox grid.
GrantLevel = Literal["view", "cursor", "edit", "terminal", "agent", "control"]

#: Ordered weakest → strongest. `gate.py` is the only thing that should compare
#: these; nothing else may assume the order.
GRANT_LADDER: tuple[GrantLevel, ...] = (
    "view",
    "cursor",
    "edit",
    "terminal",
    "agent",
    "control",
)


class Participant(BaseModel):
    """One person in a session.

    Keyed by **person**, not node, for the same reason the collab room is: you
    invite a human and the fabric picks whichever of their machines is up. The
    `node_id` records which one actually answered, because that is what the wire
    needs and what a revoke has to reach.
    """

    person_id: str
    node_id: str
    name: str
    role: Literal["host", "guest"] = "guest"
    grant: GrantLevel = "view"
    joined_at: float
    #: Whether this guest is mirroring the host's focus or roaming their own copy.
    following: bool = True


class ShareSession(BaseModel):
    """A live session, as seen over the API. Host-side state lives in `session.py`."""

    id: str
    title: str
    mode: ShareMode
    host_node: str
    host_person: str
    created_at: float
    participants: list[Participant] = Field(default_factory=list)
    #: Monotonic; every mutation bumps it so a client can drop a broadcast that
    #: arrived out of order rather than rendering a participant list that jumps
    #: backwards. Same rule the karaoke session follows.
    revision: int = 0
    #: The public relay link, once one has been minted (Phase 4). Empty means the
    #: session is fabric-only — which is the safe default and the Phase 1 state.
    link: str = ""
    #: How many panes the latest projection carries, and how many of those were
    #: withheld. Reported back to the **host**, because the one thing a person
    #: sharing a workspace needs and cannot otherwise get is a straight answer to
    #: "what are they actually seeing?" — a redaction model nobody can audit from
    #: the outside is a redaction model nobody trusts. Null until the host's
    #: browser has published anything.
    mirror_panes: int | None = None
    mirror_hidden: int | None = None


class RemoteSession(BaseModel):
    """A session hosted somewhere else that this node has joined."""

    id: str
    title: str
    host_node: str
    host_name: str
    grant: GrantLevel = "view"
    following: bool = True
    joined_at: float


class ShareInvite(BaseModel):
    """An invitation received from a friend."""

    session_id: str
    title: str
    host: str
    #: A **label**, never an authority — identity is the node id the fabric
    #: authenticated. Same rule `hassault.fabric._invite_display_name` states.
    host_name: str
    host_device: str = ""
    person_id: str = ""
    ts: float
    expires_at: float


class Invitee(BaseModel):
    """A friend who could join a session right now.

    Assembled backend-side from the social roster so the pane only ever calls
    `/api/share` — the same reason `hassault`'s invitee list is built here.
    """

    name: str
    username: str = ""
    person_id: str
    friend_code: str
    #: Whether any of their online machines advertised the `share` capability.
    #: A friend on an older build is listed but not offerable, rather than absent
    #: and unexplained.
    can_share: bool
    devices_online: int


class SessionOut(BaseModel):
    """The whole share surface for this node in one response."""

    #: The session this node is hosting, if any.
    hosting: ShareSession | None = None
    #: Sessions hosted elsewhere that this node has joined.
    joined: list[RemoteSession] = Field(default_factory=list)
    #: Live, unexpired invitations.
    invites: list[ShareInvite] = Field(default_factory=list)


class StartSessionIn(BaseModel):
    title: str = ""
    mode: ShareMode = "semantic"


class InviteIn(BaseModel):
    person_id: str


class GrantIn(BaseModel):
    person_id: str
    grant: GrantLevel


class JoinIn(BaseModel):
    session_id: str
    host_node: str


class ActionResult(BaseModel):
    ok: bool
    error: str | None = None
    detail: dict[str, Any] | None = None


class MintLinkIn(BaseModel):
    """Ask for a public link. Every field is optional; the defaults are the safe ones."""

    #: Seconds. None takes the relay's default, and the relay clamps the ceiling —
    #: a node asking for a year gets a day.
    ttl_s: int | None = None
    #: Optional. A link with no passphrase is watchable by anyone holding the URL,
    #: which is the whole point of a public link and still worth stating plainly.
    passphrase: str = ""


class LinkOut(BaseModel):
    """The host's own view of the public link.

    Served only from `GET`/`POST /api/share/link`, never broadcast: `ingest_url`
    is publish authority, and the session model goes to every guest.
    """

    #: Empty when no link is live.
    view_url: str = ""
    #: Where the host's browser pushes WHIP. Never leaves this node except to the
    #: host's own browser.
    ingest_url: str = ""
    expires_at: float = 0.0
    error: str = ""


class LinkStatusOut(BaseModel):
    """What the relay says about the live link, for the host's own pane.

    `state` carries four values and the pane must render all four:

    - `live`   -- the relay is holding published media for this token.
    - `idle`   -- the token is valid but no picture is arriving.
    - `gone`   -- the relay answered and does not have this token. Everyone
                  holding the URL is looking at a dead page; mint a new one.
    - `unknown`-- we could not ask. **Not** the same as `gone`, and rendering it
                  as either answer is the bug this field exists to prevent.

    `viewers` is the relay's count of connected watchers, which is a different
    number from `StreamState.peers` (fabric guests) and deliberately not merged
    with it.
    """

    state: str = "unknown"
    #: True only for `state == "live"`. Present so the pane can drive a chip
    #: without re-deriving the ladder, never as the whole answer.
    live: bool = False
    viewers: int = 0
    expires_at: float = 0.0
    #: Something the host can act on, or empty when everything is fine.
    detail: str = ""


class RestreamIn(BaseModel):
    """Which configured destination to push to (`twitch`, `youtube`, `custom`)."""

    destination: str


class RestreamOut(BaseModel):
    """Whether the relay is pushing, and where.

    Carries a **label**, never the target URL: that URL embeds the stream key and
    this model is served to the browser.
    """

    live: bool = False
    label: str = ""
    #: Configured destination ids, so the pane can offer only what has a key.
    available: list[str] = Field(default_factory=list)
    error: str = ""
