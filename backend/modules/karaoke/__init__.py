"""Karaoke module: search, download, queue and play karaoke videos.

A PiKaraoke-shaped karaoke machine as a dashboard pane. The session (queue, now
playing, transport) is process-global and broadcast on the `karaoke` `/ws` channel,
so the stage pane, the queue pane and a guest's phone on the LAN are all the same
view over one server-held state. See docs/modules/karaoke.mdx.
"""

from backend.modules.karaoke.agent_tools import register_agent_tools
from backend.modules.karaoke.routes import router
from backend.modules.karaoke.store import init_karaoke_db

# The live `KaraokeSession` is deliberately NOT re-exported here. Binding the
# instance to the name `session` on the package would shadow the `session`
# *submodule*, so `backend.modules.karaoke.session.<anything>` would resolve
# against the object instead of the module — which breaks `monkeypatch.setattr`
# on a dotted path and any `import backend.modules.karaoke.session`. Import it
# from its own module: `from backend.modules.karaoke.session import session`.
__all__ = ["init_karaoke_db", "register_agent_tools", "router"]
