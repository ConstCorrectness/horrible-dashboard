export {
  BROWSER_CAPABILITIES,
  DESKTOP_CAPABILITIES,
  hasCapability,
  initCapabilities,
  type Capability,
} from './capabilities';
export { ApiError, apiDelete, apiGet, apiPost, apiPut } from './api';
export {
  Avatar3D,
  DEFAULT_AVATAR_MOOD,
  DEFAULT_AVATAR_MOODS,
  type AvatarMoodMap,
} from './Avatar3D';
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
  useWorkspaces,
  workspaceStore,
  type WorkspaceSnapshot,
  type WorkspaceSummary,
} from './workspace-store';
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
  type LayoutPreset,
  type ModuleManifest,
  type OpenPaneInfo,
  type OpenPaneOptions,
  type PaneDirection,
  type PanePlacement,
  type PanelDecl,
  type PanelGroupCompanion,
  type PanelGroupDecl,
  type SplitDirection,
  type SettingDecl,
  type SettingType,
  type ShellView,
  type UseAgentContext,
  type WidgetDecl,
  type WorkspaceInfo,
} from './registry';
export { dashboardModule } from './modules/dashboard';
export { layoutsModule } from './modules/layouts';
export { scratchModule } from './modules/scratch';
export { stubModule } from './modules/stub';
export { databaseModule } from './modules/database';
export { libraryModule } from './modules/library';
export { trainingModule } from './modules/training';
export { visualizerModule } from './modules/visualizer';
export { flowModule } from './modules/flow';
export { gamesModule } from './modules/games';
export { codeModule, SymbolSearchModal } from './modules/code';
export { gitModule } from './modules/git';
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
export { replModule } from './modules/repl';
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
  networkModule,
  initNetwork,
  initLobby,
  getPeers,
  subscribeCollab,
  collabJoin,
  collabLeave,
  collabOp,
  type CollabUpdate,
  type PeerInfo,
  type NodeIdentity,
  type PeersSnapshot,
} from './modules/network';
export {
  commonsModule,
  initCommons,
  getCommonsState,
  subscribeCommons,
  commonsConnect,
  commonsSearch,
  commonsRefresh,
  commonsPublish,
  commonsRequest,
  commonsRespond,
  commonsBlock,
  commonsUnblock,
  commonsVouch,
  commonsReport,
  commonsSetProfile,
  type CommonsProfile,
  type CommonsCandidate,
  type CommonsRequest,
  type CommonsState,
} from './modules/commons';
export {
  agentModule,
  askAgent,
  DEFAULT_AGENT_MODEL,
  DEFAULT_VLLM_MODEL,
  getAgentStatus,
  initAgentManifestSync,
  initAgentRelay,
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
export { openChatSession } from './modules/agent/openSession';
export {
  clearActiveScope,
  getActiveScope,
  isEditableTarget,
  matchesKeySpec,
  resolveKeybinding,
  setActiveScope,
} from './keybindings';
export {
  getLocus,
  setLocus,
  subscribeLocus,
  useLocus,
  type Locus,
  type LocusPosition,
  type LocusRange,
} from './locus';
export { toastsStore, type Toast } from './toasts';
export {
  dialogs,
  dialogsStore,
  type ActiveDialog,
  type ConfirmOptions,
  type PromptOptions,
} from './dialogs';
