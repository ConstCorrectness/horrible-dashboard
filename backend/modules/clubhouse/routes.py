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
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException

from backend.modules.clubhouse.models import (
    AcceptSpeakerInviteRequest,
    Channel,
    ChannelList,
    ClubhouseStatus,
    CompleteAuthRequest,
    CreateChannelRequest,
    FollowingList,
    HandRequest,
    InviteUserRequest,
    JoinChannelResult,
    MuteRequest,
    StartAuthRequest,
    StartAuthResult,
    TokenConnectRequest,
    SendChannelMessageRequest,
    HandraiseSettingsRequest,
    UpdateTopicRequest,
    ChatSettingsRequest,
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
    import platform
    import subprocess

    base_dir = Path(__file__).parent / "auth_helper"

    system = platform.system().lower()
    if system == "windows":
        rid = "win-x64"
        bin_name = "ch-auth-helper.exe"
    elif system == "darwin":
        rid = "osx-arm64" if platform.machine() == "arm64" else "osx-x64"
        bin_name = "ch-auth-helper"
    else:
        rid = "linux-x64"
        bin_name = "ch-auth-helper"

    bin_path = base_dir / "bin" / bin_name
    if not bin_path.is_file():
        logger.info("Compiling Clubhouse auth helper for %s...", rid)
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
                    rid,
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
    if action == "start":
        cmd.append("")
    elif extra_arg:
        cmd.append(extra_arg)

    cmd.append(_device_id())

    # Use subprocess.run in a thread instead of asyncio.create_subprocess_exec
    # because uvicorn's event loop on Windows does not support subprocess transports.
    result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
    stdout = result.stdout
    stderr = result.stderr

    logger.info(
        "ch-auth-helper [%s] exit=%d stdout=%s stderr=%s",
        action,
        result.returncode,
        stdout.decode().strip(),
        stderr.decode().strip(),
    )

    if result.returncode != 0:
        err_msg = stderr.decode().strip() or stdout.decode().strip()
        try:
            data = json.loads(stdout.decode())
            if "error" in data:
                err_msg = data["error"]
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"Authentication failed: {err_msg}")

    try:
        data = json.loads(stdout.decode())
        logger.info("ch-auth-helper [%s] parsed response: %s", action, data)
        return data
    except json.JSONDecodeError as err:
        raise HTTPException(
            status_code=500, detail="Invalid response from auth helper"
        ) from err


def _api_base() -> str:
    return os.environ.get("HORRIBLE_CLUBHOUSE_API", "https://www.clubhouseapi.com/api")


def _headers(device_id: str | None = None) -> dict[str, str]:
    # Current Clubhouse Android client version (26.07.07).  Clubhouse rejects
    # stale builds with "login did not pass token validation".  Update when
    # the app publishes a new release.
    _app_version = "26.07.07"
    return {
        "CH-Languages": "en-US",
        "CH-Locale": "en_US",
        "CH-AppBuild": _app_version,
        "CH-AppVersion": _app_version,
        "CH-DeviceId": device_id or _device_id(),
        "User-Agent": f"clubhouse/android/{_app_version}",
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
        status_code = res.status_code if 400 <= res.status_code < 500 else 502
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
        status_code = res.status_code if 400 <= res.status_code < 500 else 502
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
        status = res.status_code if 400 <= res.status_code < 500 else 502
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
    
    channels_list = []
    cursor = None
    seen_channels = set()
    
    # Fetch up to 4 pages to get a good number of live rooms
    for _ in range(4):
        payload = {}
        if cursor:
            payload["cursor"] = cursor
            
        raw = await _ch_authed_post(
            "/get_feed_v3",
            payload,
            auth["auth_token"],
            auth["user_id"],
            auth.get("device_id"),
        )

        items = raw.get("items", []) or []
        for item in items:
            if "channel" in item and item["channel"]:
                ch_raw = item["channel"]
                channel_id = ch_raw.get("channel")
                
                # Prevent duplicates across pages
                if not channel_id or channel_id in seen_channels:
                    continue
                seen_channels.add(channel_id)

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
                        "channel": channel_id,
                        "topic": ch_raw.get("topic"),
                        "num_speakers": ch_raw.get("num_speakers"),
                        "num_all": ch_raw.get("num_all"),
                        "club": club_data,
                        "users": users_list,
                    }
                )
                
        cursor = raw.get("next_cursor") or raw.get("cursor")
        if not cursor:
            break

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

@router.get("/channels/{channel}/chat")
async def get_channel_chat(channel: str) -> dict[str, Any]:
    """Attempt to get recent chat for a channel."""
    auth = _require_auth()
    try:
        res = await _ch_authed_post(
            "/get_channel",
            {"channel": channel},
            auth["auth_token"],
            auth["user_id"],
            auth.get("device_id"),
        )
        return {"comments": res.get("recent_messages", [])}
    except Exception as e:
        print("Failed to fetch chat:", e)
        return {"comments": []}


