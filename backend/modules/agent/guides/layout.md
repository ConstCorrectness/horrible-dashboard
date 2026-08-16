# Arranging the frame

The screen is a **frame**: a center grid of **areas** (recursively split rectangles), three tool **docks** (left/right/bottom), and per-pane **region** strips down a pane's own sides.

You only need these verbs to _rearrange_ things. To simply put something on screen, use `show` — it opens or focuses by name and returns the pane's contents in one call.

## Ids: the mistake to avoid

There are two kinds and they are not interchangeable.

- A **view id** (`editor.buffer`, `terminal.instance`) names a _kind_ of pane. `open_pane` takes one.
- An **instanceId** (`editor.buffer#3`) names one _live_ pane. Every geometry verb takes one of these, or an `areaId`.

Ids are **not guessable**. Call `get_layout` (the whole frame: areas, docks, regions, windows, and the desktop's mode and backdrop) or `list_open_panes` first and use the ids it returns. If the user names a pane by title or filename ("pane:main.py"), match it against `list_open_panes` to find its instanceId.

## Placing a pane somewhere specific

To put a view **beside/below/next to** an existing pane, call `split_area` on that pane's instanceId with `viewId` set to the new view. One step, and still the best way when the pane isn't open yet.

If it _is_ already open somewhere else, `move_pane` with `edge` moves it and splits the destination in one call — so a pane opened into the wrong place can now be fixed. Without `edge`, `move_pane` tabs into an existing area or steps to an adjacent one, as before.

Both accept `left`/`right`/`above`/`below`, and also `vertical` (side by side) and `horizontal` (stacked).

## Windows and the desktop

A desktop is either **tiling** (the frame above: areas, docks, regions) or **floating** (free windows over a backdrop). `get_layout` reports which under `desktop.mode`, along with the open `windows`.

- `open_window` pops a pane out into a window; `snap` places it in a screen region, `rect` gives exact pixels.
- `window_state` handles `minimize` / `maximize` / `restore` / `snap` / `move_to_desktop`. **Minimizing is not closing** — the pane keeps running and the user gets it back from the taskbar.
- `dock_window` puts it back into the frame; `arrange_windows` lays all of them out at once.

Windows exist in _both_ modes: on a tiling desktop a window is the escape hatch for a pane that shouldn't participate in the tiling.

Changing how it **looks** — the backdrop, the theme, the taskbar, the tiling/floating mode — lives in the separate `desktop` tool group. Call `load_tools("desktop")` first; those verbs aren't loaded by default because restyling is rare and every always-on tool costs schema space on every turn.

## Roles decide where a pane lands by default

- `document` — tabs into a center area (editor buffers, notebooks, consoles)
- `widget` — takes a center area of its own
- `tool` — lives in a dock; `open_tool_in_dock` puts it there

`open_pane` routes any view correctly by role, so prefer it unless you specifically want a dock. A view is not a workspace — don't treat one as the other.

## Regions

Regions are the toggleable strips _inside_ a pane (the editor's Outline on its right strip). `toggle_region {instanceId, position}` opens or closes a position; `set_region_view {instanceId, viewId}` chooses which strip view is showing. `list_available_panes` reports which regions each view hosts.

A region is usually the right home for a narrow companion — it follows its host rather than competing with it for space.

## Workspaces

`list_workspaces` / `create_workspace` / `switch_workspace` change the _entire_ frame at once. Only touch them when the user explicitly talks about workspaces or tabs; switching one because a pane was hard to place is almost always wrong.
