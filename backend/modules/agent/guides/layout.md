# Arranging the desktop

The screen is a **desktop**: free-floating **windows** over a backdrop, with a taskbar. Every pane lives in a window; a window can hold several panes as **tabs**. Inside a pane, **region** strips run down its own sides.

You only need these verbs to _rearrange_ things. To simply put something on screen, use `show` — it opens or focuses by name and returns the pane's contents in one call.

## Ids: the mistake to avoid

There are two kinds and they are not interchangeable.

- A **view id** (`editor.buffer`, `terminal.instance`) names a _kind_ of pane. `open_pane` takes one.
- An **instanceId** (`editor.buffer#3`) names one _live_ pane. Every window verb takes one of these.

Ids are **not guessable**. Call `get_layout` (every window with its rect, mode and tabs, plus the desktop's backdrop) or `list_open_panes` first and use the ids it returns. If the user names a pane by title or filename ("pane:main.py"), match it against `list_open_panes` to find its instanceId.

## Placing a window

`open_pane` opens a view in a new window — where that view was last left, or on a cascade. Then place it:

- `window_state` with `snap` puts it in a screen region: `left`/`right`/`top`/`bottom` halves, `tl`/`tr`/`bl`/`br` quarters, or `max`. To put two panes **side by side**, snap one `left` and the other `right`.
- `window_state` also handles `minimize` / `maximize` / `restore`, and `present` / `unpresent` (fill the whole screen, past the taskbar). **Minimizing is not closing** — the pane keeps running and the user gets it back from the taskbar.
- `open_window` gives a pane a window of its own — tearing it out of a tabbed window if it shares one — and takes the same `snap`, or `rect` for exact pixels.
- `arrange_windows` lays every window out at once: `grid`, `cascade`, `columns` or `rows`.

Changing how it **looks** — the backdrop, the theme, the taskbar — lives in the separate `desktop` tool group. Call `load_tools("desktop")` first; those verbs aren't loaded by default because restyling is rare and every always-on tool costs schema space on every turn.

## Regions

Regions are the toggleable strips _inside_ a pane (the editor's Outline on its right strip). `toggle_region {instanceId, position}` opens or closes a position; `set_region_view {instanceId, viewId}` chooses which strip view is showing. `list_available_panes` reports which regions each view hosts.

A region is usually the right home for a narrow companion — it follows its host rather than competing with it for space.
