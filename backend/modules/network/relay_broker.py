"""The relay broker, re-exported for node-side callers.

It lives at `backend/relay_broker.py` because the game server hosts it and must not
import the node's module graph — and importing anything from this package runs
`backend/modules/network/__init__.py`, which pulls in the hub, the settings store
and the data directory. Node code keeps importing it from here.
"""

from backend.relay_broker import (  # noqa: F401 - re-exports
    _clients,
    app,
    challenge_bytes,
    health,
    node_fingerprint,
    relay_ws,
    verify_registration,
    verify_signature,
)
