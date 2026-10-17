"""Wiring: built-in providers, connectors and hooks (at import), and the runtime
pieces that need an event loop (at startup)."""

from __future__ import annotations

import logging

from backend.modules.music import acquire, curator, enrich, jobs, library
from backend.modules.music import listeners as music_listeners
from backend.modules.music.importers import register_builtin_importers
from backend.modules.music.session import session

logger = logging.getLogger(__name__)


def register_music() -> None:
    """Register the built-in sources, importers, listeners and connectors, and hook
    them to the session. Called from `backend/app.py` before plugins load, so a
    plugin reusing an id replaces the built-in."""
    from backend.modules.music.importers import spotify  # noqa: PLC0415
    from backend.modules.music.listeners import lastfm, listenbrainz  # noqa: PLC0415
    from backend.sdk.registry import registry  # noqa: PLC0415

    acquire.register_builtin_sources()
    register_builtin_importers()
    music_listeners.register_builtin_listeners()
    acquire.on_track_ready(enrich.on_ready)
    session.on_started(music_listeners.on_started)
    session.on_finished(music_listeners.on_finished)
    session.set_refill(curator.radio_refill)
    for connector in (spotify.build(), lastfm.build(), listenbrainz.build()):
        registry.connectors.setdefault(connector.id, connector)


async def start_music() -> None:
    """Lifespan startup: tables, the saved session (paused), interrupted imports,
    and the scrobble retry loop."""
    library.init_music_db()
    session.load()
    resumed = jobs.resume_jobs()
    if resumed:
        logger.info("resumed %d music import job(s)", resumed)
    music_listeners.start_retry_loop()
