# karaoke

A module package: the karaoke machine (stage, queue, song search, phone remote).

| Half | Where | Entry |
| --- | --- | --- |
| Web | `web/` (`@horrible-module/karaoke`) | `web/src/index.ts` → `module` |
| Python | `py/` (`horrible-mod-karaoke`) | `horrible_mod_karaoke:MODULE` |

Imports only `@horrible/kernel`, `@horribledashboard/sdk`, `backend.kernel` and
`backend.sdk`, so the directory can move to its own repository and come back as a git
submodule unchanged. Uses the `audio.mediaStrip@1` service when the audio module is
loaded; plays straight to the system output when it is not.

Tests: `pnpm --filter @horrible-module/karaoke test` and
`uv run --no-sync pytest modules/karaoke/py/tests`.
