"""Share: put another person inside this workspace.

Two modes of one module, because they serve different people. A **friend on the
fabric** gets a semantic session — the workspace mirrored as structured state,
with a revocable ladder of rights over it. **Anyone with a link** gets a video
stream and a chat box, no install required.

Phase 1 (here) is the session and the permission model with no media in it: a host
opens a session, invites friends by *person*, and moves each guest up or down a
grant ladder that every guest action is checked against. `gate.py` is the one door
— there is deliberately no second, laxer path to the same actions.

Two rules the rest of the module rests on:

- **Trust is not authority.** `session.info.trusted` means a friend can reach us.
  What they may do here is `gate.require`, evaluated per action.
- **Deny by default.** A guest sees a pane only if that pane declared itself
  shareable, so a pane written next year that forgets to declare leaks nothing.

See docs/modules/share.mdx.
"""

from backend.modules.share.channel import handle_share_message
from backend.modules.share.fabric import register as register_share
from backend.modules.share.routes import router

__all__ = ["router", "handle_share_message", "register_share"]
