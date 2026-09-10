from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints

# E.164, e.g. +15551234567
PhoneNumber = Annotated[str, StringConstraints(pattern=r"^\+\d{7,15}$")]
VerificationCode = Annotated[str, StringConstraints(pattern=r"^\d{4,8}$")]


class StartAuthRequest(BaseModel):
    phone_number: PhoneNumber


class CompleteAuthRequest(BaseModel):
    phone_number: PhoneNumber
    verification_code: VerificationCode


class TokenConnectRequest(BaseModel):
    """Connect an existing Clubhouse session by its auth token."""

    auth_token: str
    user_id: int
    # Token may be bound to the device it was issued for; pass it through if known.
    device_id: str | None = None


class StartAuthResult(BaseModel):
    success: bool


class ClubhouseStatus(BaseModel):
    """Connection status for the widget. Never includes the auth token."""

    connected: bool
    user_id: int | None = None
    username: str | None = None
    name: str | None = None
    photo_url: str | None = None


# --- Browse models (lean projections of Clubhouse's larger responses) ---


class ChannelUser(BaseModel):
    user_id: int | None = None
    name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    is_speaker: bool | None = None
    is_moderator: bool | None = None


class Club(BaseModel):
    name: str | None = None


class Channel(BaseModel):
    channel: str | None = None
    topic: str | None = None
    num_speakers: int | None = None
    num_all: int | None = None
    club: Club | None = None
    users: list[ChannelUser] = []


class ChannelList(BaseModel):
    channels: list[Channel] = []


class FollowUser(BaseModel):
    user_id: int | None = None
    name: str | None = None
    username: str | None = None
    photo_url: str | None = None


class FollowingList(BaseModel):
    users: list[FollowUser] = []


class JoinChannelResult(BaseModel):
    success: bool
    channel_id: int | None = None
    channel: str | None = None
    token: str | None = None
    rtm_token: str | None = None
    pubnub_token: str | None = None
    pubnub_origin: str | None = None
    pubnub_heartbeat_value: int | None = None
    pubnub_heartbeat_interval: int | None = None
    pubnub_enable: bool | None = None
    agora_native_mute: bool | None = None
    user_id: int | None = None
    # Whether this room takes chat at all, and who may write in it.  Clubhouse
    # decides both, and rejects a write with a bare ``cannot send message`` --
    # so a pane that does not carry these has no way to tell "chat is off in
    # this room" from "the send broke", and shows the same failed toast for both.
    is_chat_enabled: bool | None = None
    is_room_chat_available: bool | None = None
    chat_permission: Any = None
    chat_permission_options: Any = None
    # Per-*user* verdicts for this room, computed by Clubhouse.  Its
    # ``can_post_to_chat`` is the field that answers "may I write here", which
    # the room-level flags above do not: a room can have chat on and still
    # refuse this account.
    user_capabilities: dict[str, Any] | None = None
    # The room's own menus, served by Clubhouse with their wire values and
    # labels.  Carried so the pane renders what this room actually offers
    # instead of a hardcoded list that drifts from it.
    handraise_queue_setting: int | None = None
    handraise_queue_options: list[dict[str, Any]] | None = None
    emoji_reaction_options: list[str] | None = None


class MuteRequest(BaseModel):
    is_muted: bool


class HandRequest(BaseModel):
    raise_hands: bool


class RoomAudience(str, Enum):
    """Who can see a new room: Clubhouse's ``privacy_level`` choices.

    Read off the 26.08.30 room-setup screen.  ``house`` is a fourth value but
    needs a ``social_club_id``, which we have no picker for, and anything else
    (``private``, ``social``) is refused with ``"x" is not a valid choice``.
    """

    public = "public"
    friends = "friend"
    friends_of_friends = "friend_of_friend"


class CreateChannelRequest(BaseModel):
    topic: str = ""
    audience: RoomAudience = RoomAudience.public


class InviteUserRequest(BaseModel):
    user_id: int


class SendChannelMessageRequest(BaseModel):
    channel: str
    message: str


class HandraisePermission(str, Enum):
    """Who may raise a hand, named rather than numbered.

    Clubhouse's ``handraise_queue_setting`` is a bare int whose values differ
    from the ones the retired ``update_is_ask_to_join_allowed`` took (that one
    used 1 for "everyone"; here 1 means LOCKED).  Passing the old number
    straight through would lock the room while the UI said "everyone" — no
    error, just the opposite policy — so the wire int is only ever produced by
    the mapping in ``routes._HANDRAISE_QUEUE_SETTING``.
    """

    open_mic = "open_mic"
    locked = "locked"
    everyone = "everyone"
    followed_by_speakers = "followed_by_speakers"


class HandraiseSettingsRequest(BaseModel):
    is_enabled: bool
    handraise_permission: HandraisePermission = HandraisePermission.everyone


class UpdateTopicRequest(BaseModel):
    topic: str


class ChatSettingsRequest(BaseModel):
    enable_chat: bool


class ChatPermission(str, Enum):
    """Who may write in room chat, named for the same reason as
    HandraisePermission: the wire value is a bare int (``routes._CHAT_PERMISSION``).
    """

    everyone = "everyone"
    host_followers = "host_followers"
    trusted_followers = "trusted_followers"


class ChatPermissionRequest(BaseModel):
    chat_permission: ChatPermission


class ReactionRequest(BaseModel):
    emoji: str


class UninviteSpeakerRequest(BaseModel):
    user_id: int


class MakeModeratorRequest(BaseModel):
    user_id: int


class BlockChannelUserRequest(BaseModel):
    user_id: int


class RejectSpeakerInviteRequest(BaseModel):
    user_id: int


class NotificationItem(BaseModel):
    notification_id: int | None = None
    message: str | None = None
    time_created: str | None = None
    type: int | None = None
    user_profile: ChannelUser | None = None
    channel: str | None = None


class NotificationsList(BaseModel):
    notifications: list[NotificationItem] = []


class UpdateBioRequest(BaseModel):
    bio: str


class UpdateNameRequest(BaseModel):
    name: str


class UpdateUsernameRequest(BaseModel):
    username: str
