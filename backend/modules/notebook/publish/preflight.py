"""What would leave this machine, said before it does.

Modelled on the share module's pre-flight: it **warns and blocks until acknowledged**;
it is not a guarantee. A pattern list cannot know every credential format, and saying
"no secrets found" as though it could would be the most dangerous sentence this module
could print. The UI therefore reads "found these", never "this is clean".

Two severities. A **secret** blocks: publishing a live token is the one mistake that is
immediately and irreversibly exploitable (gists and Pages are crawled for exactly this).
Everything else — a home path that names you, a traceback, a widget that will render as
nothing — is a warning shown alongside.

Scanned surfaces are sources and the *text* representations of outputs. Base64 image
payloads are skipped: they are not text a person pasted, and random base64 matches
short key prefixes often enough to train people to click through.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.modules.notebook.models import PublishFinding

WIDGET_MIME = "application/vnd.jupyter.widget-view+json"

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


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(v) for v in value)
    return str(value or "")


def _output_text(output: dict[str, Any]) -> str:
    parts = [_text(output.get("text"))]
    if output.get("output_type") == "error":
        parts.append("\n".join(_text(line) for line in output.get("traceback") or []))
    for mime, value in (output.get("data") or {}).items():
        if mime.startswith("text/"):
            parts.append(_text(value))
        elif mime == "application/json":
            parts.append(json.dumps(value))
    return "\n".join(p for p in parts if p)


def _mask(secret: str) -> str:
    """Enough to find it in the cell, not enough to use it."""
    return f"{secret[:4]}…" if len(secret) > 4 else "…"


def scan(nb: Any) -> list[PublishFinding]:
    findings: list[PublishFinding] = []
    seen: set[tuple[int, str, str, str]] = set()

    def add(finding: PublishFinding) -> None:
        key = (finding.cell, finding.where, finding.kind, finding.excerpt)
        if key not in seen:
            seen.add(key)
            findings.append(finding)

    def scan_text(index: int, cell_id: str, where: str, text: str) -> None:
        if not text:
            return
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                add(
                    PublishFinding(
                        cell=index,
                        cell_id=cell_id,
                        where=where,
                        kind="secret",
                        label=label,
                        excerpt=_mask(match.group(0)),
                        blocking=True,
                    )
                )
        match = HOME_PATH.search(text)
        if match:
            add(
                PublishFinding(
                    cell=index,
                    cell_id=cell_id,
                    where=where,
                    kind="path",
                    label="Home directory path",
                    excerpt=match.group(0)[:80],
                    blocking=False,
                )
            )

    for index, cell in enumerate(nb.cells):
        cell_id = str(cell.get("id") or index)
        scan_text(index, cell_id, "source", _text(cell.get("source")))
        for output in cell.get("outputs") or []:
            if output.get("output_type") == "error":
                add(
                    PublishFinding(
                        cell=index,
                        cell_id=cell_id,
                        where="output",
                        kind="traceback",
                        label="Error traceback",
                        excerpt=f"{output.get('ename', '')}: {output.get('evalue', '')}"[
                            :120
                        ],
                        blocking=False,
                    )
                )
            if WIDGET_MIME in (output.get("data") or {}):
                add(
                    PublishFinding(
                        cell=index,
                        cell_id=cell_id,
                        where="output",
                        kind="widget",
                        label="Interactive widget",
                        excerpt="renders as nothing without a live kernel",
                        blocking=False,
                    )
                )
            scan_text(index, cell_id, "output", _output_text(output))
    return findings
