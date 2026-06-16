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
export { onSocketOpen, sendChannel, subscribeChannel, type WsMessage } from './ws';
export {
  hasAgentContext,
  PaneInstanceContext,
  readAgentContext,
  useAgentContext,
} from './agent-context';
export { PaneParamsContext, usePaneParams, type PaneParams } from './panes';
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
  type AgentCommandDecl,
  type AgentContextSnapshot,
  type AgentToolDecl,
  type CommandDecl,
  type JSONSchema,
  type KeybindingDecl,
  type LayoutController,
  type ModuleManifest,
  type OpenPaneInfo,
  type OpenPaneOptions,
  type PanelDecl,
  type SettingDecl,
  type SettingType,
  type ShellView,
  type UseAgentContext,
  type WidgetDecl,
  type WorkspaceInfo,
} from './registry';
export { dashboardModule } from './modules/dashboard';
export { scratchModule } from './modules/scratch';
export { stubModule } from './modules/stub';
export {
  editorModule,
  getActiveBufferSource,
  loadSource,
  openBuffer,
  saveSource,
  sourceTitle,
  type LoadedSource,
} from './modules/editor';
export { filesModule } from './modules/files';
export { terminalModule, openTerminal, runCommand as runTerminalCommand } from './modules/terminal';
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
  initAgentManifestSync,
  initApprovalListener,
  pullAgentModel,
  respondApproval,
  saveAgentConfig,
  serializeManifest,
  spawnVllm,
  stopVllm,
  streamAgentChat,
  useApprovals,
  type AgentCallbacks,
  type AgentStatus,
  type ApprovalDecision,
  type DetectedProvider,
  type PendingApproval,
  type PullProgress,
  type SerializedTool,
  type VllmStatus,
} from './modules/agent';
