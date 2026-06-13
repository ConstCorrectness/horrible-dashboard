# Module: marketplace

Browse, install, and manage plugins built against `@horribledashboard/sdk` — the
storefront for the public plugin contract documented in
[architecture/plugin-sdk.md](../architecture/plugin-sdk.md).

**Status: implemented** — frontend in `packages/core/src/modules/marketplace/`,
backend in `backend/modules/plugins/`. The repo ships one sample plugin
(`examples/plugins/hello-widget/`) that the default catalog serves.

## Contributions to the layout shell

- **Panels:** `marketplace.home` (singleton, center) — catalog + installed
  plugins, with Install / Update / Enable / Disable / Uninstall actions and any
  plugin load errors from the current boot.
- **Commands:** `marketplace.open`.
- **Widgets / keybindings:** none.

Lifecycle changes apply **on reload** (the module registry has no unregister);
the panel shows a "Reload now" banner after any change.

## Backend surface

`backend/modules/plugins/` owns the plugin lifecycle. Installed plugins live
under `$HORRIBLE_DATA_DIR/plugins/<id>/` (`package/` copy, `state.json`,
`storage.json`); the catalog is a directory of plugin packages
(`HORRIBLE_PLUGIN_CATALOG`, default `examples/plugins`).

- `GET    /api/plugins/catalog` — manifests of every valid package in the
  catalog dir (invalid ones are skipped and logged).
- `GET    /api/plugins/installed` — installed manifests + enabled state.
- `POST   /api/plugins/install` — `{id}`; copies the package from the catalog
  (reinstall = update). 404 if not in the catalog.
- `DELETE /api/plugins/{id}` — uninstall; removes the plugin dir **including
  its storage**.
- `PUT    /api/plugins/{id}/enabled` — `{enabled}`.
- `GET    /api/plugins/{id}/assets/{path}` — serves package files (the entry
  module at boot). Path-traversal-guarded; `.js`/`.mjs` forced to
  `text/javascript` (Windows MIME registry workaround).
- `GET/PUT/DELETE /api/plugins/{id}/storage/{key}` — the plugin-scoped
  key-value service exposed to plugins as `host.storage`.

Plugin ids are validated against `^[a-z0-9][a-z0-9-]{0,63}$` everywhere (which
also blocks traversal), and a package manifest's `id` must match its directory
name.

## Browser vs desktop

Identical in both layouts — plugins are part of the shared frontend, and all
lifecycle state lives where the backend runs. Plugins declare
`requiredCapabilities` like built-in widgets do, and should use
`host.hasCapability()` to degrade gracefully.
