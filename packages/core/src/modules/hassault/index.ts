import { registry, type ModuleManifest } from '../../registry';
import { HorribleAssaultPanel } from './HorribleAssaultPanel';

/**
 * HorribleAssault, frontend side: a WebGL first-person renderer for AssaultCube
 * maps, built from the cube grid the backend serves.
 *
 * The pane is a `document` — it wants the centre area and a lot of pixels, and it
 * grabs pointer lock, which would be hostile in a narrow dock.
 *
 * See docs/modules/hassault.mdx.
 */
export const hassaultModule: ModuleManifest = {
  id: 'hassault',
  title: 'HorribleAssault',
  panels: [
    {
      id: 'hassault.play',
      title: 'HorribleAssault',
      component: HorribleAssaultPanel,
      role: 'document',
      icon: '🔫',
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'hassault.open',
      title: 'HorribleAssault: Open',
      run: () => registry.openPanel('hassault.play'),
    },
  ],
  settings: [
    {
      key: 'hassault.sensitivity',
      title: 'Mouse sensitivity',
      description:
        'Multiplies how far the view turns per pixel of mouse movement, for this game only. Also adjustable in-game from the pause menu (Esc). The control map lives there too — it is a document rather than a value, so it has no row here.',
      type: 'number',
      default: 1,
    },
    {
      key: 'hassault.installPath',
      title: 'AssaultCube install path',
      description:
        'Folder containing packages/maps — the game content is read from your own copy and never bundled with this app. Blank auto-detects the usual locations for your platform.',
      type: 'string',
      default: '',
    },
  ],
};
