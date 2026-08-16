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
      // A first-person shooter has no use for a taskbar in its peripheral
      // vision, and on the desktop build presenting it takes the OS window
      // fullscreen too — which is what makes pointer lock and a full field of
      // view behave the way the game assumes.
      fullscreen: true,
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
        'Multiplies how far the view turns per pixel of mouse movement, for this game only. Also adjustable from the main menu and the pause menu (Esc). The control map lives there too — it is a document rather than a value, so it has no row here.',
      type: 'number',
      default: 1,
    },
    {
      key: 'hassault.fov',
      title: 'Field of view',
      description:
        'Vertical field of view in degrees, 60–110. Wider sees more of the map and makes movement read as faster. Applies immediately, mid-match included.',
      type: 'number',
      default: 75,
    },
    {
      key: 'hassault.volume',
      title: 'Volume',
      description:
        'Footsteps, shots, jumps and landings, 0–1. Every sound is synthesized on the fly — none of it is a downloaded asset, and none of it is anybody else’s copyright. Zero never even opens an audio device.',
      type: 'number',
      default: 0.7,
    },
    {
      key: 'hassault.crouchToggle',
      title: 'Crouch toggles',
      description:
        'Off (the default) means crouch is a hold, which is what the movement rewards — you can release it the instant you need speed back. On makes it a toggle, which is easier on the hand.',
      type: 'boolean',
      default: false,
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
