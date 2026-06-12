/**
 * Populates `window.__HORRIBLE_RUNTIME__` with the host's React instance and
 * SDK runtime. Plugin bundles externalize `react`/`react/jsx-runtime`/
 * `@horrible/sdk` to shim modules (apps/web/public/plugin-runtime/) that
 * re-export from this global — guaranteeing a single React across host and
 * plugins. Must run before any plugin entry is imported.
 */
import * as React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';

import { definePlugin, SDK_API_VERSION } from '@horrible/sdk';

interface HorribleRuntime {
  react: typeof React;
  jsxRuntime: typeof jsxRuntime;
  sdk: {
    SDK_API_VERSION: typeof SDK_API_VERSION;
    definePlugin: typeof definePlugin;
  };
}

declare global {
  interface Window {
    __HORRIBLE_RUNTIME__?: HorribleRuntime;
  }
}

export function installPluginRuntime(): void {
  window.__HORRIBLE_RUNTIME__ = {
    react: React,
    jsxRuntime,
    sdk: { SDK_API_VERSION, definePlugin },
  };
}
