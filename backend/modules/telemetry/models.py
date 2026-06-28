from typing import Literal

from pydantic import BaseModel

# Detail fields (headers/bodies) are captured **raw**, only size-capped — see
# instrument.py. This is a local introspection tool; the buffer can hold
# credentials and personal data. `inbound`/`outbound` are HTTP; `ws` is one frame
# of the multiplexed `/ws` socket (payload in request_body, direction in method).
IoSource = Literal["inbound", "outbound", "ws"]


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
