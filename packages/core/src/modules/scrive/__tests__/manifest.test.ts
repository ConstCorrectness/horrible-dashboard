/**
 * Registration smoke test: the manifest registers, its panes and commands resolve,
 * and mod+s is scoped to a focused Scrive page rather than stealing the editor's.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { registry } from '../../../registry';
import { scriveModule } from '../index';

beforeEach(() => registry.resetForTests());
afterEach(() => registry.resetForTests());

describe('scrive manifest', () => {
  it('registers its panes and commands', () => {
    registry.register(scriveModule);
    const panels = (scriveModule.panels ?? []).map((p) => p.id);
    expect(panels).toEqual([
      'scrive.sites',
      'scrive.page',
      'scrive.posts',
      'scrive.outline',
      'scrive.publish',
      'scrive.share',
      'scrive.outbox',
      'scrive.clip',
    ]);
    const commands = (scriveModule.commands ?? []).map((c) => c.id);
    expect(commands).toEqual(
      expect.arrayContaining([
        'scrive.open',
        'scrive.newPost',
        'scrive.save',
        'scrive.generate',
        'scrive.saveAsTemplate',
        'scrive.publishSite',
        'scrive.sharePage',
        'scrive.openOutbox',
        'scrive.openClips',
      ]),
    );
  });

  it('keeps an outline awaiting review on screen with the pane closed', () => {
    registry.register(scriveModule);
    expect(registry.shellIndicators.map((i) => i.id)).toContain('scrive.outlines');
  });

  it('binds mod+s only while a Scrive page has focus', () => {
    const save = (scriveModule.keybindings ?? []).find((k) => k.command === 'scrive.save');
    expect(save?.key).toBe('mod+s');
    expect(save?.when).toBe("paneFocus == 'scrive.page'");
  });
});
