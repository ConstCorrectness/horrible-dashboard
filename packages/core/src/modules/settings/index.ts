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
};
