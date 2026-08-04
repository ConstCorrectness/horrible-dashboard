# Friends and people

The roster is keyed by **person**, not by machine. One human with three devices is one row.

## Naming a person

`social.message`, `social.ask_agent` and `hassault.invite` all take a `who`, and all resolve it the same way:

- a **friend code** — `HD-XXXX-XXXX-XXXX-XXXX-XXXX`
- a bare **person id** — 16 lowercase base32 characters
- a **display name**, matched case-insensitively and only when it is unambiguous

Prefer whatever the user actually said. If two friends share a display name the resolve fails rather than guessing — re-ask the user, or call `social.list_friends` and use the friend code. Never invent a person id; they are not guessable.

`social.list_friends` returns each friend's `person_id`, `friend_code`, presence, and their devices. It also returns `you`, your own friend code — that is what to hand someone who wants to add you.

## Presence

Presence is derived from live connections, not stored. "Offline" means no device of theirs is reachable right now, not that they are gone. Messaging an offline friend fails; say so plainly rather than retrying.

## Asking a friend's agent

`social.ask_agent` sends a question to **another person's** agent, on their machine. It is gated on their side and read-only by default, so a request to change something there will usually be refused — that is the remote node's decision, not an error to work around.

It is a slow call (a whole remote turn). Never poll it, and don't fan it out across several friends for one question.

## What this group is not

Peer _machines_, transports, and dialling addresses are infrastructure, not this group. If the user asks "who is online", they mean people — answer from `social.list_friends`.
