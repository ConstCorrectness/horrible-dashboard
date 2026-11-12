/**
 * @horrible/kernel — the host surface a module package may import.
 *
 * A module under `modules/<id>/web` imports from exactly three places: this
 * package, `@horribledashboard/sdk`, and its own files. Not `@horrible/core`
 * internals, and never another module. That rule is what lets a module move to
 * its own repository (and come back as a git submodule) without a code change,
 * and it is enforced by ESLint on `modules/*\/web/**`.
 *
 * Anything added here becomes a contract: keep it small, and prefer a service
 * contract (`@horribledashboard/sdk` `ServiceContracts`) for anything another
 * *module* owns. See docs/architecture/modules.mdx.
 */

// Declaring a module.
export {
  defineModule,
  lazyPane,
  type ModuleContext,
  type ModuleDefinition,
  type ModuleManifest,
} from '@horrible/core/kernel-surface';

// Talking to the backend.
export { ApiError, apiDelete, apiGet, apiPost, apiPut, apiUrl } from '@horrible/core/kernel-surface';
export { sendChannel, subscribeChannel, type WsMessage } from '@horrible/core/kernel-surface';

// Settings, toasts, panes, the agent.
export { getSetting, setSetting, useSetting, type SettingValue } from '@horrible/core/kernel-surface';
export { toastsStore, type Toast } from '@horrible/core/kernel-surface';
export { registry, type OpenPaneOptions } from '@horrible/core/kernel-surface';
export { useAgentContext } from '@horrible/core/kernel-surface';

// Contract types modules declare against.
export type {
  AgentToolDecl,
  CommandDecl,
  PanelDecl,
  SettingDecl,
  WidgetDecl,
  MixerStripDecl,
  ServiceContracts,
  ServiceKey,
} from '@horribledashboard/sdk';
