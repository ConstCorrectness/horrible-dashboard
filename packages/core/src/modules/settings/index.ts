import { registry, type ModuleManifest } from '../../registry';
import { SettingsPanel } from './SettingsPanel';

/** See docs/modules/settings.md. The user-facing settings page. */
export const settingsModule: ModuleManifest = {
  id: 'settings',
  title: 'Settings',
  panels: [
    {
      id: 'settings.home',
      title: 'Settings',
      component: SettingsPanel,
      defaultPlacement: 'center',
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'settings.open',
      title: 'Settings: Open',
      run: () => registry.openPanel('settings.home'),
    },
  ],
  // VS Code parity: Ctrl/Cmd+, opens settings.
  keybindings: [{ key: 'mod+,', command: 'settings.open' }],
  settings: [
    {
      key: 'system.dataDir',
      title: 'App Data Directory',
      description:
        'Path where the database, workspace keys, and configuration settings are stored (overridden via HORRIBLE_DATA_DIR).',
      type: 'string',
      default: '.data',
    },
    {
      key: 'system.cacheDir',
      title: 'Cache Directory',
      description:
        'Path where cache data (like downloaded models or plugins) is stored (overridden via HORRIBLE_CACHE_DIR).',
      type: 'string',
      default: '.cache',
    },
    {
      key: 'system.logsDir',
      title: 'Logs Directory',
      description: 'Path where the application logs (backend.log) are stored (overridden via HORRIBLE_LOGS_DIR).',
      type: 'string',
      default: 'logs',
    },
    {
      key: 'files.roots',
      title: 'Workspace Roots',
      description:
        'List of folders accessible in the file explorer (separated by path separator; overridden via HORRIBLE_WORKSPACE_ROOTS).',
      type: 'string',
      default: '',
    },
  ],
};
