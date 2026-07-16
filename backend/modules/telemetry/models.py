from typing import Literal

from pydantic import BaseModel

# Detail fields (headers/bodies) are captured **raw**, only size-capped — see
# instrument.py. This is a local introspection tool; the buffer can hold
# credentials and personal data. `inbound`/`outbound` are HTTP; `ws` is one frame
# of the multiplexed `/ws` socket (payload in request_body, direction in method);
# `browser` is one request made by the embedded Chromium (see modules/browser).
IoSource = Literal["inbound", "outbound", "ws", "browser"]

# What the egress policy decided about a `browser` request. `allowed` passed the
# public-IP check, `blocked` was aborted before leaving the machine, and `pending`
# is still in flight — the open-connections view keys off this.
IoVerdict = Literal["allowed", "blocked", "pending"]


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
    # `browser`-only. `resource_type` is Chromium's own classification (document,
    # script, image, xhr, fetch, media, …) — the axis that makes a page's traffic
    # legible at a glance. `verdict` records what the SSRF guard did, so a blocked
    # request stays visible instead of vanishing.
    resource_type: str | None = None
    verdict: IoVerdict | None = None
