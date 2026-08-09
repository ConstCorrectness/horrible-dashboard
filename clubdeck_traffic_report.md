# Clubdeck 2.6.5 — local traffic analysis

**Capture date:** 2026-08-09  
**Target:** locally installed `Clubdeck.exe` (v2.6.5)  
**Scope:** user-operated login, room join, hand raise, leave actions. Authentication values, cookies, device identifiers, phone numbers, and message contents were deliberately excluded from this report.

## Result and confidence

The app bundle identifies its REST base URL as:

```text
https://www.clubhouseapi.com/api
```

The installed client contains these route literals:

| User action | Likely REST request | Evidence | Confidence |
|---|---|---|---|
| Start phone login | `POST /start_phone_number_auth` | bundled route literal | High |
| Complete phone login | `POST /complete_phone_number_auth` | bundled route literal | High |
| Resend phone login code | `POST /resend_phone_number_auth` | bundled route literal | High |
| Join a room/channel | `POST /join_channel` | bundled route literal | High |
| Leave a room/channel | `POST /leave_channel` | bundled route literal | High |
| Raise/lower hand (audience response) | `POST /audience_reply` | bundled route literal and `audienceReply` client action | High |
| Change room hand-raise policy (moderator control; not a normal attendee raise) | `POST /change_handraise_settings` | bundled route literal | High |
| Inspect current hand queue | `POST /get_handraise_queue` | bundled route literal | High |
| Become speaker | `POST /become_speaker` | bundled route literal | High |

The capture did **not** expose live REST request paths for the actions. It captured only Clubdeck updater requests and profile-image CDN traffic. This is because part of Clubdeck's Node/Electron networking bypassed the renderer proxy setting.

## Direct realtime connection observed

During the exercised session, a Clubdeck process held this direct TLS connection:

```text
10.0.0.18:<ephemeral> -> 54.175.191.203:443
```

Verification of that server's TLS certificate showed SANs for `*.pubnub.com`, `*.pubnubapi.com`, `*.pubnub.net`, `*.pndsn.com`, and `*.pubnub.co`. The Clubdeck bundle also contains the configured URL:

```text
https://clubhouse.pubnub.com
```

This is a realtime transport connection; it is distinct from the REST base above. Its request/message content was not decrypted by this capture.

## What was actually intercepted

* `GET https://www.clubdeck.app/release/latest.yml` — 200
* `GET https://www.clubdeck.app/release/notice.json` — 200
* 241 CDN/profile-image requests to `d14u0p1qkech25.cloudfront.net`

No Clubhouse REST or WebSocket/PubNub content is represented as a captured API call in the mitm file.

## Files

* Raw sensitive mitmproxy capture: `clubdeck_traffic.mitm`
* Redacted endpoint inventory: `endpoint_inventory_clean.json`
* Static action-path list: `static_relevant_endpoints.txt`
* This report: `clubdeck_traffic_report.md`

All files are under `C:\Users\Horrible\random_scripts\clubdeck_capture\`.

## Recommended next collection pass

To correlate each interaction to exact HTTP method, headers, body field names, and response schema, capture the Electron **main-process** connection rather than only its renderer proxy. A temporary system-level/process-level redirect or an instrumented copy of the client must be used; then repeat one action at a time and redact session credentials before sharing results.


