"""Registration of the built-in connectors.

Built-ins go straight into the process-global registry, the same one
`host.add_connector` writes to — so a backend plugin's connector is
indistinguishable from a first-party one at the `/api/connectors` surface.
"""

from __future__ import annotations

# google_sync is imported for the side effect of its `register_handler` call — the
# library module does the same thing with its queue_handlers.
from backend.modules.connectors.providers import (  # noqa: F401
    drive_fs,
    github,
    github_tools,
    google,
    google_sync,
    google_tools,
    huggingface,
    huggingface_tools,
)
from backend.sdk.registry import registry


def register_connectors() -> None:
    """Register every built-in connector, its agent tools, and any file provider it
    mounts (Drive browses as a virtual root — see `drive_fs`)."""
    # `streaming` lives in the share module rather than here: it is that module's
    # credential, it unlocks no agent tools, and only the relay path uses it.
    from backend.modules.share import streaming

    for connector in (
        github.build(),
        google.build(),
        huggingface.build(),
        streaming.connector(),
    ):
        registry.connectors[connector.id] = connector
    github_tools.register_agent_tools()
    google_tools.register_agent_tools()
    huggingface_tools.register_agent_tools()
    drive_fs.register()
