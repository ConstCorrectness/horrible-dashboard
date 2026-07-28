"""HorribleAssault: an AssaultCube remix that runs as a pane in the dashboard.

Five slices are in place: the **map and asset pipeline** (reading AssaultCube's
`.cgz` format), the **WebGL renderer**, the **authoritative match server** —
`match.py` simulates, `channel.py` carries input and snapshots over the shared
`/ws` socket, and the browser predicts and reconciles against it — **weapons**
(`weapons.py`: server-side hitscan with a clamped rewind to the shooter's own
render instant), and **bots** (`bots.py`: players whose input happens to be
produced here, so there is only ever one simulation).

Game *content* is never bundled: AssaultCube's media is copyright and only
redistributable inside an unmodified AssaultCube package, so the module reads from
a local install the user points it at. See docs/modules/hassault.mdx.
"""

from backend.modules.hassault.channel import handle as handle_hassault_message
from backend.modules.hassault.channel import on_disconnect as hassault_on_disconnect
from backend.modules.hassault.routes import router

__all__ = ["router", "handle_hassault_message", "hassault_on_disconnect"]
