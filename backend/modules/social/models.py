"""Pydantic models for the social layer.

Like the network module's models, these straddle two boundaries: the **peer wire**
(friend requests travelling between nodes, carried inside a `PeerEnvelope.data`)
and the **REST/`/ws` surface** between a node and its own browser.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.modules.social.identity import PersonId

# A friendship as this node sees it. Both sides keep their own row, so the states
# are deliberately directional: `pending_out` on one node pairs with `pending_in`
# on the other until one of them accepts.
FriendStatus = Literal["pending_out", "pending_in", "accepted", "blocked"]

# Presence is derived, never stored: a person is online when at least one of their
# devices has a live session on the fabric.
PresenceState = Literal["online", "offline"]

#: What you can set yourself to (see `status.py`).
Persona = Literal["online", "away", "busy", "invisible"]
#: What a friend can see. `invisible` is deliberately absent: it arrives as `offline`.
SharedPersona = Literal["online", "away", "busy", "offline"]


class Activity(BaseModel):
    """What someone is doing right now. `kind == "game"` puts them in In-Game."""

    kind: Literal["game", "app"] = "game"
    title: str
    detail: str | None = None
    #: Epoch seconds, so a friend can show "for 12 minutes".
    since: float


class DeviceInfo(BaseModel):
    """One machine belonging to a person."""

    node_id: str
    person_id: PersonId
    node_public_key: str
    label: str
    online: bool = False
    last_seen: float | None = None
    last_address: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class Friend(BaseModel):
    """A person in this node's roster, with their known devices."""

    person_id: PersonId
    display_name: str
    friend_code: str
    person_public_key: str
    status: FriendStatus
    note: str | None = None
    added_at: float
    presence: PresenceState = "offline"
    #: What they have set themselves to, as they chose to share it. `offline`
    #: whenever `presence` is — an invisible friend is indistinguishable from one
    #: whose machines are down, which is the point of invisible.
    persona: SharedPersona = "offline"
    activity: Activity | None = None
    devices: list[DeviceInfo] = Field(default_factory=list)
    # True when this person is *you* — your own linked machines show up in the
    # roster so "play against my other computer" needs no special case.
    is_self: bool = False
    #: This person's game-server identity, cached locally from the directory.
    #:
    #: None means "we have not linked them to a ladder account", which is a normal
    #: state and not a failure — a friend who has never signed in to the game server
    #: has no username to show. The roster renders them by display name and friend
    #: code, exactly as before. What `handle` unlocks is the *profile*: with it the
    #: UI can fetch their avatar, level and comment wall.
    handle: str | None = None
    account_id: str | None = None


class SelfProfile(BaseModel):
    """This user's own identity, as shown in the Friends panel header."""

    person_id: PersonId
    friend_code: str
    display_name: str
    person_public_key: str
    # False on a machine linked by another device: it can act as this person but
    # cannot mint device certificates or accept new link requests.
    holds_person_key: bool = True
    #: The game-server username this machine is signed in as, when it is. The one
    #: name a person has everywhere — the ladder, HorribleAssault, and the roster.
    #: None when signed out, which is why the friend code stays first-class.
    handle: str | None = None
    #: Your *chosen* persona, `invisible` included — this is the one place it shows.
    persona: Persona = "online"
    #: True while the browser reports nobody at the keyboard; friends then see
    #: `away` without the choice above being overwritten.
    idle: bool = False
    activity: Activity | None = None
    devices: list[DeviceInfo] = Field(default_factory=list)


class RosterSnapshot(BaseModel):
    self_profile: SelfProfile
    friends: list[Friend] = Field(default_factory=list)


# ---- REST request/response shapes -------------------------------------------------


class DirectoryEntry(BaseModel):
    """A public directory hit — what `@handle` search and resolve return."""

    handle: str
    display_name: str
    person_id: PersonId
    person_public_key: str


class DirectorySearchResult(BaseModel):
    results: list[DirectoryEntry] = Field(default_factory=list)
    #: Shortest query the directory will answer, so the UI can say so up front
    #: rather than looking broken on a two-character search.
    min_prefix: int = 3
    error: str | None = None


class BindHandleResult(BaseModel):
    ok: bool = False
    handle: str | None = None
    error: str | None = None


class AddFriendRequest(BaseModel):
    """Add by username (`@rob`), friend code, or a raw person id. `address`
    short-circuits discovery when you already know how to reach them — a LAN box,
    say."""

    code: str
    address: str | None = None
    note: str | None = None


class AddFriendResult(BaseModel):
    ok: bool
    friend: Friend | None = None
    error: str | None = None


class RespondRequest(BaseModel):
    person_id: PersonId
    accept: bool


class StatusRequest(BaseModel):
    """Set any of these; omitted fields are left alone. `clear_activity` exists
    because `activity: null` cannot be told apart from "not sent"."""

    persona: Persona | None = None
    activity: dict[str, Any] | None = None
    clear_activity: bool = False
    idle: bool | None = None


class UpdateProfileRequest(BaseModel):
    display_name: str | None = None
    avatar: str | None = None


class DeviceCertPayload(BaseModel):
    """A device certificate as it crosses the wire (see identity.verify_device_cert)."""

    person_id: PersonId
    person_public_key: str
    node_id: str
    node_public_key: str
    label: str
    issued_at: float
    sig: str

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()
