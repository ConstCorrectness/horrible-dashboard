"""Last.fm: scrobbling, "similar tracks" for the DJ, and loved tracks as a playlist.

**The connector** is Last.fm's desktop authentication, which is neither OAuth nor a
plain key:

1. you paste your API account's key and shared secret (last.fm/api/account/create);
2. `auth.getToken` mints a request token, and you approve it on last.fm;
3. `auth.getSession` trades the approved token for a session key, which never
   expires. Until you approve, it answers error 14 — which maps exactly onto the
   connector framework's polled flow (`oauth.begin_device`), so the popover opens
   the approval page and waits.

Every write (and `auth.*`) is signed: md5 of the sorted `key+value` pairs plus the
secret. The secret is stored in the encrypted secrets store, never in settings.

Similarity (`track.getSimilar`, `artist.getTopTracks`) only needs the API key, so
the DJ works for anyone who has configured a key even before approving a session.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from backend.modules.connectors import oauth, store
from backend.modules.connectors.store import Credential
from backend.modules.music.providers import (
    DescribedTrack,
    ImportedPlaylist,
    PlaylistSummary,
    ProviderError,
)
from backend.sdk.types import Connector, ConnectorAccount, ConnectorStatus

CONNECTOR_ID = "lastfm"
API = "https://ws.audioscrobbler.com/2.0/"
AUTH_PAGE = "https://www.last.fm/api/auth/"
_SECRET_KEY = "lastfm_api_secret"
_KEY_SETTING = "connectors.lastfm.apiKey"
_TIMEOUT = 15.0
_FLOW_TTL = 600.0

logger = logging.getLogger(__name__)


def api_key() -> str:
    try:
        from backend.modules.settings.routes import get_value  # noqa: PLC0415

        return str(get_value(_KEY_SETTING, "") or "")
    except Exception:  # noqa: BLE001
        return ""


def api_secret() -> str:
    try:
        from backend.modules.database.secrets_store import get_secret_or_none  # noqa: PLC0415

        return str(get_secret_or_none(_SECRET_KEY) or "")
    except Exception:  # noqa: BLE001
        return ""


def sign(params: dict[str, str], secret: str) -> str:
    """Last.fm's api_sig: md5 over sorted name+value pairs (minus `format` and
    `callback`), then the secret."""
    raw = "".join(
        f"{k}{params[k]}" for k in sorted(params) if k not in ("format", "callback")
    )
    return hashlib.md5((raw + secret).encode("utf-8")).hexdigest()  # noqa: S324 — the protocol's choice


async def call(
    method: str, params: dict[str, Any], *, signed: bool = False, post: bool = False
) -> dict[str, Any]:
    key = api_key()
    if not key:
        raise ProviderError("Last.fm has no API key configured")
    body: dict[str, str] = {k: str(v) for k, v in params.items() if v not in (None, "")}
    body.update({"method": method, "api_key": key})
    if signed:
        secret = api_secret()
        if not secret:
            raise ProviderError("Last.fm has no shared secret configured")
        body["api_sig"] = sign(body, secret)
    body["format"] = "json"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await (
                client.post(API, data=body) if post else client.get(API, params=body)
            )
    except httpx.HTTPError as exc:
        raise ProviderError(f"couldn't reach Last.fm: {exc}") from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise ProviderError(f"Last.fm returned {resp.status_code}") from exc
    if isinstance(data, dict) and data.get("error"):
        error = ProviderError(
            f"Last.fm error {data['error']}: {data.get('message', '')}"
        )
        error.code = int(data["error"])  # type: ignore[attr-defined]
        raise error
    return data if isinstance(data, dict) else {}


def session_key() -> str:
    cred = store.load(CONNECTOR_ID)
    return cred.access_token if cred else ""


# --- connector ----------------------------------------------------------------


def _form() -> dict[str, Any]:
    return {
        "step": "form",
        "fields": [
            {
                "name": "api_key",
                "label": "Last.fm API key",
                "value": api_key(),
                "help": "Create an API account at last.fm/api/account/create (any callback URL).",
            },
            {
                "name": "api_secret",
                "label": "Shared secret",
                "secret": True,
                "value": "",
                "help": "Shown next to the key. Stored encrypted; used to sign scrobbles.",
            },
        ],
    }


async def _poll_session(token: str) -> Credential | dict[str, Any]:
    try:
        data = await call("auth.getSession", {"token": token}, signed=True)
    except ProviderError as exc:
        if getattr(exc, "code", None) == 14:  # token not yet authorised
            return {"pending": True}
        return {"error": str(exc)}
    session = data.get("session") or {}
    name = str(session.get("name") or "")
    return Credential(
        access_token=str(session.get("key") or ""),
        scopes=["scrobble"],
        account={"id": name, "label": name or "Last.fm", "username": name},
    )


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    if options.get("reconfigure") or not (api_key() and api_secret()):
        return _form()
    try:
        data = await call("auth.getToken", {}, signed=True)
    except ProviderError as exc:
        return {"error": str(exc)}
    token = str(data.get("token") or "")
    step = oauth.begin_device(
        CONNECTOR_ID,
        user_code="",
        verification_uri=AUTH_PAGE,
        device_code=token,
        poll=_poll_session,
        interval=3.0,
        expires_in=_FLOW_TTL,
    )
    # Present it as a redirect: the user approves on a page, nothing to type.
    return {
        "step": "redirect",
        "authorize_url": f"{AUTH_PAGE}?{urlencode({'api_key': api_key(), 'token': token})}",
        "interval": step["interval"],
        "expires_in": step["expires_in"],
    }


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    from backend.modules.database.secrets_store import upsert_secret  # noqa: PLC0415
    from backend.modules.settings.routes import set_value  # noqa: PLC0415

    key = (values.get("api_key") or "").strip()
    secret = (values.get("api_secret") or "").strip()
    if not key:
        return {"error": "A Last.fm API key is required."}
    set_value(_KEY_SETTING, key)
    if secret:
        upsert_secret(_SECRET_KEY, secret)
    elif not api_secret():
        return {"error": "The shared secret is required."}
    return await _begin({})


def _status() -> ConnectorStatus:
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        return ConnectorStatus(connected=True, error=error)
    if cred is None or not cred.access_token:
        return ConnectorStatus(connected=False)
    account = cred.account or {}
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or ""),
            label=str(account.get("label") or "Last.fm"),
        ),
        scopes=cred.scopes,
    )


async def _disconnect() -> None:
    oauth.cancel_flow(CONNECTOR_ID)
    store.clear(CONNECTOR_ID)


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Last.fm",
        kind="custom",
        icon="lastfm",
        blurb="Scrobble what you play, import loved tracks, and find similar music for the DJ.",
        status=_status,
        begin=_begin,
        submit=_submit,
        poll=lambda: oauth.poll_flow(CONNECTOR_ID),
        disconnect=_disconnect,
        scopes=[],
        guide=None,
        configured=lambda: bool(api_key() and api_secret()),
    )


# --- listener -------------------------------------------------------------------


def _track_params(track: DescribedTrack) -> dict[str, Any]:
    params: dict[str, Any] = {"artist": track.artist, "track": track.title}
    if track.album:
        params["album"] = track.album
    if track.duration:
        params["duration"] = int(track.duration)
    return params


def _describe(obj: dict[str, Any]) -> DescribedTrack | None:
    title = str(obj.get("name") or "")
    artist = obj.get("artist") or {}
    artist_name = (
        str(artist.get("name") or artist.get("#text") or "")
        if isinstance(artist, dict)
        else str(artist)
    )
    if not title:
        return None
    external = {"mbid": str(obj["mbid"])} if obj.get("mbid") else {}
    duration = obj.get("duration")
    try:
        seconds = float(duration) if duration else None
    except (TypeError, ValueError):
        seconds = None
    return DescribedTrack(
        title=title, artist=artist_name, duration=seconds or None, external_ids=external
    )


class LastfmListener:
    id = "lastfm"
    label = "Last.fm"

    def configured(self) -> bool:
        return bool(api_key())

    def can_scrobble(self) -> bool:
        return bool(api_key() and api_secret() and session_key())

    async def now_playing(self, track: DescribedTrack) -> None:
        if not self.can_scrobble() or not track.artist:
            return
        await call(
            "track.updateNowPlaying",
            {**_track_params(track), "sk": session_key()},
            signed=True,
            post=True,
        )

    async def scrobble(self, track: DescribedTrack, started_at: int) -> None:
        if not self.can_scrobble():
            raise ProviderError("Last.fm isn't connected")
        if not track.artist:
            return  # Last.fm rejects artist-less scrobbles; nothing to retry
        await call(
            "track.scrobble",
            {**_track_params(track), "timestamp": started_at, "sk": session_key()},
            signed=True,
            post=True,
        )

    async def similar(
        self, seed: DescribedTrack, limit: int = 20
    ) -> list[DescribedTrack]:
        if not self.configured() or not seed.artist:
            return []
        data = await call(
            "track.getSimilar",
            {
                "artist": seed.artist,
                "track": seed.title,
                "limit": limit,
                "autocorrect": 1,
            },
        )
        tracks = (data.get("similartracks") or {}).get("track") or []
        out = [d for d in (_describe(t) for t in tracks if isinstance(t, dict)) if d]
        if out:
            return out
        # Obscure tracks have no similarity data; the artist's similar artists'
        # top tracks are the next best thing.
        data = await call(
            "artist.getSimilar", {"artist": seed.artist, "limit": 6, "autocorrect": 1}
        )
        artists = (data.get("similarartists") or {}).get("artist") or []
        for artist in artists[:6]:
            name = artist.get("name") if isinstance(artist, dict) else None
            if not name:
                continue
            try:
                top = await call("artist.getTopTracks", {"artist": name, "limit": 3})
            except ProviderError:
                continue
            for t in (top.get("toptracks") or {}).get("track") or []:
                if isinstance(t, dict) and (d := _describe(t)):
                    out.append(d)
        return out[:limit]

    async def loved(self, limit: int = 1000) -> list[DescribedTrack]:
        cred = store.load(CONNECTOR_ID)
        user = str((cred.account or {}).get("username") or "") if cred else ""
        if not user:
            raise ProviderError("Last.fm isn't connected")
        out: list[DescribedTrack] = []
        page = 1
        while len(out) < limit:
            data = await call(
                "user.getLovedTracks", {"user": user, "limit": 200, "page": page}
            )
            block = data.get("lovedtracks") or {}
            items = block.get("track") or []
            out += [
                d for d in (_describe(t) for t in items if isinstance(t, dict)) if d
            ]
            attrs = block.get("@attr") or {}
            if page >= int(attrs.get("totalPages") or 1):
                break
            page += 1
        return out[:limit]


listener = LastfmListener()


class LastfmLovedImporter:
    """Your Last.fm loved tracks, as one playlist."""

    id = "lastfm-loved"
    label = "Last.fm loved tracks"
    kind = "account"

    def configured(self) -> bool:
        return listener.can_scrobble()

    async def list_playlists(self) -> list[PlaylistSummary]:
        return [PlaylistSummary(ref="loved", name="Loved tracks (Last.fm)")]

    async def read_playlist(self, ref: str) -> ImportedPlaylist:
        tracks = await listener.loved()
        return ImportedPlaylist(
            name="Loved tracks (Last.fm)", origin="lastfm:loved", tracks=tracks
        )

    def parse(self, filename: str, data: bytes) -> ImportedPlaylist:
        raise ProviderError("Last.fm loved tracks come from your account, not a file")
