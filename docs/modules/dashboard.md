# Module: dashboard / widgets

The "one-stop" landing surface: a grid of composable widgets showing status at a
glance (recent agent sessions, tasks, feeds, metrics).

**Status: implemented** — frontend in `packages/core/src/modules/dashboard/`,
backend in `backend/modules/dashboard/`. Default widgets: `dashboard.welcome`
and `dashboard.backendStatus` (polls `/api/health` every 10s); other modules
contribute addable widgets (`clubhouse.account`, `observability.io`). Layout
(ordered
widget ids) is persisted via `GET/PUT /api/dashboard/layout` to
`$HORRIBLE_DATA_DIR/dashboard-layout.json` (default `.data/`). Widgets are
added from a toolbar picker and removed per-widget; capability-gated widgets are
filtered by the capability service before display.

The dashboard panel (`dashboard.home`) is a **singleton** window in the workspace
(opening it again focuses the existing one). Its widget grid is the inner,
second level of the two-level model — see
[../architecture/windowing.md](../architecture/windowing.md).

## Contributions to the layout shell

- **Panels:** `dashboard.home` (widget grid, default: the initial center panel on
  a fresh workspace).
- **Commands:** `dashboard.open`, `dashboard.addWidget`, `dashboard.editLayout`.

## The widget contract

The dashboard is itself extended by other modules: a **widget** is a registry
contribution (`id`, `title`, React component, optional refresh interval,
optional `requiredCapabilities`). The dashboard module owns the grid, layout
persistence, and the add-widget picker; it knows nothing about individual
widgets' internals. This is the same inversion as panels-in-the-shell, one level
down.

Widget data comes from each owning module's own backend routes; the dashboard
module's backend surface (`backend/modules/dashboard/`) only stores grid layout
per user/profile.

## Browser vs desktop

The grid behaves identically in both layouts. Differences come from individual
widgets' capability requirements:

| Concern                                                                            | Browser                                    | Desktop   |
| ---------------------------------------------------------------------------------- | ------------------------------------------ | --------- |
| Capability-gated widgets (e.g. a local system-metrics widget needing desktop APIs) | hidden from the picker, slot shows nothing | available |
| Backend-sourced widgets (sessions, tasks, feeds)                                   | identical                                  | identical |

A widget that is available in one layout and not the other must come from a
`requiredCapabilities` declaration — never from platform sniffing inside the
widget. When adding a capability-gated widget, list it in the table above.
