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
  type WidgetDecl,
} from './registry';
export { dashboardModule } from './modules/dashboard';
