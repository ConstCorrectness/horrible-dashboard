"""Google Drive OAuth for the Colab push target.

Colab has no publishing API, so notebooks reach it via Drive. Users bring their
own OAuth client (settings `training.google.clientId/clientSecret` — documented
in docs/modules/training.mdx); the refresh token persists server-side in
`.data/training_google_token.json` and is **never** returned to the client
(clubhouse token pattern). The flow is manual-code paste (works headless under
Tauri): `/google/auth/start` yields the consent URL, `/google/auth/complete`
exchanges the pasted code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.modules.training.push.base import PushError
from backend import paths

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
# Google's manual-copy flow (no local listener needed).
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


def _token_path() -> Path:
    return paths.data_dir() / "training_google_token.json"


def _client_config() -> dict[str, Any]:
    from backend.modules.settings.routes import get_value

    client_id = str(get_value("training.google.clientId", "") or "")
    client_secret = str(get_value("training.google.clientSecret", "") or "")
    if not client_id or not client_secret:
        raise PushError(
            "Google OAuth not configured — set training.google.clientId and "
            "training.google.clientSecret in Settings (see docs/modules/training)"
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }


def _flow():
    try:
        from google_auth_oauthlib.flow import Flow
    except ImportError as exc:  # pragma: no cover — dep is in pyproject
        raise PushError(f"google-auth-oauthlib not installed: {exc}") from exc
    return Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI
    )


def auth_start() -> str:
    """The consent URL the user opens; they paste the code back to complete."""
    url, _state = _flow().authorization_url(prompt="consent", access_type="offline")
    return url


def auth_complete(code: str) -> None:
    """Exchange the pasted code and persist credentials server-side."""
    flow = _flow()
    try:
        flow.fetch_token(code=code.strip())
    except Exception as exc:
        raise PushError(f"Google code exchange failed: {exc}") from exc
    creds = flow.credentials
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")


def status() -> dict[str, Any]:
    return {"connected": _token_path().is_file()}


def disconnect() -> None:
    _token_path().unlink(missing_ok=True)


def credentials():
    """Live Credentials (auto-refreshing), or raise PushError if not connected."""
    path = _token_path()
    if not path.is_file():
        raise PushError("Google Drive not connected — run the Colab connect flow first")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:  # pragma: no cover
        raise PushError(f"google-auth not installed: {exc}") from exc
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json(), encoding="utf-8")
    return creds