@router.delete("/auth", response_model=ClubhouseStatus)
def disconnect() -> ClubhouseStatus:
    _auth_path().unlink(missing_ok=True)
    return ClubhouseStatus(connected=False)


@router.get("/channels/{channel}", response_model=Channel)
async def get_channel_details(channel: str) -> dict[str, Any]:
    """Get detailed channel info including all users (Clubhouse POST /get_channel)."""
    auth = _require_auth()
    res = await _ch_authed_post(
        "/get_channel",
        {"channel": channel},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )

    # Map social_club to club
    club_data = None
    if res.get("social_club"):
        club_data = {"name": res["social_club"].get("name")}

    # Map users
    users_list = []
    for u in res.get("users", []) or []:
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

    return {
        "channel": res.get("channel"),
        "topic": res.get("topic"),
        "num_speakers": res.get("num_speakers"),
        "num_all": res.get("num_all"),
        "club": club_data,
        "users": users_list,
    }


@router.get("/users/search")
async def search_users(query: str) -> dict[str, Any]:
    """Search Clubhouse users (Clubhouse POST /search_users)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/search_users",
        {
            "query": query,
            "followers_only": False,
            "following_only": False,
            "cofollows_only": False,
        },
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.get("/users/{user_id}")
async def get_user_profile(user_id: int) -> dict[str, Any]:
    """Get detailed user profile (Clubhouse POST /get_profile)."""
    auth = _require_auth()
    res = await _ch_authed_post(
        "/get_profile",
        {"user_id": user_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )
    return res.get("user_profile") or {}


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
    # Log the full response to help diagnose PubNub issues
    logger.info("join_channel response keys: %s", list(res.keys()))
    logger.info(
        "join_channel pubnub fields: pubnub_enable=%s pubnub_origin=%s pubnub_token_len=%s",
        res.get("pubnub_enable"),
        res.get("pubnub_origin"),
        len(res.get("pubnub_token") or ""),
    )
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
        {"channel": channel, "user_id": auth["user_id"]},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/invite_speaker")
async def invite_speaker(channel: str, body: InviteUserRequest) -> dict[str, Any]:
    """Invite an audience member to speak (Clubhouse POST /invite_speaker)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/invite_speaker",
        {"channel": channel, "user_id": body.user_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels", response_model=JoinChannelResult)
async def create_channel(body: CreateChannelRequest) -> dict[str, Any]:
    """Start a new room (Clubhouse POST /create_channel) and retrieve tokens."""
    auth = _require_auth()

    # Map visibility settings to the new privacy_level field required by Clubhouse API:
    # "public" = Open (Public), "house" = Social / Closed (Private)
    privacy_level_val = "public"
    if body.is_private or body.is_social_mode:
        privacy_level_val = "house"

    res = await _ch_authed_post(
        "/create_channel",
        {
            "topic": body.topic,
            "is_private": body.is_private,
            "is_social_mode": body.is_social_mode,
            "privacy_level": privacy_level_val,
            "club_id": None,
            "user_ids": [],
            "event_id": None,
        },
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )
    res["user_id"] = auth["user_id"]
    return res


@router.post("/users/{user_id}/follow")
async def follow_user(user_id: int) -> dict[str, Any]:
    """Follow a user (Clubhouse POST /follow)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/follow",
        {"user_id": user_id, "source": "feed"},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/users/{user_id}/unfollow")
async def unfollow_user(user_id: int) -> dict[str, Any]:
    """Unfollow a user (Clubhouse POST /unfollow)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/unfollow",
        {"user_id": user_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/invite")
async def invite_user(channel: str, body: InviteUserRequest) -> dict[str, Any]:
    """Invite a user to the active room (Clubhouse POST /invite_to_existing_channel)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/invite_to_existing_channel",
        {"channel": channel, "user_id": body.user_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )

@router.post("/send_channel_message")
async def send_channel_message(body: SendChannelMessageRequest) -> dict[str, Any]:
    """Send a message to the active channel."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/send_channel_message",
        {"channel": body.channel, "message": body.message},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/handraise_settings")
async def change_handraise_settings(channel: str, body: HandraiseSettingsRequest) -> dict[str, Any]:
    """Change the hand-raise policy for the channel."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/change_handraise_settings",
        {
            "channel": channel,
            "is_enabled": body.is_enabled,
            "handraise_permission": body.handraise_permission,
        },
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/topic")
async def update_channel_topic(channel: str, body: UpdateTopicRequest) -> dict[str, Any]:
    """Change the room's title."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/set_channel_title",
        {"channel": channel, "title": body.topic},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/chat_settings")
async def update_chat_settings(channel: str, body: ChatSettingsRequest) -> dict[str, Any]:
    """Enable or disable chat in the room."""
    auth = _require_auth()
    endpoint = "/enable_channel_messages" if body.enable_chat else "/disable_channel_messages"
    return await _ch_authed_post(
        endpoint,
        {"channel": channel},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )
