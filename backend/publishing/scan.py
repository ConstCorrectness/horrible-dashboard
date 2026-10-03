"""What would leave this machine, said before it does — the text-level scanner.

It **warns and blocks until acknowledged**; it is not a guarantee. A pattern list cannot
know every credential format, and saying "no secrets found" as though it could would be
the most dangerous sentence a publisher could print. Callers therefore render "found
these", never "this is clean".

Two severities. A **secret** blocks: publishing a live token is the one mistake that is
immediately and irreversibly exploitable (gists and Pages are crawled for exactly this).
A home path that names the account is a warning.

This module knows only text. Each publisher decides which surfaces of its document to
feed it (notebook sources and outputs, MyST blocks, a tweet) and wraps the hits in its
own finding model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

#: Label, pattern. Prefix-anchored formats first; the generic assignment last so a
#: token that matches both is reported by its specific name.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ),
    ("API key (sk-…)", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "Credential in code",
        re.compile(
            r"(?i)\b\w*(?:api[_-]?key|secret|token|passw(?:or)?d)\w*\s*[:=]\s*"
            r"['\"][^'\"\s]{8,}['\"]"
        ),
    ),
)

#: A home directory, which names the account it belongs to. One or two backslashes,
#: because a Windows path appears escaped inside a repr and bare inside a print.
HOME_PATH = re.compile(
    r"\b[A-Za-z]:\\{1,2}Users\\{1,2}[^\\\s'\"<>]+|/(?:home|Users)/[^/\s'\"<>]+"
)


@dataclass(frozen=True)
class Hit:
    kind: Literal["secret", "path"]
    label: str
    #: Enough to locate it. For a secret, only its first characters — finding lists go
    #: to the browser, and they are the kind of thing that gets pasted.
    excerpt: str
    blocking: bool


def mask(secret: str) -> str:
    """Enough to find it in the document, not enough to use it."""
    return f"{secret[:4]}…" if len(secret) > 4 else "…"


def scan_text(text: str) -> list[Hit]:
    """Every secret-shaped match, plus the first home path, in `text`."""
    if not text:
        return []
    hits = [
        Hit(kind="secret", label=label, excerpt=mask(m.group(0)), blocking=True)
        for label, pattern in SECRET_PATTERNS
        for m in pattern.finditer(text)
    ]
    match = HOME_PATH.search(text)
    if match:
        hits.append(
            Hit(
                kind="path",
                label="Home directory path",
                excerpt=match.group(0)[:80],
                blocking=False,
            )
        )
    return hits
