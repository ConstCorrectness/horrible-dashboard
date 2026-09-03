export {
  BROWSER_CAPABILITIES,
  DESKTOP_CAPABILITIES,
  desktopCapabilities,
  hasCapability,
  initCapabilities,
  type Capability,
} from './capabilities';
export { ApiError, apiDelete, apiGet, apiPost, apiPut } from './api';
export { CopyableLink, CopyableValue } from './CopyableLink';
/** The shared icon set. Drawn vectors inheriting `currentColor` — the
 *  alternative every pane reached for was a native emoji. */
export {
  IconAlert,
  IconCheck,
  IconChevron,
  IconClock,
  IconPlus,
  IconRetry,
  IconSearch,
  IconSend,
  IconTrash,
} from './glyphs';
/** The shared list-row design system. See DataList.tsx — modules compose rows
 * from these rather than styling their own, so "a record with a verdict and
 * some figures" looks the same everywhere it appears. */
export {
  DataList,
  DataRow,
  PickRow,
  RollingNumber,
  RowMark,
  STAGGER_CAP,
  staggerIndex,
  type DataRowProps,
  type RowKind,
} from './DataList';
/** The shared control primitives — button, chip, pane header, empty state,
 * labelled field. See Primitives.tsx: the layer between the design tokens and a
 * pane, so the things AROUND a list look as consistent as the list does. */
export {
  Button,
  Chip,
  EmptyState,
  Field,
  PaneHeader,
  type ButtonIntent,
  type ButtonProps,
} from './Primitives';
/** The shared card primitive — one item in a feed you configure and act on
 * (a registry entry, a plugin, a downloadable model). See ResourceCard.tsx: a
 * `DataRow` is a record you read, a card is a small form you fill in. */
