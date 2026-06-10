export {
  BROWSER_CAPABILITIES,
  DESKTOP_CAPABILITIES,
  hasCapability,
  initCapabilities,
  type Capability,
} from './capabilities';
export { apiGet, apiPut } from './api';
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
