"""What a trajectory looks like once it leaves this node.

Every run served to a friend — pulled, or watched live — passes through here first,
on **this** node, before the envelope is signed. The same rule the share module
follows for its mirror: redaction belongs to the side that owns the data, never to
the receiver, because asking the receiving machine to hide things is asking the
wrong one.

## Why not `store.redact`

`store.redact` matches `SECRET_KEY_SUFFIXES`, a *settings-key* vocabulary written for
names like `github.token` and `foo.apiKey`. Tool arguments are not settings keys. It
misses `api_key` (the suffix is `.key`, dotted), `Authorization`, `x-api-key` and
`cookie` — so a trajectory redacted with it alone would carry the most common ways a
credential appears in a tool call straight to a friend's machine. That gap was found
once already, for HTTP headers in `telemetry/store.py`.

So this does two independent passes, and either one is enough to blank a value:

1. **Key names**, normalized (lower-cased, separators stripped) and matched against
   credential words. Bare `key` is deliberately *not* on the list — `{"key": "alpha"}`
   is how half of all key-value tools name an ordinary argument, and blanking it would
   make a shared run unreadable to protect nothing.
2. **String contents**, scanned for credential-shaped tokens: bearer headers, vendor
   key prefixes, JWTs, PEM private keys, `password=` pairs, URL userinfo. A secret
   pasted into a tool *result* has no key name to match on, and results are where it
   is most likely to turn up.

## What this cannot promise

Pattern scanning finds credentials that look like credentials. A password typed as
prose, a private document, a customer's name — none of that has a shape. Sharing a
dataset stays an explicit per-dataset choice for exactly that reason, and the pane
says "best effort" rather than implying the run has been made safe.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.modules.trajectories.store import REDACTED, _is_secret_key

#: A value replaced by the content scanner. Distinct from `REDACTED` so a reader can
#: tell "this whole field was a secret" from "a secret was found inside this text".
MASK = "[redacted]"

#: Substrings of a *normalized* key name that mark its value as a credential.
SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "apikey",
    "accesskey",
    "privatekey",
    "clientsecret",
    "credential",
    "authorization",
    "cookie",
    "sessionid",
    "bearer",
    "signature",
)

#: Whole normalized key names that are credentials but too short to match as parts.
SENSITIVE_KEY_EXACT = frozenset({"auth", "pwd", "pat", "jwt", "otp", "pin"})

_NORMALIZE = re.compile(r"[^a-z0-9]")


def is_sensitive_key(key: str) -> bool:
    normalized = _NORMALIZE.sub("", str(key).lower())
    if normalized in SENSITIVE_KEY_EXACT:
        return True
    if any(part in normalized for part in SENSITIVE_KEY_PARTS):
        return True
    # The settings vocabulary still applies, so nothing it caught is lost.
    return _is_secret_key(str(key))


#: Credential-shaped substrings. Each is replaced whole, except the ones with a
#: capture group, where only the secret half is masked and the label kept — so a
#: reader still sees `password=[redacted]` and knows what was there.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        MASK,
    ),
    # An explicit `Authorization:` label is unambiguous, so whatever follows it on the
    # line is masked regardless of shape -- including a token with no digits, which the
    # scheme rule below deliberately lets through when it appears in prose.
    (
        re.compile(r"(?i)\b((?:proxy-)?authorization\s*[:=]\s*)[^\r\n\"']+"),
        r"\1" + MASK,
    ),
    # A scheme word followed by a value that *looks* like a credential: at least 12
    # characters with at least one digit. Without the digit rule this masked ordinary
    # English -- "the token generation step", "basic arithmetic" -- which a random
    # bearer token essentially never resembles.
    (
        re.compile(
            r"(?i)\b(bearer|basic|token)\s+"
            r"(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{12,}"
        ),
        r"\1 " + MASK,
    ),
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}"), MASK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), MASK),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), MASK),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        MASK,
    ),
    (
        re.compile(
            r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?token)"
            r"(\s*[=:]\s*)([\"']?)[^\s\"'&,;]{4,}"
        ),
        r"\1\2\3" + MASK,
    ),
    (re.compile(r"(://[^/\s:@]+:)[^@/\s]+@"), r"\1" + MASK + "@"),
)


def scrub_text(text: str) -> str:
    """Mask credential-shaped substrings in free text."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any) -> Any:
    """Recursively blank sensitive keys and mask credential-shaped strings."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if is_sensitive_key(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


#: Above this, one payload is replaced by a marker rather than sent.
PEER_PAYLOAD_MAX = 128 * 1024


def bounded(value: Any) -> Any:
    """A payload as it may cross the fabric: redacted, and within the size budget.

    Oversized payloads are replaced by an explicit marker carrying their size. A
    `blob:` pointer never reaches here — `store` resolves those on read — which
    matters because a pointer is a path on *this* disk and would arrive on the other
    side as a run with its largest results silently missing.
    """
    if value is None:
        return None
    cleaned = redact(value)
    try:
        size = len(json.dumps(cleaned, default=str))
    except Exception:  # noqa: BLE001 - unencodable: say so rather than send it
        return {"_omitted": "payload could not be encoded for sharing"}
    if size > PEER_PAYLOAD_MAX:
        return {"_omitted": "payload too large to share", "bytes": size}
    return cleaned
