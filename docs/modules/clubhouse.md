# Module: clubhouse

Clubdeck-style onboarding for a Clubhouse account: a dashboard widget walks
through phone number → SMS verification code → connected profile, **or** connects
directly with an existing auth token.

**Status: implemented** — frontend in `packages/core/src/modules/clubhouse/`,
backend in `backend/modules/clubhouse/`. Token-connect is verified end to end
against the live API; phone-auth depends on Clubhouse's gate (see below).

## The unofficial-API caveat

Clubhouse has no public API. Like Clubdeck, the backend speaks the
reverse-engineered mobile client protocol (`www.clubhouseapi.com/api`,
overridable via `HORRIBLE_CLUBHOUSE_API`), sending mobile-client headers and a
stable per-install `CH-DeviceId`.

**Client-version headers matter.** Clubhouse validates `CH-AppBuild` /
`CH-AppVersion` / `User-Agent` and rejects stale clients with
`login did not pass token validation` (HTTP 400). `routes.py` pins a current
build (3375 / 24.01.02, iOS UA); if requests start failing with that error
again, the build is stale — bump it from a current client.

**Phone auth is gated.** `start_phone_number_auth` enforces an anti-abuse check;
with current headers it may work, but Clubhouse can block it. The reliable path
is **token-connect**: supply an existing `auth_token` + `user_id` (e.g. captured
from another logged-in client), which the backend validates against the
authenticated `/me` endpoint. The token may be bound to the device it was issued
for, so an optional `device_id` is passed through on the authed headers.

## Contributions to the layout shell

- **Dashboard widgets:** `clubhouse.account` (in the default grid) — the
  onboarding/connection card.
- **Panels:** `clubhouse.rooms` (singleton) — the **Live rooms** browse panel
  (Phase 1): lists currently-live channels for the connected account, read-only.
- **Commands:** `clubhouse.connect` (opens the dashboard widget),
  `clubhouse.rooms` (opens the Live rooms panel).

## Backend surface

`backend/modules/clubhouse/` proxies all Clubhouse traffic so the browser never
talks to Clubhouse directly (no CORS issues, no token in the page):

- `GET  /api/clubhouse/status` — `{connected, user_id, username, name,
photo_url}`. **Never includes the token.**
- `POST /api/clubhouse/auth/start` — `{phone_number}` (E.164) → triggers the
  SMS. Sends a real text; don't poke it casually.
- `POST /api/clubhouse/auth/complete` — `{phone_number, verification_code}` →
  persists the session, returns status. 400 on a wrong/expired code.
- `POST /api/clubhouse/auth/token` — `{auth_token, user_id, device_id?}` →
  validates the token against `/me`, persists the session, returns status.
- `GET  /api/clubhouse/channels` — live rooms (proxies Clubhouse
  `GET /get_channels`); `409` if not connected.
- `GET  /api/clubhouse/following` — accounts the user follows (proxies
  `POST /get_following`); `409` if not connected.
- `DELETE /api/clubhouse/auth` — disconnect (deletes the stored session).

Authed calls go through `_ch_authed_get` / `_ch_authed_post`, which attach
`Authorization: Token <token>` + `CH-UserID` to the client headers. Browse
responses are projected into lean Pydantic models (extra fields dropped) rather
than echoing Clubhouse's full payloads.

Clubhouse error messages (e.g. the validation gate) are surfaced through the
API client's thrown `Error` so the widget shows the real reason, not a bare 400.

The session (auth token, refresh token, profile) is stored server-side in
`$HORRIBLE_DATA_DIR/clubhouse-auth.json`. Treat that file as a credential.

## Browser vs desktop

Identical in both layouts — everything runs through the backend. As with the
terminal: against a remote backend, the _backend host_ holds the credential.
