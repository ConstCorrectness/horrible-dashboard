"""Friend codes: the human-typable rendering of a `person_id`.

A `person_id` is 16 characters of RFC-4648 base32 — unambiguous to a machine but
miserable to read aloud or retype. A friend code wraps the same 16 characters in a
checksummed, grouped form:

    HD-ABCD-EFGH-IJKL-MNOP-QRST
       \\_______________/ \\__/
        the person id     checksum

The checksum's job is to tell a *typo* apart from a *stranger*: without it, a
mistyped code and an unreachable person both surface as "not found", and the user
has no idea which of the two happened. Four check characters is more than typo
detection strictly needs, but it makes every group exactly four wide, which is the
form people are used to transcribing.

Parsing is deliberately forgiving. Base32 uses `A-Z` and `2-7`, so `0`, `1`, `8`
and `9` can never legitimately appear and are safely folded onto the letters they
get mistaken for. `1` is folded to `I` rather than `L` — both are plausible, so
that one is a guess, and a wrong guess is exactly what the checksum catches.
"""

from __future__ import annotations

import base64
import hashlib

PREFIX = "HD"
ID_LEN = 16
CHECK_LEN = 4
GROUP = 4

# Digits base32 never produces, mapped to the letter they are usually mistaken for.
_CONFUSABLES = str.maketrans({"0": "O", "1": "I", "8": "B", "9": "G"})


def _checksum(person_id: str) -> str:
    """Check characters for `person_id`, in the same alphabet as the id itself.

    Domain-separated so these bytes can never collide with another hash the fabric
    computes over the same id (the node fingerprint, say).
    """
    digest = hashlib.sha256(f"horrible-friend-code:{person_id}".encode()).digest()
    return base64.b32encode(digest).decode("ascii")[:CHECK_LEN]


def format_friend_code(person_id: str) -> str:
    """Render `person_id` as the grouped, checksummed code a user shares."""
    body = (person_id.upper() + _checksum(person_id))[: ID_LEN + CHECK_LEN]
    groups = [body[i : i + GROUP] for i in range(0, len(body), GROUP)]
    return f"{PREFIX}-" + "-".join(groups)


def normalize(code: str) -> str:
    """Strip a code down to its bare alphanumeric body, upper-cased and de-confused.

    Tolerates the separators and prefix people paste along with a code — spaces,
    dashes, a leading `HD`/`HD-`, and lowercase.
    """
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    if raw.startswith(PREFIX):
        raw = raw[len(PREFIX) :]
    return raw.translate(_CONFUSABLES)


def parse_friend_code(code: str) -> str:
    """The `person_id` a friend code encodes.

    Raises `ValueError` on a malformed or mistyped code — the caller can therefore
    report "that code is wrong" without a network round trip.
    """
    raw = normalize(code)
    if len(raw) != ID_LEN + CHECK_LEN:
        raise ValueError(
            f"a friend code has {ID_LEN + CHECK_LEN} characters, got {len(raw)}"
        )
    person_id, check = raw[:ID_LEN], raw[ID_LEN:]
    # The id is stored lowercase everywhere else on the fabric (node ids are too),
    # so fold it back before checksumming or the digest won't match.
    person_id = person_id.lower()
    if _checksum(person_id) != check:
        raise ValueError("that friend code has a typo in it")
    return person_id


def is_friend_code(value: str) -> bool:
    """Whether `value` parses as a valid friend code."""
    try:
        parse_friend_code(value)
    except ValueError:
        return False
    return True


def resolve_person_id(value: str) -> str:
    """The person id `value` refers to, accepting either a friend code or a bare id.

    The two are told apart by *length*, not by whether parsing succeeds. Falling
    back to "treat it as a raw id" whenever the checksum fails would defeat the
    checksum entirely — a mistyped code would sail past validation and be dialed
    as if it were a real person. So a 20-character input is always judged as a
    code, and only a bare 16-character id is accepted verbatim.

    Raises `ValueError` with a message meant for the user.
    """
    raw = normalize(value)
    looks_like_code = len(
        raw
    ) == ID_LEN + CHECK_LEN or value.strip().upper().startswith(PREFIX)
    if looks_like_code:
        return parse_friend_code(value)
    if len(raw) == ID_LEN:
        return raw.lower()
    raise ValueError("that doesn't look like a friend code")
