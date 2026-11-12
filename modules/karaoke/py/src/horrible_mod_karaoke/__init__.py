"""Karaoke module: search, download, queue and play karaoke videos.

A PiKaraoke-shaped karaoke machine as a dashboard pane. The session (queue, now
playing, transport) is process-global and broadcast on the `karaoke` `/ws` channel,
so the stage pane, the queue pane and a guest's phone on the LAN are all the same
view over one server-held state. See docs/modules/karaoke.mdx.

`MODULE` is the entry the host loads (`horrible-module.json` `py.entry`). The
`/api/karaoke/*` routes and the `karaoke.*` agent tools come and go with it.
"""

from __future__ import annotations

from backend.kernel import HorribleModule, ModuleHost
from horrible_mod_karaoke.agent_tools import TOOLS
from horrible_mod_karaoke.routes import router


class KaraokeModule(HorribleModule):
    # Identity (id, name, tool groups) comes from horrible-module.json, not here.
    def setup(self, host: ModuleHost) -> None:
        host.add_router(router)
        for tool in TOOLS:
            host.add_agent_tool(tool)


MODULE = KaraokeModule()

# The live `KaraokeSession` is deliberately NOT re-exported here. Binding the
# instance to the name `session` on the package would shadow the `session`
# *submodule*, so `horrible_mod_karaoke.session.<anything>` would resolve against
# the object instead of the module, which breaks `monkeypatch.setattr` on a dotted
# path. Import it from its own module: `from horrible_mod_karaoke.session import session`.
__all__ = ["MODULE", "KaraokeModule"]
