import { describe, expect, it } from 'vitest';

import { GRANT_BLURB, GRANT_LADDER, type GrantLevel } from '../api';
import { shareModule } from '../index';

/**
 * Registration smoke test plus the two contracts the pane leans on.
 *
 * `initShare()` is deliberately never called here: the shared socket connects
 * lazily, so importing the manifest is free, but starting the channel would try
 * to open a real WebSocket in node.
 */

describe('share module manifest', () => {
  it('registers the session tool and the mirror document, both singletons', () => {
    expect(shareModule.id).toBe('share');
    const panels = shareModule.panels ?? [];
    const byId = Object.fromEntries(panels.map((p) => [p.id, p]));
    expect(byId['share.session'].role).toBe('tool');
    expect(byId['share.mirror'].role).toBe('document');
    expect(panels.every((p) => p.singleton)).toBe(true);
  });

  it('shares none of its own panes', () => {
    // The share panes render the participant list and the grant picker. A guest
    // seeing the host's grant controls would be the module leaking itself.
    for (const panel of shareModule.panels ?? []) {
      expect(panel.share).toBeUndefined();
    }
  });

  it('namespaces every command under the module id', () => {
    for (const command of shareModule.commands ?? []) {
      expect(command.id.startsWith('share.')).toBe(true);
    }
  });

  it('exposes stopping and revoking as commands, not just buttons', () => {
    // Every user-facing capability is a command first — and these two are the
    // ones somebody reaches for in a hurry, so they have to be in the palette.
    const ids = (shareModule.commands ?? []).map((c) => c.id);
    expect(ids).toContain('share.stop');
    expect(ids).toContain('share.revokeAll');
  });
});

describe('the grant ladder', () => {
  it('runs weakest to strongest', () => {
    expect(GRANT_LADDER[0]).toBe('view');
    expect(GRANT_LADDER[GRANT_LADDER.length - 1]).toBe('control');
  });

  it('explains every rung', () => {
    // A rung with no blurb renders an empty description under a guest's name,
    // which is the one place the UI has to say what it just handed over.
    for (const level of GRANT_LADDER) {
      expect(GRANT_BLURB[level as GrantLevel]).toBeTruthy();
    }
    expect(Object.keys(GRANT_BLURB).sort()).toEqual([...GRANT_LADDER].sort());
  });

  it('has no duplicate rungs', () => {
    expect(new Set(GRANT_LADDER).size).toBe(GRANT_LADDER.length);
  });
});
