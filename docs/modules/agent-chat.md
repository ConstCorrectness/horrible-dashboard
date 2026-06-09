# Module: agent chat cockpit

The core "agentic era" surface: converse with agents/LLMs, watch their tool calls
stream, and manage sessions.

## Contributions to the layout shell

- **Panels:** `chat.conversation` (main chat view, default: center tab group),
  `chat.sessions` (session list, default: left dock), `chat.inspector`
  (tool-call/trace detail, default: right dock, opened on demand).
- **Commands:** `chat.new`, `chat.focusInput`, `chat.openSession`,
  `chat.toggleInspector`, `chat.stopGeneration`.
- **Default keybindings:** declared for new-session and focus-input; bound through
  the shell keybinding service.
- **Dashboard widgets:** `chat.recentSessions` (see [dashboard.md](dashboard.md)).

## Backend surface

`backend/modules/chat/` — session CRUD over HTTP; streaming (tokens, tool-call
events, status) over the shared WebSocket on `chat.*` channels. Agent execution
lives entirely in the backend; the frontend only renders the stream and sends
user input. Pydantic models define the event schema.

## Browser vs desktop

The conversation experience is identical — it's all backend traffic over the
shared socket. Differences are notification/summon ergonomics only:

| Concern                  | Browser                              | Desktop                               |
| ------------------------ | ------------------------------------ | ------------------------------------- |
| Completion notifications | Web Notifications (`notifications.system` capability, tab must allow it) | OS notification, click focuses window |
| Quick summon             | none                                 | global shortcut raises the window and runs `chat.focusInput` |
| Long-running agents      | keep tab open (no background work in the page; the backend keeps running regardless) | window can be closed to tray; stream resumes on reopen |

Both layouts must tolerate socket reconnects mid-generation: the backend is the
source of truth for session state, and the panel re-syncs on reconnect.
