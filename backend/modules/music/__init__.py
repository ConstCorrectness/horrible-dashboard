"""Music module: the app's own music library, and a player that plays it.

Tracks are files the library owns (downloaded, or copied in from an import); the
session (queue, now playing, transport) is app-wide and broadcast on the `music`
`/ws` channel. Spotify, Apple Music exports, Last.fm and ListenBrainz *feed* the
library — playlists, metadata, scrobbles, similarity — and never play anything.
See docs/modules/music.mdx.
"""

from backend.modules.music.agent_tools import register_agent_tools
from backend.modules.music.library import init_music_db
from backend.modules.music.routes import router
from backend.modules.music.setup import register_music, start_music

# The live `MusicSession` is deliberately NOT re-exported: binding it to `session`
# here would shadow the `session` submodule (see karaoke/__init__.py).
__all__ = [
    "init_music_db",
    "register_agent_tools",
    "register_music",
    "router",
    "start_music",
]
