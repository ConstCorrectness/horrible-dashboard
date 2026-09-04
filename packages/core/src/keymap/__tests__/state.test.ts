import { beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../registry';
import { getKeymap, initKeymapHost, setKeymapOverrides, unreachableDefaults } from '../state';
import { keyContextStore } from '../state';
import { releaseCapture, requestCapture } from '../capture';
import { layoutStore } from '../../layout/store';
import { seedFromPreset } from '../../layout/presets';

/** Isolate the keymap from every other module's real bindings. */
function only(keybindings: Parameters<typeof registry.register>[0]['keybindings']): void {
  registry.resetForTests();
  registry.register({ id: 'test', title: 'Test', keybindings });
}

beforeEach(() => {
  setKeymapOverrides([]);
  initKeymapHost({ platform: 'win', host: 'browser' });
});

describe('merging defaults with overrides', () => {
  it('normalizes the legacy `scope` field into a when clause', () => {
    only([{ key: 'mod+k', command: 'terminal.clear', scope: 'terminal.instance' }]);
    const binding = getKeymap().find((b) => b.command === 'terminal.clear')!;
    expect(binding.when).toBe("paneFocus == 'terminal.instance'");
  });

  it('canonicalizes the spec text so an override can key off it', () => {
    only([{ key: 'CTRL+Space', command: 'x' }]);
    expect(getKeymap().find((b) => b.command === 'x')!.key).toBe('ctrl+space');
  });

  it('drops an unparseable binding rather than throwing into the key handler', () => {
    only([
      { key: 'hyper+k', command: 'bogus' },
      { key: 'mod+k', command: 'good' },
    ]);
    const keymap = getKeymap();
    expect(keymap.some((b) => b.command === 'bogus')).toBe(false);
    expect(keymap.some((b) => b.command === 'good')).toBe(true);
  });

  it('appends user overrides after defaults, tagged as user', () => {
    only([{ key: 'mod+k', command: 'shell.commandPalette' }]);
    setKeymapOverrides([{ key: 'mod+shift+p', command: 'shell.commandPalette' }]);
    const mine = getKeymap().filter((b) => b.command === 'shell.commandPalette');
    expect(mine.map((b) => [b.key, b.source])).toEqual([
      ['mod+k', 'default'],
      ['mod+shift+p', 'user'],
    ]);
  });

  it('a disabled override suppresses the matching default', () => {
    // Half of a rebind. Without this the old key keeps working next to the new
    // one, which reads as "my change did nothing".
    only([{ key: 'mod+1', command: 'workspace.switch:1' }]);
    setKeymapOverrides([
      { key: 'alt+1', command: 'workspace.switch:1' },
      { key: 'mod+1', command: 'workspace.switch:1', disabled: true },
    ]);
    expect(getKeymap().map((b) => b.key)).toEqual(['alt+1']);
  });

  it('a disabled entry only suppresses its own key+command pair', () => {
    only([
      { key: 'mod+1', command: 'workspace.switch:1' },
      { key: 'mod+1', command: 'other.command' },
    ]);
    setKeymapOverrides([{ key: 'mod+1', command: 'workspace.switch:1', disabled: true }]);
    expect(getKeymap().map((b) => b.command)).toEqual(['other.command']);
  });
});

describe('host and platform filtering', () => {
  const split = [
    { key: 'mod+1', command: 'workspace.switch:1', hosts: ['desktop' as const] },
    { key: 'alt+1', command: 'workspace.switch:1', hosts: ['browser' as const] },
  ];

  it('keeps only the binding for this host', () => {
    only(split);
    expect(getKeymap().map((b) => b.key)).toEqual(['alt+1']);

    initKeymapHost({ host: 'desktop' });
    expect(getKeymap().map((b) => b.key)).toEqual(['mod+1']);
  });

  it('filters by platform too', () => {
    only([
      { key: 'ctrl+space', command: 'area.fullscreen', platforms: ['linux' as const] },
      { key: 'mod+alt+f', command: 'area.fullscreen', platforms: ['mac' as const, 'win' as const] },
    ]);
    expect(getKeymap().map((b) => b.key)).toEqual(['mod+alt+f']);

    initKeymapHost({ platform: 'linux' });
    expect(getKeymap().map((b) => b.key)).toEqual(['ctrl+space']);
  });

  it('an unfiltered binding applies everywhere', () => {
    only([{ key: 'mod+k', command: 'shell.commandPalette' }]);
    expect(getKeymap()).toHaveLength(1);
    initKeymapHost({ platform: 'mac', host: 'desktop' });
    expect(getKeymap()).toHaveLength(1);
  });
});

describe('unreachableDefaults', () => {
  it('names a default the host swallows, and clears once it is host-gated', () => {
    only([{ key: 'mod+1', command: 'workspace.switch:1' }]);
    expect(unreachableDefaults().map((d) => d.binding.key)).toEqual(['mod+1']);

    only([{ key: 'mod+1', command: 'workspace.switch:1', hosts: ['desktop' as const] }]);
    expect(unreachableDefaults()).toEqual([]);
  });

  it('ignores a merely preventable collision', () => {
    // alt+left is browser Back, but preventDefault reclaims it — that is a
    // choice, not a broken binding.
    only([{ key: 'alt+left', command: 'area.focus:left' }]);
    expect(unreachableDefaults()).toEqual([]);
  });

  it('does not flag the user own overrides', () => {
    only([]);
    setKeymapOverrides([{ key: 'mod+1', command: 'mine' }]);
    expect(unreachableDefaults()).toEqual([]);
  });
});

describe('the context store notices a capture', () => {
  it('re-emits when a pane takes or releases the keyboard', () => {
    // Capture is not implied by a layout dispatch: a pane grabs the keyboard
    // *after* focus has settled, so without an explicit subscription `capture`,
    // `captureView` and `keyboardLock` change with nobody re-reading them. The
    // dispatcher re-reads per keystroke and so never noticed; every reactive
    // consumer — the Shortcuts badge, the capture HUD — did.
    registry.resetForTests();
    layoutStore.resetForTests();
    layoutStore.dispatch({
      type: 'LOAD_WORKSPACE',
      workspaceId: 'test',
      frame: seedFromPreset(
        { id: 't', name: 'T', frame: { center: { pane: 'hassault.play' } } },
        { knownViews: new Set(['hassault.play']) },
      ),
    });
    const center = layoutStore.getSnapshot().frame.center;
    if (center.kind !== 'area') throw new Error('expected a single seeded area');
    const instanceId = center.tabs[0].instanceId;
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId });

    let emits = 0;
    const stop = keyContextStore.subscribe(() => {
      emits += 1;
    });
    try {
      // No layout dispatch here — only the capture changes.
      requestCapture({ mode: 'full', escape: 'passthrough', instanceId, viewId: 'hassault.play' });
      expect(emits).toBeGreaterThan(0);
      expect(keyContextStore.getSnapshot().capture).toBe('full');

      const afterGrab = emits;
      releaseCapture(instanceId);
      expect(emits).toBeGreaterThan(afterGrab);
      expect(keyContextStore.getSnapshot().capture).toBeNull();
    } finally {
      stop();
      releaseCapture();
    }
  });
});
