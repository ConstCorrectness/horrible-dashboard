# @horrible/sdk

Plugin SDK for [horrible-dashboard](https://github.com/ConstCorrectness/horrible-dashboard) —
the typed plugin contract and the Vite build preset for plugin authors.

A plugin is an ES module whose default export is `definePlugin({ setup })`. The
host calls `setup(host)` at boot; whatever you return (panels, widgets,
commands, keybindings) is registered into the shell. Plugins are trusted code
(Obsidian-style): they run unsandboxed in the app realm.

## Writing a plugin

```ts
// src/index.tsx
import { definePlugin } from '@horrible/sdk';

export default definePlugin({
  setup(host) {
    return {
      widgets: [
        {
          id: 'my-plugin.greeting',
          title: 'Hello',
          component: () => <p>Hello from a plugin!</p>,
        },
      ],
      commands: [
        {
          id: 'my-plugin.sayHello',
          title: 'My Plugin: Say hello',
          run: () => host.openPanel('dashboard.home'),
        },
      ],
    };
  },
});
```

Every contributed id must be namespaced under your plugin id
(`<pluginId>.<name>`) — the loader rejects anything else.

## Building

Use the bundled Vite preset. It marks `react`, `react/jsx-runtime`, and
`@horrible/sdk` external and rewrites them to host-served shim URLs, so every
plugin shares the host's React instance and SDK runtime. Bundling your own
React breaks hooks — don't bypass the preset.

```ts
// vite.config.ts
import { defineConfig } from 'vite';
import { horriblePluginViteConfig } from '@horrible/sdk/vite';

export default defineConfig(horriblePluginViteConfig({ entry: 'src/index.tsx' }));
```

`vite build` produces `dist/index.js` — a few kB, since everything heavy is
external. Add a `horrible-plugin.json` manifest next to it and the package is
installable from a catalog:

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "0.1.0",
  "description": "What it does.",
  "author": "you",
  "entry": "dist/index.js",
  "sdkVersion": 1,
  "requiredCapabilities": [],
  "permissions": []
}
```

## The host handle

`setup(host)` receives the only door back into the shell:

- `host.api` — backend HTTP client (`get`/`post`/`put`/`del`, relative to `/api`)
- `host.storage` — per-plugin key-value storage, persisted server-side
- `host.subscribeChannel(channel, handler)` — the shared `/ws` socket
- `host.openPanel(panelId)` / `host.runCommand(commandId)`
- `host.hasCapability(capability)`

See the
[plugin SDK architecture doc](https://github.com/ConstCorrectness/horrible-dashboard/blob/main/docs/architecture/plugin-sdk.md)
for the full design, and
[`examples/plugins/hello-widget`](https://github.com/ConstCorrectness/horrible-dashboard/tree/main/examples/plugins/hello-widget)
for a complete working plugin.
