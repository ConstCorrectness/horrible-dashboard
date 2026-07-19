"""Connectors: external accounts the node holds credentials for.

One connector is one integration — GitHub, Google, an API-key provider — and owns
three things: how you connect it, the credential once you have (held server-side,
encrypted, never handed to the browser), and the agent tools it unlocks.

Connectors are contributed through `backend.sdk` (`host.add_connector`), so built-in
modules and third-party backend plugins register identically. The home page renders
whatever is in the registry.

See docs/modules/connectors.mdx.
"""

from backend.modules.connectors.providers.github_routes import router as github_router
from backend.modules.connectors.providers.google_routes import router as google_router
from backend.modules.connectors.routes import router
from backend.modules.connectors.setup import register_connectors

__all__ = ["github_router", "google_router", "register_connectors", "router"]
