# Module: clubhouse

Clubdeck-style onboarding for a Clubhouse account: a dashboard widget walks
through phone number → SMS verification code → connected profile.

**Status: implemented** — frontend in `packages/core/src/modules/clubhouse/`,
backend in `backend/modules/clubhouse/`.

## The unofficial-API caveat

Clubhouse has no public API. Like Clubdeck, the backend speaks the
reverse-engineered mobile client protocol (`www.clubhouseapi.com/api`,
overridable via `HORRIBLE_CLUBHOUSE_API`), sending mobile-client headers and a
stable per-install `CH-DeviceId`. Clubhouse may change or rate-limit these
endpoints at any time, and third-party clients are tolerated rather than
sanctioned — the client-version constants in `routes.py` may need bumping if
requests start failing.

## Contributions to the layout shell

- **Dashboard widgets:** `clubhouse.account` (in the default grid) — the
  onboarding/connection card.
- **Commands:** `clubhouse.connect` (opens the dashboard, where the widget
  lives).
- **Panels:** none yet — a room/hallway panel would be the natural next step.

## Backend surface

`backend/modules/clubhouse/` proxies all Clubhouse traffic so the browser never
talks to Clubhouse directly (no CORS issues, no token in the page):

- `GET  /api/clubhouse/status` — `{connected, user_id, username, name,
photo_url}`. **Never includes the token.**
- `POST /api/clubhouse/auth/start` — `{phone_number}` (E.164) → triggers the
  SMS. Sends a real text; don't poke it casually.
- `POST /api/clubhouse/auth/complete` — `{phone_number, verification_code}` →
  persists the session, returns status. 400 on a wrong/expired code.
- `DELETE /api/clubhouse/auth` — disconnect (deletes the stored session).

The session (auth token, refresh token, profile) is stored server-side in
`$HORRIBLE_DATA_DIR/clubhouse-auth.json`. Treat that file as a credential.

## Browser vs desktop

Identical in both layouts — everything runs through the backend. As with the
terminal: against a remote backend, the _backend host_ holds the credential.
