/**
 * Named keymap presets — alternative default sets, selected by the
 * `keymap.preset` setting.
 *
 * A preset is expressed as **ordinary overrides**, the same shape the Shortcuts
 * UI writes, and applied through the same resolver. There is deliberately no
 * second dispatcher and no second notion of "a default": switching to the i3 set
 * is exactly equivalent to a user rebinding those keys by hand, which is what
 * makes it composable with their own rebinds instead of fighting them.
 *
 * See docs/architecture/keybindings.mdx.
 */
import type { KeymapOverride } from './state';

export const KEYMAP_PRESET_KEY = 'keymap.preset';

export interface KeymapPreset {
  id: string;
  title: string;
  description: string;
  bindings: KeymapOverride[];
}

/**
 * i3/sway muscle memory: `mod+hjkl` to move focus, `mod+shift+hjkl` to move a
 * window, `mod+<n>` for workspaces, `mod+f` fullscreen, `mod+return` a terminal.
 *
 * Two deviations, both forced rather than chosen. The i3 modifier is the Super
 * key, which a web page never receives as a chord on its own — so `mod` here is
 * ctrl/cmd. And workspace switching keeps `mod+alt+<n>`: plain `mod+<n>` is
 * browser tab switching and is not cancellable, so binding it would produce keys
 * that silently never fire in the browser layout (see `reserved.ts`).
 */
const I3: KeymapOverride[] = [
  // Focus, vim keys.
  { key: 'mod+h', command: 'area.focus:left' },
  { key: 'mod+j', command: 'area.focus:down' },
  { key: 'mod+k', command: 'area.focus:up' },
  { key: 'mod+l', command: 'area.focus:right' },
  // Move the pane itself.
  { key: 'mod+shift+h', command: 'pane.move:left' },
  { key: 'mod+shift+j', command: 'pane.move:down' },
  { key: 'mod+shift+k', command: 'pane.move:up' },
  { key: 'mod+shift+l', command: 'pane.move:right' },
  // Splits: i3's `mod+v` / `mod+b` open the next pane below / beside.
  { key: 'mod+v', command: 'area.split:down' },
  { key: 'mod+shift+b', command: 'area.split:right' },
  { key: 'mod+f', command: 'area.fullscreen' },
  { key: 'mod+shift+q', command: 'pane.close' },
  { key: 'mod+shift+space', command: 'desktop.toggleMode' },
  { key: 'mod+return', command: 'pane.open:terminal.instance' },
  { key: 'mod+d', command: 'shell.commandPalette' },
  // `mod+k` is the spotlight in the default set and focus-up here, so the
  // shipped binding has to be suppressed or both would resolve on one press.
  { key: 'mod+k', command: 'shell.commandPalette', disabled: true },
  { key: 'mod+j', command: 'dock.toggle:bottom', disabled: true },
  { key: 'mod+b', command: 'dock.toggle:left', disabled: true },
];

export const KEYMAP_PRESETS: KeymapPreset[] = [
  {
    id: 'default',
    title: 'Default',
    description: 'Conventional shortcuts that avoid the chords the host eats.',
    bindings: [],
  },
  {
    id: 'i3',
    title: 'i3 / sway',
    description:
      'vim-style focus and movement on the mod key. Uses ctrl/cmd rather than Super, which a web page never receives.',
    bindings: I3,
  },
];

export function presetBindings(id: string | undefined): KeymapOverride[] {
  return KEYMAP_PRESETS.find((p) => p.id === id)?.bindings ?? [];
}
