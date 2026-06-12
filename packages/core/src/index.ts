export {
  BROWSER_CAPABILITIES,
  DESKTOP_CAPABILITIES,
  hasCapability,
  initCapabilities,
  type Capability,
} from './capabilities';
export { ApiError, apiDelete, apiGet, apiPost, apiPut } from './api';
export { apiUrl, getBackendOrigin, initBackendOrigin, wsUrl } from './origin';
export {
  loadPlugins,
  pluginLoadErrors,
  type InstalledPlugin,
  type InstalledPluginList,
  type PluginLoadError,
} from './plugins/loader';
export { getWorkspaceLayout, saveWorkspaceLayout, type SerializedLayout } from './workspace';
export { subscribeChannel, type WsMessage } from './ws';
export { recordClientIo, telemetryStore, type IoEvent, type IoSource } from './telemetry';
export {
  registry,
  type CommandDecl,
  type KeybindingDecl,
  type ModuleManifest,
  type PanelDecl,
  type ShellView,
  type WidgetDecl,
} from './registry';
export { dashboardModule } from './modules/dashboard';
export { scratchModule } from './modules/scratch';
export {
  clubhouseModule,
  completeClubhouseAuth,
  disconnectClubhouse,
  getClubhouseStatus,
  startClubhouseAuth,
  type ClubhouseStatus,
} from './modules/clubhouse';
export { observabilityModule } from './modules/observability';
export { marketplaceModule } from './modules/marketplace';
export {
  agentModule,
  DEFAULT_AGENT_MODEL,
  getAgentStatus,
  pullAgentModel,
  saveAgentConfig,
  streamAgentChat,
  type AgentStatus,
  type PullProgress,
} from './modules/agent';
