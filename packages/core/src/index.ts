export {
  BROWSER_CAPABILITIES,
  DESKTOP_CAPABILITIES,
  hasCapability,
  initCapabilities,
  type Capability,
} from './capabilities';
export { ApiError, apiDelete, apiGet, apiPost, apiPut } from './api';
export { CopyableLink } from './CopyableLink';
export {
  installExternalLinkBridge,
  isDesktopShell,
  notifyExternalOpenFailed,
  onExternalOpenFailed,
  openExternal,
} from './external';
export {
  Avatar3D,
  DEFAULT_AVATAR_MOOD,
  DEFAULT_AVATAR_MOODS,
  type AvatarMoodMap,
} from './Avatar3D';
export { apiUrl, getBackendOrigin, initBackendOrigin, wsUrl } from './origin';
export { setWindowControl, windowControl, type ResizeEdge, type WindowControl } from './window';
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
// The frame layout engine (packages/ui renders it; modules go through the
// registry / controller, never the store directly).
export { layoutStore } from './layout/store';
export type { LayoutAction } from './layout/actions';
export { findArea, firstArea, listPanes, MIN_FRACTION } from './layout/model';
export { hideRailView, moveViewToDock, railEntries, RAIL_SECTIONS } from './layout/rail';
export type { RailEntry, RailSide, RailState } from './layout/rail';
export { getRailPrefs, railPrefsStore, resetRailPrefs, setViewHidden } from './layout/rail-prefs';
export type { RailPrefs } from './layout/rail-prefs';
export { dropPaneOnArea, paneDrag } from './layout/drag';
export type { DragPayload } from './layout/drag';
export { matchCommands, minibuffer, resolveCommand } from './minibuffer';
export type { MinibufferState } from './minibuffer';
export type {
  AreaNode,
  DockState,
  FloatingPane,
  FrameState,
  LayoutNode,
  LayoutStoreState,
  LocatedPane,
  NavDirection,
  PaneLocation,
  PaneState,
  RegionState,
  SplitNode,
} from './layout/types';
export type { FramePreset, PresetNode } from './layout/presets';
export {
  activeSectionOf,
  closePaneGuarded,
  collapseRegion,
  focusAreaDirection,
  focusedPane,
  focusedViewId,
  focusInstance,
  focusPaneDom,
  fullscreenArea,
  fullscreenFocusedArea,
  dockSidesOf,
  installFrameController,
  isDockable,
  joinAreaDirection,
  movePaneDirection,
  openDocument,
  openPane as openFramePane,
  openPaneInArea,
  openToolInDock,
  readPaneAgentContext,
  regionsFor,
  resizeAreaPx,
  resolveView,
  revealRegionView,
  revealSection,
  roleOf,
  sectionsOf,
  setCenterMeasurer,
  setPaneSection,
  setRegionView,
  splitAreaBy,
  toggleDock,
  toggleRegion,
  toggleRegionView,
} from './layout/controller';
export {
  anyPaneDirty,
  isPaneDirty,
  registerCloseGuard,
  runCloseGuard,
  setPaneDirty,
  type CloseGuard,
} from './layout/close-guards';
export { usePaneSection, sectionOfInstance, type PaneSections } from './layout/use-sections';
export * as framePersistence from './layout/persistence';
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
export { backendHealth, type BackendHealth } from './health';
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
  type DockSide,
  type JSONSchema,
  type KeybindingDecl,
  type PaneCaptureDecl,
  type PaneRole,
  type RegionPosition,
  type RegionViewDecl,
  type SectionDecl,
  type LayoutController,
  type ModuleManifest,
  type OpenPaneInfo,
  type OpenPaneOptions,
  type PaneDirection,
  type PanelDecl,
  type SplitDirection,
  type SettingDecl,
  type SettingType,
  type ShellView,
  type UseAgentContext,
  type WidgetDecl,
  type WorkspaceInfo,
} from './registry';
export {
  beginConnect,
  disconnectConnector,
  listConnectors,
  pollConnect,
  pollUntilDone,
  submitConnect,
  type Connector,
  type ConnectorAccount,
  type ConnectorField,
  type ConnectorKind,
  type ConnectorScope,
  type ConnectStep,
} from './connectors/api';
export {
  connectorById,
  connectorsStore,
  onConnectRequested,
  refreshConnectors,
  requestConnect,
  type ConnectorsState,
} from './connectors/store';
export { useConnector, useConnectors } from './connectors/useConnectors';
export { ConnectionGate } from './connectors/ConnectionGate';
export { accountStore, refreshAccount, type AccountState } from './account-store';
export { useAccount } from './useAccount';
export { SignInCard } from './SignInCard';
export { dashboardModule } from './modules/dashboard';
export { layoutsModule } from './modules/layouts';
export { scratchModule } from './modules/scratch';
export { mcpModule } from './modules/mcp';
export { browserModule } from './modules/browser';
export { stubModule } from './modules/stub';
export { databaseModule } from './modules/database';
export { libraryModule } from './modules/library';
export { recordsModule } from './modules/records';
export { researchModule } from './modules/research';
export { searchModule } from './modules/search';
export { trainingModule } from './modules/training';
export { notebookModule } from './modules/notebook';
export { visualizerModule } from './modules/visualizer';
export { flowModule } from './modules/flow';
export { gamesModule } from './modules/games';
export { writerModule } from './modules/writer';
export { designModule } from './modules/design';
export { codeModule, SymbolSearchModal } from './modules/code';
export { gitModule } from './modules/git';
export { githubModule } from './modules/github';
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
export { interpretabilityModule } from './modules/interpretability';
export { observabilityModule } from './modules/observability';
export { marketplaceModule } from './modules/marketplace';
export { settingsModule } from './modules/settings';
export { keymapModule } from './modules/keymap';
export { hassaultModule } from './modules/hassault';
export {
  socialModule,
  initSocial,
  getRoster,
  addFriend,
  linkDevice,
  type Friend,
  type DeviceInfo,
  type SelfProfile,
  type RosterSnapshot,
} from './modules/social';
export { explorerModule } from './modules/explorer';
export { peopleModule } from './modules/people';
export {
  networkModule,
  initNetwork,
  initLobby,
  getPeers,
  createInvite,
  redeemInvite,
  subscribeCollab,
  collabJoin,
  collabLeave,
  collabOp,
  type CollabUpdate,
  type PeerInfo,
  type NodeIdentity,
  type PeersSnapshot,
  type InviteResponse,
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
export { executeTool } from './modules/agent/tool-exec';
export * from './keymap';
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
export { closeTransientChrome, hasTransientChrome, registerTransient } from './transient';
export {
  dialogs,
  dialogsStore,
  type ActiveDialog,
  type ChoiceButton,
  type ChoiceOptions,
  type ConfirmOptions,
  type PromptOptions,
} from './dialogs';
