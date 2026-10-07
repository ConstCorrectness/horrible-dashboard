# Security disclosure — Clubhouse room reactions render unvalidated text

**To:** Clubhouse security team
**From:** Horrible Dashboard (third-party Clubhouse client)
**Date:** 2026-10-07
**Severity (our assessment):** Medium — abuse / social-engineering vector, no account compromise observed

---

## Summary

The room reaction feature accepts an arbitrary string in the reaction's
`emoji` field and renders it to every participant without validating that the
value is actually an emoji. A crafted reaction causes long, arbitrary text to
burst across the screens of everyone in the room, using the normal reaction
animation. We have observed this in the wild: a reaction carrying placeholder
"leaked data" text (`[DEMO] Leaked … IBAN:… CVV:… SSN:…`) sprayed as white text
over a live room, which participants reasonably mistook for an account breach.

No data is actually exposed by this — the text is attacker-supplied and fake —
but it is an effective scare / impersonation / spam vector because it looks like
a system-level leak and originates from a real participant's tile.

## Root cause

Two layers each miss a check:

1. **Server — reaction send endpoint.** The reaction submission
   (`POST /emoji_reaction`, `{channel, emoji}`) does not appear to validate that
   `emoji` is a member of the room's advertised `emoji_reaction_options`, nor
   that it is an emoji at all. Any string in `emoji` is accepted and fanned out
   to the room's realtime feed as a `new_channel_reaction` event.

2. **Client — reaction renderer.** The official client draws whatever string
   arrives in the reaction event as if it were an emoji glyph, with no length
   cap and no emoji check. A text string therefore renders as text, repeated and
   rotated by the burst animation, anchored on the sender's tile.

The advertised `emoji_reaction_options` list is enforced only by the sending
client's own picker UI, so a request made outside that UI bypasses it entirely.

## Impact

- **Scare / social engineering:** fake "leaked credentials" text that looks like
  a breach, as observed.
- **Impersonation / harassment / spam:** arbitrary text attributed to the
  sender's avatar, shown to all participants, repeatable.
- **Client stress:** because the client caps neither length nor (as far as we
  can tell) rate, large or rapid payloads may degrade the room UI on
  participants' devices.

We did **not** observe any account takeover, data exfiltration, or privilege
escalation. The "leaked" content is fabricated by the attacker.

## Reproduction (mechanism, not a weaponized payload)

We are deliberately not including a ready-to-run exploit request. The mechanism:

1. A reaction event of the normal shape reaches the room feed:
   `{ action: "new_channel_reaction", action_user_profile: {…}, channel, message_id, <content> }`.
2. The `<content>` carries a non-emoji string instead of one of the room's
   `emoji_reaction_options`.
3. Every participant's client renders that string via the reaction burst.

We can reproduce the **client rendering half** entirely offline, with no network
and no live room, by feeding a crafted event into a reaction parser — confirming
that the string is drawn verbatim. We are happy to share that in-process test
privately on request.

## Suggested remediation

- **Server:** reject a reaction whose `emoji` is not in that room's
  `emoji_reaction_options` (or, at minimum, is not a short emoji-only string).
  This is the single highest-value fix, since it stops the fan-out at the source.
- **Client:** treat the reaction field as untrusted — cap its length, require it
  to be an emoji before rendering, and rate-limit bursts per sender.
- **Defense in depth:** apply the same validation on any other field a client
  renders from a realtime event.

## What we changed on our side

Our client is third-party and read-mostly, but we hardened both directions:

- We validate that any reaction we send is a genuine emoji, so our tooling
  cannot be used to emit these text payloads to a live room.
- We cap and escape incoming reaction text before rendering, so a hostile
  payload cannot grow our UI unbounded or be interpreted as markup.

## Contact

Happy to provide the offline reproduction test, wire captures of the observed
event, or any other detail that helps. Please let us know your preferred
disclosure channel and timeline.
