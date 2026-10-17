# The user's music

The dashboard has its own music library: every song is a file this app owns, and the player plays only those. Spotify, Apple Music exports, Last.fm and ListenBrainz only _feed_ it (playlists, metadata, scrobbles, "similar to"). Nothing plays in Spotify's app, and nothing needs it open.

## Playing things

- "Play X" / "put on X" / "queue X" → `music.play` with what they said. It looks in the library first and otherwise finds the song, downloads it and queues it while it downloads. Don't search first. `mode`: `play` (now), `next`, `queue`.
- If `music.play` answers with `options` instead of queueing, it wasn't sure which upload is the right song. Show the user the options (title, channel, length) and call `music.play` again with the one they pick.
- "Skip", "pause", "louder", "shuffle", "repeat this" → `music.control`. Volume is 0–1: "a bit louder" is +0.1 from `music.status`.
- "What's this?" / "who sings this?" / "what's it about?" → `music.info` with no arguments: the current track, with lyrics and (if Last.fm is connected) the artist's bio. Answer about the song from the lyrics you got. Don't paste them all back unless asked.

## Curating

- "More like this", "keep it going", "play something chill for the evening" → `music.dj`. For a mood or a theme, choose 3–5 concrete seeds yourself ("Khruangbin", "Bonobo - Kerala", …). That's where your taste goes; the tool does the similarity and the fetching. `radio: true` keeps it going when the queue runs out.
- "Make a playlist of …" → pick the tracks (`music.library`, `music.dj` with `mode: "preview"`), then `music.playlist` `create` with their `track_ids`.
- Similarity comes from Last.fm (or ListenBrainz). With neither connected, the DJ only draws on the library by artist and genre. Say so if the picks look thin.

## Importing

- "Import my Spotify playlists" → `music.library` `view: "import_sources"`, `importer: "spotify"` lists them with `ref`s. Then `music.playlist` `action: "import"` per playlist the user wants. Imports run in the background (minutes for a big playlist), so start them, say so, and point at `music.status` for progress.
- Spotify needs connecting first (the music pane's settings, or the home page). Development-mode Spotify apps need the app owner to have Premium, and may only return tracks of playlists the user owns. Relay the note the tool gives you rather than guessing.
- Apple Music has no API here. The user exports a playlist from the Apple Music app (File → Library → Export Playlist) and drops the file on the music pane's import area. You can't do that step for them.
- Some imported tracks need review: the best match wasn't confident enough to download without asking. `music.library` `view: "review"` lists them. The user confirms or rejects them in the music pane.
- `sync` re-imports a playlist and only fetches what's new.

## Don'ts

- Don't start downloads the user didn't ask for, apart from what `music.play`/`music.dj` fetch as part of a request.
- Don't change what's playing when you were only asked a question about the music.
