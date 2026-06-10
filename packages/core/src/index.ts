export {
  BROWSER_CAPABILITIES,
  DESKTOP_CAPABILITIES,
  hasCapability,
  initCapabilities,
  type Capability,
} from './capabilities';
export { apiDelete, apiGet, apiPost, apiPut } from './api';
export { getWorkspaceLayout, saveWorkspaceLayout, type SerializedLayout } from './workspace';
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
