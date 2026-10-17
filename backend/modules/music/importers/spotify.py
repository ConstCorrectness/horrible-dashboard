"""Spotify as a playlist *importer*: read your playlists and Liked Songs, never play.

Spotify streams are DRM-protected and only play inside Spotify's own players, so
nothing here touches audio. What Spotify is good for is *knowing your music*: the
playlists you've built, the songs you've liked, and clean metadata (title, artists,
album, cover, ISRC, duration) for each. The import job turns that into local
playlists of files the library owns.

**The connector** is an OAuth 2.0 authorization-code + PKCE **public client** (no
secret), same shape as the X connector. Bring your own app from
developer.spotify.com and register this node's callback URL exactly
(`http://127.0.0.1:<port>/api/connectors/spotify/callback` — Spotify requires the
loopback IP literal, not `localhost`). Read-only scopes only.

**Development-mode limits** (Spotify's February 2026 rules, which apply to any app
you create yourself): the app's owner needs an active Premium subscription, and a
dev-mode app serves at most five users. Since the 2026 migration the playlist items
endpoint is `/playlists/{id}/items` (was `/tracks`), with `items[].item` (was
`items[].track`); both shapes are read here. Item lists may be withheld for
playlists you don't own — that comes back as a note, not a failure.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from backend.modules.connectors import config, oauth, store
from backend.modules.connectors.providers import social_http
from backend.modules.connectors.store import Credential
from backend.modules.music.providers import (
    DescribedTrack,
    ImportedPlaylist,
    PlaylistSummary,
    ProviderError,
)
from backend.sdk.types import (
    Connector,
    ConnectorAccount,
    ConnectorScope,
    ConnectorStatus,
)

CONNECTOR_ID = "spotify"
SERVICE = "Spotify"
ID_ENV = "SPOTIFY_CLIENT_ID"

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"

LIKED_REF = "liked"
_PAGE = 50
# A Liked Songs library can be tens of thousands of tracks; an import of that size
# is a download queue measured in days. Cap it and say so.
_MAX_TRACKS = 2000

SCOPE_IDS = [
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
]
SCOPES = [
    ConnectorScope(
        id="playlist-read-private",
        label="Read your playlists",
        description="To import them into your local music library. Nothing is changed on Spotify.",
    ),
    ConnectorScope(
        id="playlist-read-collaborative", label="Read collaborative playlists"
    ),
    ConnectorScope(id="user-library-read", label="Read your Liked Songs"),
]


def client_id() -> str:
    return config.client_id(CONNECTOR_ID, ID_ENV)


def _configured() -> bool:
    return config.is_configured(CONNECTOR_ID, id_env=ID_ENV)


def _configure_step() -> dict[str, Any]:
    return config.configure_step(
        CONNECTOR_ID,
        id_env=ID_ENV,
        id_label="Spotify app Client ID",
        id_help=(
            "Create an app at developer.spotify.com (Web API), and add this redirect URI "
            f"exactly: {oauth.redirect_uri(CONNECTOR_ID)}. Development-mode apps need the "
            "app owner to have Spotify Premium and allow up to five users."
        ),
    )


def _authorize_url(state: str, challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id(),
        "redirect_uri": oauth.redirect_uri(CONNECTOR_ID),
        "scope": " ".join(SCOPE_IDS),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _credential_from(
    data: dict[str, Any], account: dict[str, Any], old_refresh: str | None = None
) -> Credential:
    expires_in = data.get("expires_in")
    return Credential(
        access_token=str(data.get("access_token") or ""),
        # Spotify may or may not rotate the refresh token; keep the old one if not.
        refresh_token=data.get("refresh_token") or old_refresh,
        expires_at=time.time() + float(expires_in) if expires_in else None,
        scopes=str(data.get("scope") or " ".join(SCOPE_IDS)).split(),
        account=account,
    )


async def _token_request(form: dict[str, str]) -> dict[str, Any]:
    res = await social_http.call(
        "POST",
        TOKEN_URL,
        service=SERVICE,
        data={**form, "client_id": client_id()},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20.0,
    )
    return res.json()


async def fetch_account(access_token: str) -> dict[str, Any]:
    try:
        res = await social_http.call(
            "GET", f"{API}/me", service=SERVICE, token=access_token, timeout=15.0
        )
        me = res.json() or {}
    except (social_http.SendError, ValueError):
        return {"id": "", "label": "Spotify account"}
    images = me.get("images") or []
    return {
        "id": str(me.get("id") or ""),
        "label": str(me.get("display_name") or me.get("id") or "Spotify account"),
        "avatar_url": images[0].get("url")
        if images and isinstance(images[0], dict)
        else None,
    }


async def _exchange(code: str, verifier: str) -> Credential | dict[str, Any]:
    try:
        data = await _token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": oauth.redirect_uri(CONNECTOR_ID),
            }
        )
    except social_http.SendError as exc:
        return {"error": str(exc)}
    account = await fetch_account(str(data.get("access_token") or ""))
    return _credential_from(data, account)


async def _refresh(cred: Credential) -> Credential | dict[str, Any]:
    if not cred.refresh_token:
        return {"error": "no refresh token — reconnect Spotify"}
    try:
        data = await _token_request(
            {"grant_type": "refresh_token", "refresh_token": cred.refresh_token}
        )
    except social_http.SendError as exc:
        return {"error": str(exc)}
    return _credential_from(data, cred.account, cred.refresh_token)


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    if options.get("reconfigure") or not _configured():
        return _configure_step()
    return oauth.begin_redirect(
        CONNECTOR_ID, authorize_url=_authorize_url, exchange=_exchange
    )


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    if error := config.apply_config(CONNECTOR_ID, values, id_env=ID_ENV):
        return {"error": error}
    return await _begin({})


def _status() -> ConnectorStatus:
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        return ConnectorStatus(connected=True, error=error)
    if cred is None:
        return ConnectorStatus(connected=False)
    account = cred.account or {}
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or ""),
            label=str(account.get("label") or "Spotify account"),
            avatar_url=account.get("avatar_url"),
        ),
        scopes=cred.scopes,
    )


async def _disconnect() -> None:
    oauth.cancel_flow(CONNECTOR_ID)
    store.clear(CONNECTOR_ID)


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Spotify",
        kind="oauth",
        icon="spotify",
        blurb="Import your playlists and Liked Songs into the music library.",
        status=_status,
        begin=_begin,
        submit=_submit,
        poll=lambda: oauth.poll_flow(CONNECTOR_ID),
        disconnect=_disconnect,
        scopes=SCOPES,
        # No agent tools of its own: the music.* tools reach it as an importer.
        guide=None,
        configured=_configured,
    )


async def _token() -> str:
    cred = await oauth.ensure_fresh(CONNECTOR_ID, _refresh)
    if not cred or not cred.access_token:
        raise ProviderError(
            "Spotify isn't connected — connect it from the music settings."
        )
    return cred.access_token


class SpotifyHTTPError(ProviderError):
    def __init__(self, message: str, status: int | None) -> None:
        super().__init__(message)
        self.status = status


async def _get(url: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        res = await social_http.call("GET", url, service=SERVICE, token=token, params=params, timeout=20.0)
    except social_http.SendError as exc:
        raise SpotifyHTTPError(str(exc), exc.status) from exc
    return res.json() or {}


def _describe(obj: dict[str, Any]) -> DescribedTrack | None:
    """A Spotify track object → a description. None for podcasts episodes and local
    files, which have nothing a source could find."""
    if not obj or obj.get("type", "track") != "track" or obj.get("is_local"):
        return None
    title = str(obj.get("name") or "")
    if not title:
        return None
    artists = [
        str(a.get("name") or "")
        for a in obj.get("artists") or []
        if isinstance(a, dict)
    ]
    album = obj.get("album") or {}
    images = album.get("images") or []
    external: dict[str, str] = {}
    if obj.get("id"):
        external["spotify"] = str(obj["id"])
    isrc = (obj.get("external_ids") or {}).get("isrc")
    if isrc:
        external["isrc"] = str(isrc)
    return DescribedTrack(
        title=title,
        artist=artists[0] if artists else "",
        album=str(album.get("name") or ""),
        duration=(obj.get("duration_ms") or 0) / 1000 or None,
        external_ids=external,
        art_url=str(images[0].get("url") or "")
        if images and isinstance(images[0], dict)
        else "",
    )


def _item_track(item: dict[str, Any]) -> dict[str, Any]:
    # 2026 shape is `item`; the pre-migration shape was `track`.
    return item.get("item") or item.get("track") or {}


class SpotifyImporter:
    id = "spotify"
    label = "Spotify"
    kind = "account"

    def configured(self) -> bool:
        return store.is_connected(CONNECTOR_ID)

    async def list_playlists(self) -> list[PlaylistSummary]:
        token = await _token()
        out = [PlaylistSummary(ref=LIKED_REF, name="Liked Songs")]
        me = (store.load(CONNECTOR_ID) or Credential(access_token="")).account or {}
        url: str | None = f"{API}/me/playlists"
        params: dict[str, Any] | None = {"limit": _PAGE}
        while url:
            page = await _get(url, token, params)
            params = None  # `next` already carries them
            for pl in page.get("items") or []:
                if not isinstance(pl, dict):
                    continue
                owner = pl.get("owner") or {}
                counts = pl.get("items") or pl.get("tracks") or {}
                mine = (
                    not me.get("id")
                    or owner.get("id") == me.get("id")
                    or pl.get("collaborative")
                )
                out.append(
                    PlaylistSummary(
                        ref=str(pl.get("id") or ""),
                        name=str(pl.get("name") or "Untitled"),
                        track_count=counts.get("total")
                        if isinstance(counts, dict)
                        else None,
                        owner=str(owner.get("display_name") or owner.get("id") or ""),
                        note=""
                        if mine
                        else "Spotify may only return the tracks of playlists you own.",
                    )
                )
            url = page.get("next")
        return out

    async def read_playlist(self, ref: str) -> ImportedPlaylist:
        token = await _token()
        if ref == LIKED_REF:
            return await self._read_pages(
                f"{API}/me/tracks", token, name="Liked Songs", origin="spotify:liked"
            )
        meta = await _get(
            f"{API}/playlists/{ref}", token, {"fields": "name,owner(display_name,id)"}
        )
        name = str(meta.get("name") or "Spotify playlist")
        try:
            return await self._read_pages(
                f"{API}/playlists/{ref}/items",
                token,
                name=name,
                origin=f"spotify:{ref}",
            )
        except SpotifyHTTPError as exc:
            if exc.status == 404:
                # A node talking to an API that predates the rename.
                return await self._read_pages(
                    f"{API}/playlists/{ref}/tracks",
                    token,
                    name=name,
                    origin=f"spotify:{ref}",
                )
            if exc.status == 403:
                return ImportedPlaylist(
                    name=name,
                    origin=f"spotify:{ref}",
                    tracks=[],
                    note=(
                        "Spotify returned this playlist's details but not its tracks — "
                        "development-mode apps can usually only read playlists you own. "
                        "Copy it into a playlist of your own on Spotify, then import that."
                    ),
                )
            raise

    async def _read_pages(
        self, url: str, token: str, *, name: str, origin: str
    ) -> ImportedPlaylist:
        tracks: list[DescribedTrack] = []
        next_url: str | None = url
        params: dict[str, Any] | None = {"limit": _PAGE}
        note = ""
        while next_url:
            page = await _get(next_url, token, params)
            params = None
            for item in page.get("items") or []:
                desc = _describe(_item_track(item)) if isinstance(item, dict) else None
                if desc is not None:
                    tracks.append(desc)
            if len(tracks) >= _MAX_TRACKS:
                note = (
                    f"Imported the first {_MAX_TRACKS} tracks; the rest were left out."
                )
                tracks = tracks[:_MAX_TRACKS]
                break
            next_url = page.get("next")
        return ImportedPlaylist(name=name, origin=origin, tracks=tracks, note=note)

    def parse(self, filename: str, data: bytes) -> ImportedPlaylist:
        raise ProviderError("Spotify imports from your account, not a file")
