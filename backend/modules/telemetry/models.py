from typing import Literal

from pydantic import BaseModel

# Detail fields (headers/bodies) are captured **redacted and truncated** — see
# instrument.py: credential-bearing headers are masked, bodies on sensitive
# routes (Clubhouse auth: phone numbers, SMS codes, tokens) are suppressed, and
# everything is capped in size. Never record a raw header or body.
IoSource = Literal["inbound", "outbound"]


class IoEvent(BaseModel):
    id: int
    ts: float
    source: IoSource
    method: str
    target: str
    status: int | None = None
    duration_ms: float | None = None
    request_bytes: int | None = None
    response_bytes: int | None = None
    error: str | None = None
    # Expandable detail — present only when safely capturable (see instrument.py).
    request_headers: dict[str, str] | None = None
    response_headers: dict[str, str] | None = None
    request_body: str | None = None
    response_body: str | None = None
