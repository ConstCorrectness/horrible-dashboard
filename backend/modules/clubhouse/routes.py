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
    BlockChannelUserRequest,
    Channel,
    ChannelList,
    ClubDetails,
    ClubMemberList,
    ClubhouseEvent,
    ClubhouseStatus,
    CompleteAuthRequest,
    CreateChannelRequest,
    CreateEventRequest,
    EventList,
    FollowingList,
    HandRequest,
    HandraiseSettingsRequest,
    InviteUserRequest,
    JoinChannelResult,
    MakeModeratorRequest,
    MuteRequest,
    NotificationItem,
    NotificationsList,
    OnlineFriendsList,
    RejectSpeakerInviteRequest,
    SendChannelMessageRequest,
    StartAuthRequest,
    StartAuthResult,
    TokenConnectRequest,
    UninviteSpeakerRequest,
    UpdateBioRequest,
    UpdateNameRequest,
    UpdateSkintoneRequest,
    UpdateTopicRequest,
    UpdateUsernameRequest,
    ChatSettingsRequest,
)
from backend.modules.telemetry.instrument import instrumented_client
from backend import jsonstore, paths

router = APIRouter(prefix="/clubhouse", tags=["clubhouse"])
logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    return paths.data_dir()


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
        logger.warning("Clubhouse API error for %s: %s %s", path, status_code, message)
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
    jsonstore.write_text(_auth_path(), json.dumps(record))
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
    jsonstore.write_text(_auth_path(), json.dumps(record))
    return status()


def _parse_channel_data(ch_raw: dict[str, Any]) -> dict[str, Any] | None:
    """Safely extracts normalized room structure from a raw channel dict."""
    if not isinstance(ch_raw, dict):
        return None
    channel_id = ch_raw.get("channel") or ch_raw.get("channel_id")
    if not channel_id:
        return None

    # Map club
    club_data = None
    if ch_raw.get("social_club") and isinstance(ch_raw["social_club"], dict):
        club_data = {"name": ch_raw["social_club"].get("name")}
    elif ch_raw.get("club") and isinstance(ch_raw["club"], dict):
        club_data = {"name": ch_raw["club"].get("name")}

    # Map users
    users_list = []
    for u in ch_raw.get("users", []) or []:
        if isinstance(u, dict):
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
        "channel": str(channel_id),
        "topic": ch_raw.get("topic"),
        "num_speakers": ch_raw.get("num_speakers") or len([u for u in users_list if u.get("is_speaker")]),
        "num_all": ch_raw.get("num_all") or len(users_list),
        "club": club_data,
        "users": users_list,
    }


