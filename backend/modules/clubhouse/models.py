from typing import Annotated

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


class MuteRequest(BaseModel):
    is_muted: bool


class HandRequest(BaseModel):
    raise_hands: bool


class AcceptSpeakerInviteRequest(BaseModel):
    user_id: int


class CreateChannelRequest(BaseModel):
    topic: str = ""
    is_private: bool = False
    is_social_mode: bool = False


class InviteUserRequest(BaseModel):
    user_id: int


class SendChannelMessageRequest(BaseModel):
    channel: str
    message: str

