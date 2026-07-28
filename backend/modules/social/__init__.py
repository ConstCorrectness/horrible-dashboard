"""The social layer: a person-level friends roster over the peer fabric.

Where `network` deals in *machines* (node ids, transports, trust), this module
deals in *people*: a person identity that spans your devices, a shareable friend
code, and a roster whose accepted entries are granted fabric trust — which is what
lets friends message each other, share panes, and let their agents talk without a
second pairing step. See docs/modules/social.mdx.
"""

from backend.modules.social.channel import handle_social_message, subscribe_social_conn
from backend.modules.social.roster import register as register_social
from backend.modules.social.routes import router

__all__ = [
    "router",
    "handle_social_message",
    "subscribe_social_conn",
    "register_social",
]
