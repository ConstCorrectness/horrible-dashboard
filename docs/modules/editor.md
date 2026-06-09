# Module: editor / notes buffers

The emacs-like editing core: markdown/text buffers with command-palette-driven
editing. Also the rendering target other modules use to open text (file explorer
opens files into editor buffers).

## Contributions to the layout shell

- **Panels:** `editor.buffer` (one panel instance per open buffer, default:
  center tab group). Buffer tabs are workspace tabs — the shell owns tab UX, the
  editor owns content.
- **Commands:** `editor.newNote`, `editor.open`, `editor.save`, `editor.saveAll`,
  plus editing commands exposed to the palette so keybindings stay rebindable.
- **Services for other modules:** `editor.openBuffer(source)` is the public way
  any module shows editable text. Sources are URIs (`note:`, `workspace-file:`,
  `osfile:` on desktop) so the buffer layer doesn't care where content lives.
- **Dashboard widgets:** `editor.recentNotes`.

## Backend surface

`backend/modules/notes/` — note storage (CRUD, search) and workspace-file
read/write. Notes are backend-owned data, identical everywhere. Autosave and
conflict handling (buffer revision vs backend revision) live behind the same
routes for both layouts.

## Browser vs desktop

Editing notes and workspace files is identical in both layouts. The difference
is **which files you can reach**:

| Concern              | Browser                                          | Desktop                                  |
| -------------------- | ------------------------------------------------ | ---------------------------------------- |
| Notes                | full                                             | full                                     |
| Workspace files      | full — read/write through the backend FS API     | full — same path                         |
| Arbitrary OS files   | no (`fs.nativeDialogs` absent; File System Access API deliberately not used for v1) | native open/save dialogs via Tauri; opened as `osfile:` buffers |
| Drag a file onto app | imports content as a new note                    | opens the file as a buffer               |

Remember the layout-shell rule: with a remote backend in the browser,
"workspace files" are files on the backend host.
