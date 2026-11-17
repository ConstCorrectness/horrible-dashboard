# Arranging the workbench

The screen is a **workbench**. Every pane is a tab in a **group**. Groups live in the **centre** (a split layout of side-by-side and stacked groups, where documents go) or in one of three edge **docks** (`left`, `right`, `bottom`, where tools go). A dock can be hidden and shown; the panes in it keep running. Inside a pane, **region** strips run down its own sides.

You only need these verbs to _rearrange_ things. To simply put something on screen, use `show`: it opens or focuses by name and returns the pane's contents in one call.

## Ids: the mistake to avoid

There are three kinds and they are not interchangeable.

- A **view id** (`editor.buffer`, `terminal.instance`) names a _kind_ of pane. `open_pane` takes one.
- An **instanceId** (`editor.buffer#3`) names one _live_ pane. Every arrangement verb takes one.
- A **group id** (`a12`) names one tab group. `place_pane` takes one for `group` and the splits.

Ids are **not guessable**. Call `get_layout` (the centre tree with group ids and tabs, the docks, what is maximized and focused) or `list_open_panes` first and use the ids it returns. If the user names a pane by title or filename ("pane:main.py"), match it against `list_open_panes` to find its instanceId.

## Placing a pane

`open_pane` opens a view where it belongs: a document as a tab of the focused centre group, a tool in its edge dock (or wherever the user last put that view). Then move it:

- `place_pane` with `to`:
  - `split-left` / `split-right` / `split-above` / `split-below`: a new centre group on that side. To put two panes **side by side**, place the second one `split-right`.
  - `dock-left` / `dock-right` / `dock-bottom`: into that dock, which is then shown.
  - `center`: back into the focused centre group as a tab.
  - `group` with `group: <id>`: as a tab of that group.
- `pane_state` with `state`: `maximize` / `restore` (one group over the whole workbench), `hide` / `show` (put a dock away and bring it back; **hiding is not closing**, and only docks hide), and `present` / `unpresent` (fill the entire screen, past the status bar).
- `arrange_panes` re-lays the whole centre: `columns`, `rows`, `grid` (a group per pane), `tabs` (all in one group) or `even` (keep the layout, equalise the splits).

Changing how it **looks** (the backdrop, the theme, the taskbar) lives in the separate `desktop` tool group. Call `load_tools("desktop")` first; those verbs aren't loaded by default because restyling is rare and every always-on tool costs schema space on every turn.

## Regions

Regions are the toggleable strips _inside_ a pane (the editor's Outline on its right strip). `toggle_region {instanceId, position}` opens or closes a position; `set_region_view {instanceId, viewId}` chooses which strip view is showing. `list_available_panes` reports which regions each view hosts.

A region is usually the right home for a narrow companion: it follows its host rather than competing with it for space.
