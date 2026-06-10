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
    ClubhouseStatus,
    CompleteAuthRequest,
    StartAuthRequest,
    StartAuthResult,
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


def _headers() -> dict[str, str]:
    return {
        "CH-Languages": "en-US",
        "CH-Locale": "en_US",
        "CH-AppBuild": "304",
        "CH-AppVersion": "0.1.28",
        "CH-DeviceId": _device_id(),
        "User-Agent": "clubhouse/304 (iPhone; iOS 14.4; Scale/2.00)",
    }


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
        detail = res.text[:300]
        status = res.status_code if res.status_code in (400, 401, 403, 429) else 502
        raise HTTPException(status_code=status, detail=f"Clubhouse error: {detail}")
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


@router.delete("/auth", response_model=ClubhouseStatus)
def disconnect() -> ClubhouseStatus:
    _auth_path().unlink(missing_ok=True)
    return ClubhouseStatus(connected=False)
