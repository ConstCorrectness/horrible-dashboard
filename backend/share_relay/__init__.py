"""The public share relay — a standalone service, not a backend module.

Deployed as its own Fly app (`horrible-share`) rather than inside a node's
backend or alongside the games server: it is the one component strangers talk to,
it scales on a different axis from everything else, and a relay outage must never
be able to take a node down with it.

See docs/architecture/share-relay.mdx.
"""
