"""Credential redaction for I/O events.

Its own module, with **no imports**, because both ends of the pipeline need it:
`store.py` (the durable copy) and `recorder.py` (the in-memory ring). `recorder` is
imported very early and `store` reaches `backend.modules.database` — which it defers
precisely to avoid a cycle — so having the ring import the store for this would put
that cycle back.
"""

from __future__ import annotations

#: Matched as substrings of a **lowercased header name**, so one entry covers a
#: family: `auth` catches `authorization` and `proxy-authorization`, `key` catches
#: `x-api-key` and `api-key`, `cookie` catches `set-cookie`.
#:
#: `trajectories.store.redact` cannot do this job: it matches `SECRET_KEY_SUFFIXES`,
#: which is a *settings-key* vocabulary (`github.token`, `foo.apiKey`). Those suffixes
#: are dotted, so `x-api-key` does not match `.key` and `Authorization` matches
#: nothing at all -- every credential header would have gone past it, silently.
SENSITIVE_HEADER_PARTS = (
    "authorization",
    "auth",
    "token",
    "key",
    "secret",
    "password",
    "cookie",
    "credential",
)

#: What a blanked value is replaced with. Blanked rather than dropped, for the reason
#: the settings route gives: an absent key is indistinguishable from one that was
#: never sent, and "this request was authenticated" is itself worth knowing -- it is
#: the difference between a 401 you misconfigured and a 401 you were refused.
HEADER_REDACTED = "***"


def is_sensitive_header(name: str) -> bool:
    lower = str(name).lower()
    return any(part in lower for part in SENSITIVE_HEADER_PARTS)


def redact_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    """Blank credential-bearing values, keeping every name.

    Case-insensitive on the name: httpx hands them over lowercased, but an event
    recorded from anywhere else need not, and a redaction that depends on the caller
    having lowercased first is one that fails silently on the caller that did not.
    """
    if not headers:
        return headers
    return {
        k: (HEADER_REDACTED if is_sensitive_header(k) else v)
        for k, v in headers.items()
    }
