"""Link tokens for the public share relay.

A token is the whole of a public viewer's authority: hold one (and the passphrase,
if there is one) and you may watch; that is all a public viewer ever gets. So the
rules here are small on purpose.

**Opaque and random, not signed.** A signed token (a JWT) would let the relay
verify a link with no state at all, which is tempting for a horizontally scalable
service — but a signed token cannot be *revoked*, and "revocation is immediate" is
a requirement, not a nicety. Someone who stops a share expects the link to die at
that instant, not at expiry. So the registry is the authority and the token is a
lookup key with no meaning of its own.

**The passphrase is stored hashed**, even though this is a short-lived in-memory
registry. A relay process holds other people's live video; a crash dump or a stray
log line that spills plaintext passphrases is exactly the kind of thing that never
gets noticed. `compare_digest` on the way back, because a token check that leaks
timing is a token check that can be walked character by character.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

#: How long a link lives when the minting node does not say. Deliberately hours,
#: not days: a public URL that outlives the session it was minted for is the
#: failure mode this whole file exists to bound.
DEFAULT_TTL_S = 4 * 60 * 60

#: The ceiling a caller may ask for. A node asking for a year gets a day.
MAX_TTL_S = 24 * 60 * 60

#: Token entropy. 32 urlsafe bytes ≈ 256 bits — a public URL is guessable in
#: principle, so it must not be guessable in practice.
TOKEN_BYTES = 32


def _hash_passphrase(passphrase: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, 100_000).hex()


@dataclass
class Stream:
    """One minted link, and the ingest that may (eventually) feed it."""

    token: str
    created_at: float
    expires_at: float
    #: Empty when the link needs no passphrase.
    passphrase_hash: str = ""
    passphrase_salt: bytes = b""
    #: A label for the viewer page. Never trusted, never rendered as HTML.
    title: str = ""
    #: Set once the host's WHIP offer lands. Until then the link resolves but
    #: there is nothing to watch — which is a real state, not an error: a host
    #: mints a link, sends it, and starts the stream a minute later.
    live: bool = False
    #: Viewer peer connections currently attached, for the ceiling check.
    viewers: int = 0
    revoked: bool = False
    extra: dict[str, str] = field(default_factory=dict)

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def usable(self, now: float | None = None) -> bool:
        return not self.revoked and not self.expired(now)

    def check_passphrase(self, supplied: str) -> bool:
        if not self.passphrase_hash:
            return True
        candidate = _hash_passphrase(supplied or "", self.passphrase_salt)
        return hmac.compare_digest(candidate, self.passphrase_hash)


class Registry:
    """Every live link on this relay process.

    In-memory by design. Persisting it would make a relay restart resurrect links
    for sessions that ended while it was down, and the correct behaviour for a
    lost relay is that every link dies with it — the host still holds the fabric
    path, and re-minting is one click.
    """

    def __init__(self, *, max_viewers_per_stream: int = 25) -> None:
        self._streams: dict[str, Stream] = {}
        self.max_viewers_per_stream = max_viewers_per_stream

    def mint(
        self,
        *,
        title: str = "",
        ttl_s: int | None = None,
        passphrase: str = "",
        now: float | None = None,
    ) -> Stream:
        now = now if now is not None else time.time()
        ttl = DEFAULT_TTL_S if ttl_s is None else max(60, min(int(ttl_s), MAX_TTL_S))
        salt = secrets.token_bytes(16)
        stream = Stream(
            token=secrets.token_urlsafe(TOKEN_BYTES),
            created_at=now,
            expires_at=now + ttl,
            passphrase_hash=_hash_passphrase(passphrase, salt) if passphrase else "",
            passphrase_salt=salt if passphrase else b"",
            title=title[:120],
        )
        self._streams[stream.token] = stream
        return stream

    def get(self, token: str, now: float | None = None) -> Stream | None:
        """The stream for a token, or None if it is unknown, revoked or expired.

        Collapsing those three into one `None` is deliberate: telling a caller
        "that token existed but expired" confirms the token was real, which is
        one bit more than a stranger holding a guessed URL should ever learn.
        """
        stream = self._streams.get(token)
        if stream is None or not stream.usable(now):
            return None
        return stream

    def revoke(self, token: str) -> bool:
        """Kill a link now. Returns whether it was live to begin with."""
        stream = self._streams.get(token)
        if stream is None or stream.revoked:
            return False
        stream.revoked = True
        stream.live = False
        return True

    def sweep(self, now: float | None = None) -> int:
        """Drop expired and revoked entries. Returns how many went."""
        now = now if now is not None else time.time()
        dead = [t for t, s in self._streams.items() if s.revoked or s.expired(now)]
        for token in dead:
            del self._streams[token]
        return len(dead)

    def at_capacity(self, stream: Stream) -> bool:
        """Whether this stream has hit the per-stream viewer ceiling.

        An aiortc SFU has a real ceiling — tens of viewers on a small machine,
        not thousands. Refusing the 26th viewer with a clear answer is far better
        than accepting them and degrading the stream for the 25 already watching.
        """
        return stream.viewers >= self.max_viewers_per_stream

    def __len__(self) -> int:
        return len(self._streams)

    def all(self) -> list[Stream]:
        return list(self._streams.values())
