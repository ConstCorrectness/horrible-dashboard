# Module: terminal

Embedded terminals for working on a machine from inside the app.

## Contributions to the layout shell

- **Panels:** `terminal.instance` (one per terminal, default: bottom dock tab
  group).
- **Commands:** `terminal.new`, `terminal.kill`, `terminal.clear`,
  `terminal.focusNext`/`focusPrev`.
- **Services for other modules:** `terminal.runCommand(cmd, opts)` lets modules
  (e.g. agent chat showing a suggested command) open a terminal pre-filled or
  running — always visibly, never hidden execution.

## Backend surface

`backend/modules/terminal/` owns the PTYs. The frontend is xterm.js rendering a
`terminal.<id>` channel on the shared WebSocket; resize/input go up, output
streams down. **The PTY always runs where the backend runs** — there is no
client-side shell in either layout. This keeps browser and desktop byte-for-byte
identical and gives agents and humans the same terminals.

## Browser vs desktop

| Concern            | Browser                                            | Desktop                            |
| ------------------ | -------------------------------------------------- | ---------------------------------- |
| Where the shell runs | on the backend host (local dev: your machine; remote backend: the server — make this visible in the panel title) | localhost backend = your machine   |
| Shell defaults     | backend host's default shell                       | same (PowerShell on Windows)       |
| Copy/paste, scrollback, themes | identical (xterm.js)                   | identical                          |
| Keybinding capture | browser reserves some chords (e.g. Ctrl+W) — the shell keybinding service must not bind those for terminal focus | full capture available |

Security note: exposing the backend beyond localhost exposes shell execution on
that host. Any future remote-access story must address auth at the backend
boundary, not in this module.
