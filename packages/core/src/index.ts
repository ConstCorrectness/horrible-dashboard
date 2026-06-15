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
export {
  createWorkspace,
  deleteWorkspace,
  getWorkspaces,
  saveWorkspace,
  setActiveWorkspace,
  type SerializedLayout,
  type Workspace,
  type WorkspacesState,
} from './workspace';
export { subscribeChannel, type WsMessage } from './ws';
export { recordClientIo, telemetryStore, type IoEvent, type IoSource } from './telemetry';
export {
  getSetting,
  isSettingOverridden,
  loadSettings,
  resetSetting,
  setSetting,
  settingsStore,
  useSetting,
  type SettingValue,
} from './settings';
export {
  registry,
  type CommandDecl,
  type KeybindingDecl,
  type LayoutController,
  type ModuleManifest,
  type OpenPaneInfo,
  type PanelDecl,
  type SettingDecl,
  type SettingType,
  type ShellView,
  type WidgetDecl,
  type WorkspaceInfo,
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
export { settingsModule } from './modules/settings';
export {
  agentModule,
  askAgent,
  DEFAULT_AGENT_MODEL,
  DEFAULT_VLLM_MODEL,
  getAgentStatus,
  pullAgentModel,
  saveAgentConfig,
  spawnVllm,
  stopVllm,
  streamAgentChat,
  type AgentCallbacks,
  type AgentStatus,
  type DetectedProvider,
  type PullProgress,
  type VllmStatus,
} from './modules/agent';
