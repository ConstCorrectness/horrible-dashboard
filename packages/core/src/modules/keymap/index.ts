/**
 * The keymap module: the Keyboard Shortcuts pane, plus the agent tools that let
 * the assistant read and edit bindings on the user's behalf ("I don't like this,
 * change Tab so it does X"). See docs/modules/keymap.mdx.
 */
import { getSetting, settingsStore } from '../../settings';
import { KEYMAP_PRESET_KEY, KEYMAP_PRESETS, presetBindings } from '../../keymap/presets';
import { setKeymapPreset } from '../../keymap/state';
import type { ModuleManifest } from '../../registry';
import { registry } from '../../registry';
import { keymapAgentTools } from './tools';
import { ShortcutsPanel } from './ShortcutsPanel';

export const keymapModule: ModuleManifest = {
  id: 'keymap',
  title: 'Keyboard',
  panels: [
    {
      id: 'keymap.shortcuts',
      title: 'Keyboard Shortcuts',
      component: ShortcutsPanel,
      role: 'tool',
      icon: '⌨',
      singleton: true,
      dockable: ['right', 'left'],
      defaultDock: 'right',
      defaultDockSize: 460,
      // Declared on the pane but not dependent on it being open — the manifest
      // collects `agentTools` from every registered view, so the agent can
      // rebind a key without the Shortcuts pane being on screen.
      agentTools: keymapAgentTools,
    },
  ],
  commands: [
    {
      id: 'keymap.open',
      title: 'Keyboard: Open shortcuts',
      run: () => registry.openPanel('keymap.shortcuts'),
    },
  ],
  keybindings: [
    // A sequence, so it costs no single chord: the palette prefix then `k`.
    { key: 'mod+k mod+s', command: 'keymap.open' },
  ],
  settings: [
    {
      key: 'keymap.escapeHoldMs',
      title: 'Hold-Escape duration (ms)',
      description:
        'How long Escape must be held to hand the mouse back from a pane that has captured it (a game). Only applies where the browser lets the page keep Escape; otherwise a single press releases.',
      type: 'number',
      default: 400,
    },
    {
      key: KEYMAP_PRESET_KEY,
      title: 'Shortcut preset',
      description: KEYMAP_PRESETS.map((p) => `${p.title}: ${p.description}`).join(' · '),
      type: 'enum',
      enumValues: KEYMAP_PRESETS.map((p) => p.id),
      default: 'default',
    },
  ],
};

/**
 * Keep the resolver in step with the `keymap.preset` setting.
 *
 * Subscribed rather than read once at boot so switching preset takes effect on
 * the next keystroke — a shortcut set that needs a restart to apply is one the
 * user will conclude is broken.
 */
export function initKeymapPreset(): () => void {
  const apply = () => setKeymapPreset(presetBindings(getSetting<string>(KEYMAP_PRESET_KEY)));
  apply();
  return settingsStore.subscribe(apply);
}
