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
