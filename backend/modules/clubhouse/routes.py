"""Clubhouse account onboarding via the unofficial mobile API.

Clubhouse has no public API; like Clubdeck, we speak the reverse-engineered
mobile client protocol (phone number -> SMS code -> token). The client-version
headers below date from public documentation of that protocol and may need
bumping if Clubhouse rejects them. The auth token is stored server-side only
(``$HORRIBLE_DATA_DIR/clubhouse-auth.json``) and never sent to the frontend.
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from backend.modules.clubhouse.models import (
    AcceptSpeakerInviteRequest,
    ChannelList,
    ClubhouseStatus,
    CompleteAuthRequest,
    FollowingList,
    HandRequest,
    JoinChannelResult,
    MuteRequest,
    StartAuthRequest,
    StartAuthResult,
    TokenConnectRequest,
)
from backend.modules.telemetry.instrument import instrumented_client

router = APIRouter(prefix="/clubhouse", tags=["clubhouse"])
logger = logging.getLogger(__name__)


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


def _get_helper_path() -> Path:
    base_dir = Path(__file__).parent / "auth_helper"
    bin_path = base_dir / "bin" / "ch-auth-helper"
    if not bin_path.is_file():
        import subprocess

        logger.info("Compiling Clubhouse auth helper...")
        base_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [
                    "dotnet",
                    "publish",
                    str(base_dir / "ch-auth-helper.csproj"),
                    "-c",
                    "Release",
                    "-r",
                    "linux-x64",
                    "--self-contained",
                    "false",
                    "-o",
                    str(base_dir / "bin"),
                ],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as err:
            logger.error(f"Failed to compile ch-auth-helper: {err.stderr.decode()}")
            raise RuntimeError(
                f"Failed to compile ch-auth-helper: {err.stderr.decode()}"
            ) from err
    return bin_path


async def _run_helper(
    action: str, phone_number: str, extra_arg: str = ""
) -> dict[str, Any]:
    bin_path = _get_helper_path()
    cmd = [str(bin_path), action, phone_number]
    if extra_arg:
        cmd.append(extra_arg)

    cmd.append(_device_id())

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        err_msg = stderr.decode().strip() or stdout.decode().strip()
        try:
            data = json.loads(stdout.decode())
            if "error" in data:
                err_msg = data["error"]
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"Authentication failed: {err_msg}")

    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError as err:
        raise HTTPException(
            status_code=500, detail="Invalid response from auth helper"
        ) from err


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
    data = await _run_helper("start", body.phone_number)
    if not data.get("success"):
        error_msg = (
            data.get("error_message") or data.get("error") or "verification gate block"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Clubhouse verification failed: {error_msg}",
        )
    return StartAuthResult(success=True)


@router.post("/auth/complete", response_model=ClubhouseStatus)
async def complete_auth(body: CompleteAuthRequest) -> ClubhouseStatus:
    data = await _run_helper("complete", body.phone_number, body.verification_code)
    if not data.get("success") and not data.get("auth_token"):
        error_msg = (
            data.get("error_message") or data.get("error") or "wrong or expired code"
        )
        raise HTTPException(
            status_code=400, detail=f"Verification failed — {error_msg}"
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
    """Live rooms right now (Clubhouse POST /get_feed_v3)."""
    auth = _require_auth()
    raw = await _ch_authed_post(
        "/get_feed_v3",
        {},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )

    channels_list = []
    items = raw.get("items", []) or []
    for item in items:
        if "channel" in item and item["channel"]:
            ch_raw = item["channel"]

            # Map social_club to club
            club_data = None
            if ch_raw.get("social_club"):
                club_data = {"name": ch_raw["social_club"].get("name")}

            # Map users
            users_list = []
            for u in ch_raw.get("users", []):
                users_list.append(
                    {
                        "user_id": u.get("user_id"),
                        "name": u.get("name"),
                        "username": u.get("username"),
                        "photo_url": u.get("photo_url"),
                        "is_speaker": u.get("is_speaker"),
                        "is_moderator": u.get("is_moderator", False),
                    }
                )

            channels_list.append(
                {
                    "channel": ch_raw.get("channel"),
                    "topic": ch_raw.get("topic"),
                    "num_speakers": ch_raw.get("num_speakers"),
                    "num_all": ch_raw.get("num_all"),
                    "club": club_data,
                    "users": users_list,
                }
            )

    return {"channels": channels_list}


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


@router.post("/channels/{channel}/join", response_model=JoinChannelResult)
async def join_channel(channel: str) -> dict[str, Any]:
    """Join a live room (Clubhouse POST /join_channel) and retrieve tokens."""
    auth = _require_auth()
    res = await _ch_authed_post(
        "/join_channel",
        {"channel": channel},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )
    res["user_id"] = auth["user_id"]
    return res


@router.post("/channels/{channel}/leave")
async def leave_channel(channel: str) -> dict[str, Any]:
    """Leave a room (Clubhouse POST /leave_channel)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/leave_channel",
        {"channel": channel},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/ping")
async def active_ping(channel: str) -> dict[str, Any]:
    """Keep the user session active (Clubhouse POST /active_ping)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/active_ping",
        {"channel": channel},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/mute")
async def mute_channel(channel: str, body: MuteRequest) -> dict[str, Any]:
    """Notify Clubhouse of speaker mute/unmute state (Clubhouse POST /update_is_muted)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/update_is_muted",
        {"channel": channel, "is_muted": body.is_muted},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/hand")
async def hand_channel(channel: str, body: HandRequest) -> dict[str, Any]:
    """Raise or lower hand in a room (Clubhouse POST /audience_reply)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/audience_reply",
        {
            "channel": channel,
            "raise_hands": body.raise_hands,
            "unraise_hands": not body.raise_hands,
        },
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/accept_speaker")
async def accept_speaker(
    channel: str, body: AcceptSpeakerInviteRequest
) -> dict[str, Any]:
    """Accept invitation to speak from a moderator (Clubhouse POST /accept_speaker_invite)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/accept_speaker_invite",
        {"channel": channel, "user_id": body.user_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )
