from typing import Literal

from pydantic import BaseModel

# Only metadata is ever recorded — never request/response bodies or headers,
# which would leak phone numbers, SMS codes, tokens, or prompts.
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
