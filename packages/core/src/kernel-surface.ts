/**
 * What `@horrible/kernel` re-exports, taken from the leaf files rather than the
 * `@horrible/core` barrel.
 *
 * The barrel imports every built-in module, and some of them do work at import
 * time (the editor subscribes to `/ws` the moment it loads). A module package that
 * imported the kernel through it would drag all of core into its own tests and its
 * own bundle chunk. This file is the narrow door; keep it in step with
 * `packages/kernel/src/index.ts`.
 */
export { defineModule, type ModuleContext, type ModuleDefinition } from './module-def';
export { lazyPane } from './lazy-pane';
export { registry, type ModuleManifest, type OpenPaneOptions } from './registry';
export { ApiError, apiDelete, apiGet, apiPost, apiPut } from './api';
export { apiUrl } from './origin';
export { sendChannel, subscribeChannel, type WsMessage } from './ws';
export { getSetting, setSetting, useSetting, type SettingValue } from './settings';
export { toastsStore, type Toast } from './toasts';
export { useAgentContext } from './agent-context';
