"""ListenBrainz: open-source scrobbling, and similar-artist radio for the DJ.

An **api-key** connector: the user token from listenbrainz.org/settings. Submitting
listens is a JSON POST with `Authorization: Token <token>`; `validate-token` answers
the username.

Similarity comes from ListenBrainz's Labs similar-artists dataset keyed by
MusicBrainz artist ids, which most library tracks only have after enrichment — so
this listener's `similar()` is best-effort and the DJ prefers Last.fm when both are
connected.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.modules.connectors import store
from backend.modules.connectors.store import Credential
from backend.modules.music.providers import DescribedTrack, ProviderError
from backend.sdk.types import Connector, ConnectorAccount, ConnectorStatus

logger = logging.getLogger(__name__)

CONNECTOR_ID = "listenbrainz"
API = "https://api.listenbrainz.org"
_TIMEOUT = 15.0


def _token() -> str:
    cred = store.load(CONNECTOR_ID)
    return cred.access_token if cred else ""


async def _request(
    method: str, path: str, *, token: str | None = None, **kwargs: Any
) -> dict[str, Any]:
    headers = {"Authorization": f"Token {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(
                method, f"{API}{path}", headers=headers, **kwargs
            )
    except httpx.HTTPError as exc:
        raise ProviderError(f"couldn't reach ListenBrainz: {exc}") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("error") or resp.text[:200]
        except ValueError:
            detail = resp.text[:200]
        raise ProviderError(f"ListenBrainz returned {resp.status_code}: {detail}")
    try:
        return resp.json() or {}
    except ValueError:
        return {}


# --- connector ----------------------------------------------------------------


async def _begin(options: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": "form",
        "fields": [
            {
                "name": "token",
                "label": "ListenBrainz user token",
                "secret": True,
                "help": "From listenbrainz.org/settings. Used only to submit your listens.",
            }
        ],
    }


async def _submit(values: dict[str, str]) -> dict[str, Any]:
    token = (values.get("token") or "").strip()
    if not token:
        if store.is_connected(CONNECTOR_ID):
            return {"connected": True}
        return {"error": "a ListenBrainz user token is required"}
    try:
        data = await _request("GET", "/1/validate-token", token=token)
    except ProviderError as exc:
        return {"error": str(exc)}
    if not data.get("valid"):
        return {"error": "ListenBrainz says that token isn't valid"}
    name = str(data.get("user_name") or "")
    account = {"id": name, "label": name or "ListenBrainz", "username": name}
    store.save(
        CONNECTOR_ID,
        Credential(access_token=token, scopes=["submit-listens"], account=account),
    )
    return {"connected": True, "account": account}


def _status() -> ConnectorStatus:
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        return ConnectorStatus(connected=False, error=error)
    if not cred or not cred.access_token:
        return ConnectorStatus(connected=False)
    account = cred.account or {}
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id=str(account.get("id") or ""),
            label=str(account.get("label") or "ListenBrainz"),
        ),
        scopes=cred.scopes,
    )


async def _disconnect() -> None:
    store.clear(CONNECTOR_ID)


def build() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="ListenBrainz",
        kind="api-key",
        icon="listenbrainz",
        blurb="Submit what you play to ListenBrainz, the open listening-history archive.",
        status=_status,
        begin=_begin,
        submit=_submit,
        disconnect=_disconnect,
        scopes=[],
        guide=None,
        configured=None,
    )


# --- listener -------------------------------------------------------------------


def _payload(track: DescribedTrack, listened_at: int | None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "media_player": "horrible-dashboard",
        "submission_client": "horrible-dashboard",
    }
    if track.duration:
        info["duration_ms"] = int(track.duration * 1000)
    if mbid := track.external_ids.get("mbid"):
        info["recording_mbid"] = mbid
    if isrc := track.external_ids.get("isrc"):
        info["isrc"] = isrc
    if spotify := track.external_ids.get("spotify"):
        info["spotify_id"] = f"https://open.spotify.com/track/{spotify}"
    meta: dict[str, Any] = {
        "artist_name": track.artist,
        "track_name": track.title,
        "additional_info": info,
    }
    if track.album:
        meta["release_name"] = track.album
    listen: dict[str, Any] = {"track_metadata": meta}
    if listened_at is not None:
        listen["listened_at"] = listened_at
    return listen


class ListenBrainzListener:
    id = "listenbrainz"
    label = "ListenBrainz"

    def configured(self) -> bool:
        return bool(_token())

    async def now_playing(self, track: DescribedTrack) -> None:
        if not self.configured() or not track.artist:
            return
        await _request(
            "POST",
            "/1/submit-listens",
            token=_token(),
            json={"listen_type": "playing_now", "payload": [_payload(track, None)]},
        )

    async def scrobble(self, track: DescribedTrack, started_at: int) -> None:
        if not self.configured():
            raise ProviderError("ListenBrainz isn't connected")
        if not track.artist:
            return
        await _request(
            "POST",
            "/1/submit-listens",
            token=_token(),
            json={"listen_type": "single", "payload": [_payload(track, started_at)]},
        )

    async def similar(
        self, seed: DescribedTrack, limit: int = 20
    ) -> list[DescribedTrack]:
        """LB Radio's artist mode: tracks from artists similar to the seed's."""
        if not seed.artist:
            return []
        try:
            data = await _request(
                "GET",
                "/1/explore/lb-radio",
                token=_token() or None,
                params={"prompt": f"artist:({seed.artist})", "mode": "easy"},
            )
        except ProviderError as exc:
            logger.info("lb-radio failed for %s: %s", seed.artist, exc)
            return []
        tracks = ((data.get("payload") or {}).get("jspf") or {}).get(
            "playlist", {}
        ).get("track") or []
        out: list[DescribedTrack] = []
        for t in tracks:
            if not isinstance(t, dict) or not t.get("title"):
                continue
            ids = t.get("identifier") or []
            ident = ids[0] if isinstance(ids, list) and ids else ids
            mbid = str(ident).rsplit("/", 1)[-1] if ident else ""
            duration = (t.get("duration") or 0) / 1000 or None
            out.append(
                DescribedTrack(
                    title=str(t["title"]),
                    artist=str(t.get("creator") or ""),
                    album=str(t.get("album") or ""),
                    duration=duration,
                    external_ids={"mbid": mbid} if mbid else {},
                )
            )
        return out[:limit]


listener = ListenBrainzListener()
