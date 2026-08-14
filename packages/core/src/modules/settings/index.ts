import { registry, type ModuleManifest } from '../../registry';
import { setSetting } from '../../settings';
import { DEFAULT_THEME, THEME_SETTING_KEY, THEMES } from '../../theme';
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
      // A center tab, the way VS Code opens its Settings editor — not a dock
      // panel. The page is two columns (category list + settings) and a right
      // dock gave that ~20rem, which wrapped every row onto three lines.
      role: 'document',
      icon: '⚙',
      singleton: true,
    },
  ],
  // Appearance is global rather than any one feature's concern. The `shell` module
  // (packages/ui/src/AppShell.tsx) would otherwise be the natural home, but it
  // registers from inside AppShell's mount effect — long after `initTheme()` has
  // read the theme at boot — so its declared default would not exist yet at the
  // one moment the theme is first resolved. This module is registered
  // synchronously in main.tsx before `loadSettings()`, so the default is always
  // there to be read.
  settings: [
    {
      key: THEME_SETTING_KEY,
      title: 'Theme',
      description: THEMES.map((t) => `${t.id} — ${t.description}`).join('  ·  '),
      type: 'enum',
      default: DEFAULT_THEME,
      enumValues: THEMES.map((t) => t.id),
    },
  ],
  commands: [
    {
      id: 'settings.open',
      title: 'Settings: Open',
      run: () => registry.openPanel('settings.home'),
    },
    // One entry per theme rather than a single "cycle" command: the palette is how
    // you reach a thing by name, and cycling past the one you wanted is the
    // failure mode of every toggle that stands in for a list.
    ...THEMES.map((theme) => ({
      id: `settings.theme.${theme.id}`,
      title: `Theme: ${theme.title}`,
      run: () => {
        void setSetting(THEME_SETTING_KEY, theme.id);
      },
    })),
  ],
  // VS Code parity: Ctrl/Cmd+, opens settings.
  keybindings: [{ key: 'mod+,', command: 'settings.open' }],
};
