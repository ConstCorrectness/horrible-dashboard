# Arranging the frame

The screen is a **frame**: a center grid of **areas** (recursively split rectangles), three tool **docks** (left/right/bottom), and per-pane **region** strips down a pane's own sides.

You only need these verbs to _rearrange_ things. To simply put something on screen, use `show` — it opens or focuses by name and returns the pane's contents in one call.

## Ids: the mistake to avoid

There are two kinds and they are not interchangeable.

- A **view id** (`editor.buffer`, `terminal.instance`) names a _kind_ of pane. `open_pane` takes one.
- An **instanceId** (`editor.buffer#3`) names one _live_ pane. Every geometry verb takes one of these, or an `areaId`.

Ids are **not guessable**. Call `get_layout` (the whole frame: areas, docks, regions, floating) or `list_open_panes` first and use the ids it returns. If the user names a pane by title or filename ("pane:main.py"), match it against `list_open_panes` to find its instanceId.

## Placing a pane somewhere specific

To put a view **beside/below/next to** an existing pane, call `split_area` on that pane's instanceId with `viewId` set to the new view. Do it in that one step.

Do **not** `open_pane` first and then try to move it. `move_pane` and `join_area` only work on panes and areas that are _already adjacent_ — they cannot create a new split. A pane opened into the wrong place often cannot be fixed afterwards at all.

`split_area` accepts `left`/`right`/`above`/`below`, and also `vertical` (side by side) and `horizontal` (stacked).

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
