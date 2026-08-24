"""The `streaming` connector: RTMP destinations and their stream keys.

An RTMP **stream key is a password** — anyone holding it can broadcast to that
channel as you, and it does not expire on its own. So it goes in `secrets.db`
under the connector's Fernet-encrypted credential, never in a setting: every
setting is served to the browser by `GET /api/settings`, and the shape-based
blanking backstop is a backstop, not a place to put a credential on purpose.

Deliberately **not an OAuth connector**, unlike GitHub or Google. Twitch and
YouTube both have real OAuth APIs, but neither hands out an ingest key through
one without a review process, and both accept a key pasted from their own
dashboard. Asking the user for the thing their provider already shows them is
honest; building an OAuth flow that ends at "now go and paste your key anyway"
would not be.

TikTok is deliberately absent. It has no generally-available live-ingest API —
its RTMP endpoints are handed out per-account through a partner programme — so
listing it as a destination would be promising something this cannot deliver.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from backend.modules.connectors import store
from backend.modules.connectors.store import Credential
from backend.sdk.types import Connector, ConnectorAccount, ConnectorStatus

logger = logging.getLogger(__name__)

CONNECTOR_ID = "streaming"


@dataclass(frozen=True)
class Destination:
    """One place a stream can be sent.

    The ingest URL is public knowledge (it is in every provider's docs); only the
    key is secret. They are kept together because a key is meaningless without
    knowing which service it belongs to.
    """

    id: str
    label: str
    #: The RTMP application URL, without the key.
    ingest: str
    #: Where the user finds their key, so the form can say so rather than
    #: assuming they know.
    where: str


DESTINATIONS: dict[str, Destination] = {
    "twitch": Destination(
        id="twitch",
        label="Twitch",
        # Twitch publishes a list of regional ingests; this is the auto-selecting
        # global one, which is the right default for a personal broadcast.
        ingest="rtmp://live.twitch.tv/app",
        where="Twitch Creator Dashboard → Settings → Stream → Primary Stream key",
    ),
    "youtube": Destination(
        id="youtube",
        label="YouTube Live",
        ingest="rtmp://a.rtmp.youtube.com/live2",
        where="YouTube Studio → Go Live → Stream settings → Stream key",
    ),
    "custom": Destination(
        id="custom",
        label="Custom RTMP",
        # Supplied by the user in the form; anything speaking RTMP works, which is
        # most self-hosted servers (nginx-rtmp, Owncast, restream services).
        ingest="",
        where="Your own server's documentation",
    ),
}


def _load() -> dict[str, Any]:
    """The stored destinations, as `{destination id: {key, ingest}}`.

    Carried in `access_token` because that is the field `Credential` encrypts and
    this connector holds no OAuth token to put there. A JSON blob rather than one
    credential per destination: they are configured, revoked and forgotten
    together, and a partial disconnect is how a forgotten key survives.
    """
    cred, error = store.load_or_error(CONNECTOR_ID)
    if error:
        logger.warning("streaming: stored credential will not decrypt")
        return {}
    if cred is None or not cred.access_token:
        return {}
    try:
        data = json.loads(cred.access_token)
    except (ValueError, TypeError):
        logger.warning("streaming: stored credential is not readable JSON")
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    store.save(CONNECTOR_ID, Credential(access_token=json.dumps(data)))


def configured_destinations() -> list[str]:
    """Which destinations have a key stored. **Never the keys themselves.**"""
    return sorted(k for k, v in _load().items() if isinstance(v, dict) and v.get("key"))


def target_url(destination: str) -> str | None:
    """The full `rtmp://host/app/KEY` for a destination, or None if unconfigured.

    This is the one function that returns the secret, and it exists so the key
    reaches exactly one place: the argument list of the ffmpeg process. It must
    never be logged, echoed in an API response, or put in an error message —
    `redact()` is for anything that has to be shown.
    """
    entry = _load().get(destination)
    if not isinstance(entry, dict):
        return None
    key = str(entry.get("key") or "")
    if not key:
        return None
    base = str(entry.get("ingest") or "").rstrip("/")
    if not base:
        known = DESTINATIONS.get(destination)
        base = known.ingest.rstrip("/") if known else ""
    if not base:
        return None
    return f"{base}/{key}"


def redact(url: str) -> str:
    """An RTMP URL with the key replaced. Use this in every log line and error.

    A stream key in a log file is a stream key in a bug report, a screenshot and
    a pasted stack trace. The URL is otherwise useful for diagnosis, so it is
    kept and the last segment is dropped rather than blanking the whole thing.
    """
    if "/" not in url:
        return "rtmp://<redacted>"
    head, _, _tail = url.rpartition("/")
    return f"{head}/<key>"


def _status() -> ConnectorStatus:
    ready = configured_destinations()
    if not ready:
        return ConnectorStatus(connected=False)
    return ConnectorStatus(
        connected=True,
        account=ConnectorAccount(
            id="destinations",
            label=", ".join(DESTINATIONS[d].label for d in ready if d in DESTINATIONS)
            or "Configured",
        ),
        # The destination ids, never the keys. This list is served to the browser.
        scopes=ready,
    )


async def _begin(_params: dict[str, Any]) -> dict[str, Any]:
    """The form. One destination at a time, because keys are per-service."""
    return {
        "step": "form",
        "fields": [
            {
                "name": "destination",
                "label": "Service",
                "type": "select",
                "options": [
                    {"value": d.id, "label": d.label} for d in DESTINATIONS.values()
                ],
            },
            {
                "name": "ingest",
                "label": "Ingest URL (custom only)",
                "placeholder": "rtmp://your-server/live",
                "secret": False,
                "optional": True,
            },
            {
                "name": "key",
                "label": "Stream key",
                "secret": True,
                "help": "Twitch: Creator Dashboard → Settings → Stream. "
                "YouTube: Studio → Go Live → Stream settings.",
            },
        ],
    }


async def _submit(params: dict[str, Any]) -> dict[str, Any]:
    destination = str(params.get("destination") or "").strip()
    key = str(params.get("key") or "").strip()
    ingest = str(params.get("ingest") or "").strip()

    if destination not in DESTINATIONS:
        return {"step": "error", "message": "Pick a service."}
    if not key:
        return {"step": "error", "message": "A stream key is required."}
    if destination == "custom" and not ingest:
        return {"step": "error", "message": "A custom destination needs an ingest URL."}

    data = _load()
    data[destination] = {
        "key": key,
        "ingest": ingest or DESTINATIONS[destination].ingest,
    }
    _save(data)
    return {"step": "done"}


async def _disconnect() -> None:
    """Forget every key. All of them: a half-disconnected credential store is a
    place a forgotten key survives."""
    store.clear(CONNECTOR_ID)


def connector() -> Connector:
    return Connector(
        id=CONNECTOR_ID,
        label="Streaming",
        kind="custom",
        icon="streaming",
        blurb="Restream a shared screen to Twitch, YouTube Live, or any RTMP server.",
        status=_status,
        begin=_begin,
        submit=_submit,
        disconnect=_disconnect,
        # No agent tools. This connector's id therefore names no tool group, the
        # same arrangement `trackers` has -- starting somebody's public broadcast
        # is not a thing an agent should be able to do on its own initiative.
    )