export {
  Caption,
  CodeChip,
  ControlBar,
  ControlRow,
  ResourceCard,
  ResourceCardList,
  Stack,
  type CardTag,
  type ResourceCardProps,
} from './ResourceCard';
export {
  installExternalLinkBridge,
  isDesktopShell,
  notifyExternalOpenFailed,
  onExternalOpenFailed,
  openExternal,
  openPath,
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
  isAppFullscreen,
  setAppFullscreen,
  subscribeFullscreen,
  toggleAppFullscreen,
} from './fullscreen';
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
/** Recently opened panes — the Start menu's Recent band. */
export { MAX_RECENTS, recentViewIds, subscribeRecents } from './layout/recents';
export type { LayoutAction } from './layout/actions';
export {
  findArea,
  findAreaAnywhere,
  findPaneAnywhere,
  findWindow,
  firstArea,
  listPanes,
  MIN_FRACTION,
} from './layout/model';
export {
  arrangeWindows,
  cascadeRect,
  clampRect,
  DEFAULT_SNAP,
  MIN_WINDOW_SIZE,
  rectForZone,
  rescaleRect,
  snapZoneAt,
  TITLEBAR_KEEP,
  type ArrangeStyle,
  type SnapConfig,
} from './layout/snap';
export { explodeToWindows, NOMINAL_VIEWPORT, tileWindows } from './layout/windows';
export { hideRailView, moveViewToDock, railEntries, RAIL_SECTIONS } from './layout/rail';
export { resolveViewIcon, taskbarEntries } from './layout/taskbar';
export type { TaskbarEntry, TaskbarState } from './layout/taskbar';
export type { RailEntry, RailSide, RailState } from './layout/rail';
export { getRailPrefs, railPrefsStore, resetRailPrefs, setViewHidden } from './layout/rail-prefs';
export type { RailPrefs } from './layout/rail-prefs';
export { dropPaneOnArea, dropPaneOnTab, paneDrag } from './layout/drag';
export type { DragPayload } from './layout/drag';
export { DEFAULT_BACKDROP } from './layout/types';
export {
  parseSpotlightQuery,
  spotlightResults,
  type SpotlightAction,
  type SpotlightItem,
  type SpotlightKind,
} from './spotlight';
export {
  KEYMAP_PRESET_KEY,
  KEYMAP_PRESETS,
  presetBindings,
  type KeymapPreset,
} from './keymap/presets';
export { matchCommands, minibuffer, resolveCommand } from './minibuffer';
export type { MinibufferState } from './minibuffer';
export type {
  AreaNode,
  DockState,
  BackdropRef,
  DesktopMode,
  SnapZone,
  WindowMode,
  WindowRect,
  WindowState,
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
  isPresenting,
  presentPane,
  dockSidesOf,
  installFrameController,
  isDockable,
  joinAreaDirection,
  movePaneDirection,
  moveTabToSplit,
  openDocument,
  openPane as openFramePane,
  openPaneInArea,
  openToolInDock,
  paneDisplayTitle,
  readPaneAgentContext,
  regionsFor,
  resizeAreaPx,
  resolveView,
  revealRegionView,
  revealSection,
  roleOf,
  sectionsOf,
  setCenterMeasurer,
  activateTaskbarEntry,
  arrangeDesktop,
  cycleWindows,
  desktopViewport,
  focusWindow,
  focusWindowDirection,
  minimizePane,
  requestPaneAttention,
  moveWindowToDesktop,
  movePaneTo,
  setBackdrop,
  setDesktopMeasurer,
  setDesktopMode,
  setPaneMinimized,
  setPaneWindowed,
  setWindowMode,
  snapFocused,
  snapWindow,
  toggleDesktopMode,
  toggleWindowMaximized,
  toggleWindowMinimized,
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
export { lastPlacement, rememberPlacement, WINDOW_PLACEMENT_KEY } from './layout/window-placement';
export type { WindowPlacement } from './layout/window-placement';
export * as framePersistence from './layout/persistence';
export {
  BOOT_WORKSPACE_KEY,
  BOOT_WORKSPACE_LAST,
  DEFAULT_BOOT_WORKSPACE,
} from './layout/persistence';
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
  SectionInstanceContext,
  sectionsWithAgentContext,
  useAgentContext,
} from './agent-context';
export { backendHealth, type BackendHealth } from './health';
export { bootFailed, bootReady, bootStep, bootStore, type BootPhase, type BootState } from './boot';
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
  applyTheme,
  currentThemeId,
  DEFAULT_THEME,
  initTheme,
  isKnownTheme,
  readThemeTokens,
  THEME_SETTING_KEY,
  THEMES,
  useThemeId,
  type ThemeDecl,
} from './theme';
export {
  registry,
  type AgentCommandDecl,
  type AgentContextSnapshot,
  type AgentToolDecl,
  type BackdropDecl,
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
export { skillsModule } from './modules/skills';
export { browserModule } from './modules/browser';
// Native overlays (the browser's child webview) composite ABOVE the HTML layer, so
// any full-window UI must claim suppression while it is up or it renders underneath.
export {
  suppressNativeOverlays,
  nativeOverlaysSuppressed,
  subscribeNativeOverlaySuppression,
} from './modules/browser/overlay';
export { stubModule } from './modules/stub';
export { databaseModule } from './modules/database';
export { karaokeModule } from './modules/karaoke';
export { audioModule } from './modules/audio';
export { libraryModule } from './modules/library';
export { docviewerModule } from './modules/docviewer';
export { recordsModule } from './modules/records';
// Started at boot, not on pane mount: a proposal that arrives while you are looking
// at something else is the normal case for an unattended extraction.
export { initRecordsWatch } from './modules/records/store';
export { researchModule } from './modules/research';
export { searchModule } from './modules/search';
export { docsModule } from './modules/docs';
export {
  DEFAULT_DOC_SOURCES,
  DOC_SOURCE_IDS,
  enabledDocSources,
  lookupDocs,
  parseDocSources,
  setLspDocResolver,
  type DocEntry,
  type DocLookupRequest,
  type DocLookupResult,
  type DocSourceId,
} from './docs/chain';
export { docsHover, docsKeymap, renderDocEntry, symbolAt } from './docs/cm-docs';
export { renderMarkdown as renderDocMarkdown } from './docs/markdown';
export { trainingModule } from './modules/training';
export { localtrackModule } from './modules/localtrack';
export { notebookModule } from './modules/notebook';
export { visualizerModule } from './modules/visualizer';
export { flowModule } from './modules/flow';
export { gamesModule } from './modules/games';
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
export { hardwareModule } from './modules/hardware';
export {
  getPaths,
  storageModule,
  type RootSource,
  type StoragePaths,
  type StorageRoot,
} from './modules/storage';
export {
  installUpdate,
  startAutoUpdateChecks,
  updatesModule,
  type AutoUpdatePolicy,
  type UpdateInfo,
} from './modules/updates';
export { interpretabilityModule } from './modules/interpretability';
export { evalsModule } from './modules/evals';
export { trajectoriesModule } from './modules/trajectories';
export { agentpediaModule } from './modules/agentpedia';
export { labModule } from './modules/lab';
export { llamacppModule } from './modules/llamacpp';
export { observabilityModule } from './modules/observability';
export { marketplaceModule } from './modules/marketplace';
export { settingsModule } from './modules/settings';
export { initKeymapPreset, keymapModule } from './modules/keymap';
export { hassaultModule } from './modules/hassault';
export {
  shareModule,
  initShare,
  getShareSnapshot,
  subscribeShare,
  type GrantLevel,
  type Participant,
  type ShareSession,
} from './modules/share';
export {
  socialModule,
  initSocial,
  getRoster,
  getSocialState,
  subscribeSocial,
  addFriend,
  linkDevice,
  type Friend,
  type DeviceInfo,
  type SelfProfile,
  type RosterSnapshot,
} from './modules/social';
export {
  notificationsModule,
  initNotifications,
  getNotifications,
  subscribeNotifications,
  unreadCount,
  markAllRead,
  dismissNotification,
  retractNotification,
  registerNotificationAction,
  canNotifyDesktop,
  desktopPermission,
  ensureDesktopPermission,
  showDesktopNotification,
  type NotificationAction,
  type NotificationItem,
  type PermissionState,
} from './modules/notifications';
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
  collabShare,
  collabUnshare,
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
  deleteProviderKey,
  pullAgentModel,
  respondApproval,
  saveAgentConfig,
  saveProviderKey,
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
export {
  clearModelLocus,
  getModelLocus,
  setModelLocus,
  subscribeModelLocus,
  useModelLocus,
  type ModelLocus,
} from './model-locus';
export { toastsStore, type Toast } from './toasts';
export { closeTransientChrome, hasTransientChrome, registerTransient } from './transient';
export {
  placeLayer,
  type Align,
  type Placement,
  type PlacementRequest,
  type Rect,
  type Side,
  type Viewport,
} from './overlay/placement';
export {
  addContextMenuProvider,
  closeContextMenu,
  contextMenuStore,
  itemsForTarget,
  openContextMenu,
  resetContextMenuProviders,
  type ContextMenuItem,
  type ContextMenuProvider,
  type ContextTarget,
  type OpenContextMenu,
} from './overlay/context-menu';
export {
  dialogs,
  dialogsStore,
  type ActiveDialog,
  type ChoiceButton,
  type ChoiceOptions,
  type ConfirmOptions,
  type PromptOptions,
} from './dialogs';
