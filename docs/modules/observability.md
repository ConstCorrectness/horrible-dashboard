# Module: observability

Observe the app's data flow — frontend↔backend↔external — the way Docker
Desktop shows a container's network I/O. Optional: off the default dashboard,
added when you want it.

**Status: implemented** — frontend in
`packages/core/src/modules/observability/`, instrumentation in
`backend/modules/telemetry/` plus `packages/core/src/{telemetry,ws}.ts`.

## The instrumentation, not the widget, is the point

A useful observability view can't be per-module logging — it instruments the
**chokepoints once** so every module's traffic shows up for free (like the
Docker daemon already seeing all container I/O). Three sources feed one stream:

- **`client`** — every frontend→backend round-trip, recorded in the API client's
  `request<T>` ([packages/core/src/api.ts](../../packages/core/src/api.ts)).
- **`inbound`** — every request the backend receives, via an ASGI middleware
  (`backend/modules/telemetry/instrument.py`).
- **`outbound`** — the backend's calls to external services (Clubhouse, Ollama),
  via `instrumented_client()` wrapping httpx; agent and clubhouse use it. This is
  the part the frontend can't see on its own.

So a single user action reads end to end: `client GET /agent/status` →
`inbound /api/agent/status` → `outbound GET …/api/tags` (Ollama).

## Detail is captured redacted — never raw

Every event carries metadata (method, target, status, duration, byte counts)
plus **redacted detail** for the expandable row view: headers with
credential-bearing values masked (`authorization`, `cookie`, anything matching
token/secret/api-key/session), bodies truncated to 2 KB, and bodies on
sensitive routes suppressed entirely (Clubhouse paths/hosts — phone numbers,
SMS codes, tokens; the Clubhouse token never reaches the browser). Outbound
URLs are recorded scheme+host+path only (query/fragment stripped). Response
bodies are only captured on `client` events: inbound/outbound responses may
stream, and the client event for the same round-trip shows the payload anyway.
The redaction lives at the capture chokepoints (`instrument.py`, `api.ts`) —
never record a raw header or body.

## Transport

The backend keeps a 500-event ring buffer (`recorder.py`) and streams events to
the frontend over the **shared `/ws` socket** on the `telemetry` channel — the
first real use of that socket. On connect it replays the recent backlog, then
pushes live. `GET /api/telemetry/recent` exposes the same backlog for polling.
The frontend store (`telemetry.ts`) merges `client` events with the streamed
`inbound`/`outbound` events into one capped list.

## Contributions to the layout shell

- **Panels:** `observability.logs` (singleton, `defaultPlacement: bottom`) — the
  full I/O table (time, source badge, method, target, status, ms, size) with a
  Clear button. Rows with captured detail show a caret and expand on click to
  the redacted headers/bodies.
- **Dashboard widgets:** `observability.io` ("Data flow") — compact summary
  (call/error counts + last few, expandable the same way). **Not in the default
  layout** — this is the "optional" part; add it from the dashboard picker.
- **Commands:** `observability.open` (opens the panel).

## Backend surface

`backend/modules/telemetry/` — `GET /api/telemetry/recent` (backlog) and the
`/ws` telemetry stream. The telemetry endpoints are excluded from recording to
avoid feedback noise.

## Browser vs desktop

Identical. The store also captures real client-side round-trip latency, which
reflects wherever the backend lives (local or remote).

## Not yet

Filtering/search, client-side streaming calls (the agent chat/pull streams
bypass `request<T>`, so they show as outbound on the backend but not as client
events), inbound/outbound response bodies (streaming — see above), and
persistence across reloads (the buffer is in-memory).