@router.get("/channels", response_model=ChannelList)
async def channels() -> dict[str, Any]:
    """Live rooms right now (Clubhouse POST /get_feed_v3)."""
    auth = _require_auth()

    channels_list: list[dict[str, Any]] = []
    cursor = None
    seen_channels: set[str] = set()

    # Fetch up to 6 pages to get comprehensive feed of live rooms
    for _ in range(6):
        payload: dict[str, Any] = {}
        if cursor:
            payload["cursor"] = cursor

        try:
            raw = await _ch_authed_post(
                "/get_feed_v3",
                payload,
                auth["auth_token"],
                auth["user_id"],
                auth.get("device_id"),
            )
        except Exception:
            break

        items = raw.get("items", []) or []
        for item in items:
            if not isinstance(item, dict):
                continue

            # 1. Direct channel item
            if "channel" in item and isinstance(item["channel"], dict):
                parsed = _parse_channel_data(item["channel"])
                if parsed and parsed["channel"] not in seen_channels:
                    seen_channels.add(parsed["channel"])
                    channels_list.append(parsed)

            # 2. Carousel / Topic shelf with multiple channels (item['channels'])
            if "channels" in item and isinstance(item["channels"], list):
                for sub_ch in item["channels"]:
                    parsed = _parse_channel_data(sub_ch)
                    if parsed and parsed["channel"] not in seen_channels:
                        seen_channels.add(parsed["channel"])
                        channels_list.append(parsed)

            # 3. Nested items
            if "items" in item and isinstance(item["items"], list):
                for sub_item in item["items"]:
                    if isinstance(sub_item, dict):
                        if "channel" in sub_item and isinstance(sub_item["channel"], dict):
                            parsed = _parse_channel_data(sub_item["channel"])
                            if parsed and parsed["channel"] not in seen_channels:
                                seen_channels.add(parsed["channel"])
                                channels_list.append(parsed)

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
    """Recent chat backlog for a channel, so a late joiner sees what was said.

    Chat is not available in every room (a moderator can disable it, and older
    rooms predate the endpoint), and a room with no chat is not an error — an
    empty backlog is the right answer, so an upstream failure degrades to one
    rather than failing the join.
    """
    auth = _require_auth()
    try:
        res = await _ch_authed_get(
            "/get_channel_messages",
            auth["auth_token"],
            auth["user_id"],
            auth.get("device_id"),
            {"channel": channel},
        )
        return {"comments": res.get("messages", [])}
    except HTTPException as exc:
        logger.info(
            "Clubhouse chat backlog unavailable for %s: %s", channel, exc.detail
        )
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
    """Notify Clubhouse of speaker mute/unmute state (Clubhouse POST /mute_speaker)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/mute_speaker",
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
async def change_handraise_settings(
    channel: str, body: HandraiseSettingsRequest
) -> dict[str, Any]:
    """Change the hand-raise policy for the channel."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/update_is_ask_to_join_allowed",
        {
            "channel": channel,
            "is_ask_to_join_allowed": body.is_enabled,
            "handraise_permission": body.handraise_permission,
        },
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/topic")
async def update_channel_topic(
    channel: str, body: UpdateTopicRequest
) -> dict[str, Any]:
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
async def update_chat_settings(
    channel: str, body: ChatSettingsRequest
) -> dict[str, Any]:
    """Enable or disable chat in the room."""
    auth = _require_auth()
    endpoint = (
        "/enable_channel_messages" if body.enable_chat else "/disable_channel_messages"
    )
    return await _ch_authed_post(
        endpoint,
        {"channel": channel},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/uninvite_speaker")
async def uninvite_speaker(channel: str, body: UninviteSpeakerRequest) -> dict[str, Any]:
    """Move a speaker back to the audience (Clubhouse POST /uninvite_speaker)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/uninvite_speaker",
        {"channel": channel, "user_id": body.user_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/make_moderator")
async def make_moderator(channel: str, body: MakeModeratorRequest) -> dict[str, Any]:
    """Promote a speaker to moderator (Clubhouse POST /make_moderator)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/make_moderator",
        {"channel": channel, "user_id": body.user_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/block")
async def block_from_channel(
    channel: str, body: BlockChannelUserRequest
) -> dict[str, Any]:
    """Remove and block a user from the room (Clubhouse POST /block_from_channel)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/block_from_channel",
        {"channel": channel, "user_id": body.user_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/end")
async def end_channel(channel: str) -> dict[str, Any]:
    """End the room for everyone (Clubhouse POST /end_channel)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/end_channel",
        {"channel": channel},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/make_public")
async def make_channel_public(channel: str) -> dict[str, Any]:
    """Make the room open to everyone (Clubhouse POST /make_channel_public)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/make_channel_public",
        {"channel": channel},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/make_social")
async def make_channel_social(channel: str) -> dict[str, Any]:
    """Make the room open to followed users (Clubhouse POST /make_channel_social)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/make_channel_social",
        {"channel": channel},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/channels/{channel}/reject_speaker")
async def reject_speaker_invite(
    channel: str, body: RejectSpeakerInviteRequest
) -> dict[str, Any]:
    """Reject an invitation to speak (Clubhouse POST /reject_speaker_invite)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/reject_speaker_invite",
        {"channel": channel, "user_id": auth["user_id"]},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


# --- Social, Online Presence, and Followers ---


@router.get("/online_friends", response_model=OnlineFriendsList)
async def get_online_friends() -> dict[str, Any]:
    """List active online friends (Clubhouse POST /get_online_friends)."""
    auth = _require_auth()
    res = await _ch_authed_post(
        "/get_online_friends",
        {},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )
    return {"users": res.get("users", []) or []}


@router.get("/users/{user_id}/followers")
async def get_followers(user_id: int, page_size: int = 50, page: int = 1) -> dict[str, Any]:
    """List followers of a user (Clubhouse GET /get_followers)."""
    auth = _require_auth()
    return await _ch_authed_get(
        "/get_followers",
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
        params={"user_id": user_id, "page_size": page_size, "page": page},
    )


@router.get("/notifications")
async def get_notifications(page_size: int = 25, page: int = 1) -> dict[str, Any]:
    """Get recent notifications (Clubhouse GET /get_notifications)."""
    auth = _require_auth()
    return await _ch_authed_get(
        "/get_notifications",
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
        params={"page_size": page_size, "page": page},
    )


# --- Events & Calendar ---


@router.get("/events")
async def get_events(is_filtered: bool = True, page_size: int = 25, page: int = 1) -> dict[str, Any]:
    """Get list of upcoming scheduled events (Clubhouse GET /get_events)."""
    auth = _require_auth()
    return await _ch_authed_get(
        "/get_events",
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
        params={
            "is_filtered": "true" if is_filtered else "false",
            "page_size": page_size,
            "page": page,
        },
    )


@router.post("/events")
async def create_event(body: CreateEventRequest) -> dict[str, Any]:
    """Create or schedule an event (Clubhouse POST /edit_event)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/edit_event",
        {
            "name": body.name,
            "time_start_epoch": body.time_start_epoch,
            "description": body.description,
            "club_id": body.club_id,
            "user_ids": body.user_ids or [auth["user_id"]],
            "is_member_only": body.is_member_only,
        },
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.delete("/events/{event_id}")
async def delete_event(event_id: int) -> dict[str, Any]:
    """Delete a scheduled event (Clubhouse POST /delete_event)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/delete_event",
        {"event_id": event_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


# --- Clubs ---


@router.get("/clubs/{club_id}")
async def get_club(club_id: int) -> dict[str, Any]:
    """Get details for a club (Clubhouse POST /get_club)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/get_club",
        {"club_id": club_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.get("/clubs/{club_id}/members")
async def get_club_members(
    club_id: int, return_followers: bool = False, return_members: bool = True, page_size: int = 50, page: int = 1
) -> dict[str, Any]:
    """List members of a club (Clubhouse GET /get_club_members)."""
    auth = _require_auth()
    return await _ch_authed_get(
        "/get_club_members",
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
        params={
            "club_id": club_id,
            "return_followers": int(return_followers),
            "return_members": int(return_members),
            "page_size": page_size,
            "page": page,
        },
    )


@router.post("/clubs/{club_id}/follow")
async def follow_club(club_id: int) -> dict[str, Any]:
    """Follow a club (Clubhouse POST /follow_club)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/follow_club",
        {"club_id": club_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/clubs/{club_id}/unfollow")
async def unfollow_club(club_id: int) -> dict[str, Any]:
    """Unfollow a club (Clubhouse POST /unfollow_club)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/unfollow_club",
        {"club_id": club_id},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


# --- Profile Management ---


@router.post("/me/bio")
async def update_bio(body: UpdateBioRequest) -> dict[str, Any]:
    """Update profile bio (Clubhouse POST /update_bio)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/update_bio",
        {"bio": body.bio},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/me/name")
async def update_name(body: UpdateNameRequest) -> dict[str, Any]:
    """Update display name (Clubhouse POST /update_name)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/update_name",
        {"name": body.name},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/me/username")
async def update_username(body: UpdateUsernameRequest) -> dict[str, Any]:
    """Update handle username (Clubhouse POST /update_username)."""
    auth = _require_auth()
    return await _ch_authed_post(
        "/update_username",
        {"username": body.username},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )


@router.post("/me/skintone")
async def update_skintone(body: UpdateSkintoneRequest) -> dict[str, Any]:
    """Update emoji hand skin tone 1-5 (Clubhouse POST /update_skintone)."""
    auth = _require_auth()
    if not 1 <= body.skintone <= 5:
        raise HTTPException(status_code=400, detail="Skintone must be between 1 and 5")
    return await _ch_authed_post(
        "/update_skintone",
        {"skintone": body.skintone},
        auth["auth_token"],
        auth["user_id"],
        auth.get("device_id"),
    )



# --- People Knowledge & Profile Memory Endpoints ---


@router.get("/people-memory")
def list_people_memory(q: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """List or search all users stored in the agent's persistent memory."""
    from backend.modules.clubhouse.people_memory import people_memory_store

    if q.strip():
        results = people_memory_store.search(q.strip())[:limit]
    else:
        results = people_memory_store.list_all(limit)
    return [p.to_dict() for p in results]


@router.get("/people-memory/{user_id}")
def get_person_memory(user_id: int) -> dict[str, Any]:
    """Get persistent memory details for a specific user."""
    from backend.modules.clubhouse.people_memory import people_memory_store

    person = people_memory_store.get(user_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found in memory")
    return person.to_dict()


@router.post("/people-memory/{user_id}/notes")
def add_person_note(user_id: int, body: dict[str, str]) -> dict[str, Any]:
    """Add a learned note / fact to a person's profile."""
    from backend.modules.clubhouse.people_memory import people_memory_store

    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Note cannot be empty")
    person = people_memory_store.add_note(user_id, note)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found in memory")
    return person.to_dict()


@router.delete("/people-memory/{user_id}/notes/{note_idx}")
def remove_person_note(user_id: int, note_idx: int) -> dict[str, Any]:
    """Delete a learned note from a person's profile."""
    from backend.modules.clubhouse.people_memory import people_memory_store

    person = people_memory_store.remove_note(user_id, note_idx)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found in memory")
    return person.to_dict()


@router.post("/people-memory/{user_id}/tags")
def add_person_tag(user_id: int, body: dict[str, str]) -> dict[str, Any]:
    """Add a tag to a user in memory."""
    from backend.modules.clubhouse.people_memory import people_memory_store

    tag = (body.get("tag") or "").strip()
    if not tag:
        raise HTTPException(status_code=400, detail="Tag cannot be empty")
    person = people_memory_store.add_tag(user_id, tag)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found in memory")
    return person.to_dict()


@router.delete("/people-memory/{user_id}/tags/{tag}")
def remove_person_tag(user_id: int, tag: str) -> dict[str, Any]:
    """Remove a tag from a user in memory."""
    from backend.modules.clubhouse.people_memory import people_memory_store

    person = people_memory_store.remove_tag(user_id, tag)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found in memory")
    return person.to_dict()


@router.delete("/people-memory/{user_id}")
def forget_person_memory(user_id: int) -> dict[str, Any]:
    """Forget all stored knowledge about a user."""
    from backend.modules.clubhouse.people_memory import people_memory_store

    forgotten = people_memory_store.forget_person(user_id)
    return {"user_id": user_id, "forgotten": forgotten}
