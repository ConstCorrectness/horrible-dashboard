"""Clubhouse account onboarding via the unofficial mobile API.

Clubhouse has no public API; like Clubdeck, we speak the reverse-engineered
mobile client protocol (phone number -> SMS code -> token). The client-version
headers below date from public documentation of that protocol and may need
bumping if Clubhouse rejects them. The auth token is stored server-side only
(``$HORRIBLE_DATA_DIR/clubhouse-auth.json``) and never sent to the frontend.
"""

import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from backend.modules.clubhouse.models import (
    ChannelList,
    ClubhouseStatus,
    CompleteAuthRequest,
    FollowingList,
    StartAuthRequest,
    StartAuthResult,
    TokenConnectRequest,
)
from backend.modules.telemetry.instrument import instrumented_client

router = APIRouter(prefix="/clubhouse", tags=["clubhouse"])


def _data_dir() -> Path:
    return Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))


def _auth_path() -> Path:
    return _data_dir() / "clubhouse-auth.json"


def _device_id() -> str:
    """Stable per-install device id, as the mobile client would have."""
    path = _data_dir() / "clubhouse-device-id"
    if path.is_file():
        return path.read_text().strip()
    device_id = str(uuid.uuid4()).upper()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(device_id)
    return device_id


def _api_base() -> str:
    return os.environ.get("HORRIBLE_CLUBHOUSE_API", "https://www.clubhouseapi.com/api")


def _headers(device_id: str | None = None) -> dict[str, str]:
    # Current client values (build 3375 / app 24.01.02). Clubhouse rejects stale
    # builds with "login did not pass token validation".
    return {
        "CH-Languages": "en-US",
        "CH-Locale": "en_US",
        "CH-AppBuild": "3375",
        "CH-AppVersion": "24.01.02",
        "CH-DeviceId": device_id or _device_id(),
        "User-Agent": "clubhouse/3375 (iPhone; iOS 17.1.2; Scale/3.00)",
    }


def _auth_headers(
    token: str, user_id: int, device_id: str | None = None
) -> dict[str, str]:
    return {
        **_headers(device_id),
        "Authorization": f"Token {token}",
        "CH-UserID": str(user_id),
    }


async def _ch_authed_post(
    path: str,
    payload: dict[str, Any],
    token: str,
    user_id: int,
    device_id: str | None = None,
) -> dict[str, Any]:
    """POST to an authenticated Clubhouse endpoint; tests monkeypatch this seam."""
    try:
        async with instrumented_client(timeout=15) as client:
            res = await client.post(
                f"{_api_base()}{path}",
                json=payload,
                headers=_auth_headers(token, user_id, device_id),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Clubhouse unreachable: {exc}"
        ) from exc
    if res.status_code >= 400:
        message = res.text[:300]
        try:
            message = res.json().get("error_message") or message
        except (ValueError, AttributeError):
            pass
        status_code = (
            res.status_code if res.status_code in (400, 401, 403, 429) else 502
        )
        raise HTTPException(status_code=status_code, detail=f"Clubhouse: {message}")
    return res.json()


async def _ch_authed_get(
    path: str,
    token: str,
    user_id: int,
    device_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET an authenticated Clubhouse endpoint; tests monkeypatch this seam."""
    try:
        async with instrumented_client(timeout=15) as client:
            res = await client.get(
                f"{_api_base()}{path}",
                headers=_auth_headers(token, user_id, device_id),
                params=params,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Clubhouse unreachable: {exc}"
        ) from exc
    if res.status_code >= 400:
        message = res.text[:300]
        try:
            message = res.json().get("error_message") or message
        except (ValueError, AttributeError):
            pass
        status_code = (
            res.status_code if res.status_code in (400, 401, 403, 429) else 502
        )
        raise HTTPException(status_code=status_code, detail=f"Clubhouse: {message}")
    return res.json()


def _require_auth() -> dict[str, Any]:
    """Load the stored session or 409 if the account isn't connected."""
    path = _auth_path()
    if not path.is_file():
        raise HTTPException(status_code=409, detail="Clubhouse not connected")
    return json.loads(path.read_text())


async def _ch_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the Clubhouse API; tests monkeypatch this seam."""
    try:
        async with instrumented_client(timeout=15) as client:
            res = await client.post(
                f"{_api_base()}{path}", json=payload, headers=_headers()
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Clubhouse unreachable: {exc}"
        ) from exc
    if res.status_code >= 400:
        message = res.text[:300]
        try:
            message = res.json().get("error_message") or message
        except (ValueError, AttributeError):
            pass
        status = res.status_code if res.status_code in (400, 401, 403, 429) else 502
        raise HTTPException(status_code=status, detail=f"Clubhouse: {message}")
    return res.json()


@router.get("/status", response_model=ClubhouseStatus)
def status() -> ClubhouseStatus:
    path = _auth_path()
    if not path.is_file():
        return ClubhouseStatus(connected=False)
    saved = json.loads(path.read_text())
    return ClubhouseStatus(
        connected=True,
        user_id=saved.get("user_id"),
        username=saved.get("username"),
        name=saved.get("name"),
        photo_url=saved.get("photo_url"),
    )


@router.post("/auth/start", response_model=StartAuthResult)
async def start_auth(body: StartAuthRequest) -> StartAuthResult:
    data = await _ch_post(
        "/start_phone_number_auth", {"phone_number": body.phone_number}
    )
    return StartAuthResult(success=bool(data.get("success", False)))


@router.post("/auth/complete", response_model=ClubhouseStatus)
async def complete_auth(body: CompleteAuthRequest) -> ClubhouseStatus:
    data = await _ch_post(
        "/complete_phone_number_auth",
        {
            "phone_number": body.phone_number,
            "verification_code": body.verification_code,
        },
    )
    token = data.get("auth_token")
    if not token:
        raise HTTPException(
            status_code=400, detail="Verification failed — wrong or expired code"
        )

    profile = data.get("user_profile") or {}
    record = {
        "auth_token": token,
        "refresh_token": data.get("refresh_token"),
        "user_id": profile.get("user_id"),
        "username": profile.get("username"),
        "name": profile.get("name"),
        "photo_url": profile.get("photo_url"),
    }
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return status()


@router.post("/auth/token", response_model=ClubhouseStatus)
async def connect_with_token(body: TokenConnectRequest) -> ClubhouseStatus:
    """Connect using an existing auth token, validated against /me."""
    data = await _ch_authed_post(
        "/me", {}, body.auth_token, body.user_id, body.device_id
    )
    profile = data.get("user_profile") or {}
    record = {
        "auth_token": body.auth_token,
        "device_id": body.device_id,
        "user_id": profile.get("user_id") or body.user_id,
        "username": profile.get("username"),
        "name": profile.get("name"),
        "photo_url": profile.get("photo_url"),
    }
    path = _auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return status()


@router.get("/channels", response_model=ChannelList)
async def channels() -> dict[str, Any]:
    """Live rooms right now (Clubhouse GET /get_channels)."""
    auth = _require_auth()
    return await _ch_authed_get(
        "/get_channels", auth["auth_token"], auth["user_id"], auth.get("device_id")
    )


@router.get("/following", response_model=FollowingList)
async def following() -> dict[str, Any]:
    """People the connected account follows (Clubhouse POST /get_following)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/get_following",
        {"user_id": auth["user_id"], "page_size": 50, "page": 1},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.delete("/auth", response_model=ClubhouseStatus)
def disconnect() -> ClubhouseStatus:
    _auth_path().unlink(missing_ok=True)
    return ClubhouseStatus(connected=False)
