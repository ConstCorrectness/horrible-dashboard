"""What would leave this machine, said before it does.

Modelled on the share module's pre-flight: it **warns and blocks until acknowledged**;
it is not a guarantee. A pattern list cannot know every credential format, and saying
"no secrets found" as though it could would be the most dangerous sentence this module
could print. The UI therefore reads "found these", never "this is clean".

Two severities. A **secret** blocks: publishing a live token is the one mistake that is
immediately and irreversibly exploitable (gists and Pages are crawled for exactly this).
Everything else — a home path that names you, a traceback, a widget that will render as
nothing — is a warning shown alongside.

The patterns themselves live in `backend/publishing/scan.py`, shared with Scrive; this
file decides which surfaces of a notebook to feed them. Scanned surfaces are sources and
the *text* representations of outputs. Base64 image payloads are skipped: they are not
text a person pasted, and random base64 matches short key prefixes often enough to train
people to click through.
"""

from __future__ import annotations

import json
from typing import Any

from backend.modules.notebook.models import PublishFinding
from backend.publishing.scan import scan_text as _scan_hits

WIDGET_MIME = "application/vnd.jupyter.widget-view+json"


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


def scan(nb: Any) -> list[PublishFinding]:
    findings: list[PublishFinding] = []
    seen: set[tuple[int, str, str, str]] = set()

    def add(finding: PublishFinding) -> None:
        key = (finding.cell, finding.where, finding.kind, finding.excerpt)
        if key not in seen:
            seen.add(key)
            findings.append(finding)

    def scan_text(index: int, cell_id: str, where: str, text: str) -> None:
        for hit in _scan_hits(text):
            add(
                PublishFinding(
                    cell=index,
                    cell_id=cell_id,
                    where=where,
                    kind=hit.kind,
                    label=hit.label,
                    excerpt=hit.excerpt,
                    blocking=hit.blocking,
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
