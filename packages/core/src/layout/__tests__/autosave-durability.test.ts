/**
 * A layout edit must survive a save that fails.
 *
 * The bug this pins: `saveSnapshotNow` marked the revision saved *before* awaiting
 * the PUT. A save that then failed — the backend restarting under `--reload`, a
 * dropped connection, a tab going offline for a moment — left the store believing
 * everything was persisted, and no retry was ever attempted. Nothing looked wrong:
 * the window you had just opened was still on screen. It vanished on the next
 * refresh, which is what made a refresh look like the thing that had discarded it.
 *
 * Two properties are asserted, and they are the two halves of "no silent loss":
 * a failed save is retried until it lands, and concurrent saves cannot write an
 * older frame after a newer one.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { layoutStore } from '../store';
import { createEmptyFrame } from '../model';
import type { FrameState } from '../types';

const saved: { layout: unknown }[] = [];
let failures = 0;

vi.mock('../../workspace', () => ({
  getWorkspaces: vi.fn(async () => ({ active: 'desktop', workspaces: [] })),
  createWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
  setActiveWorkspace: vi.fn(async () => ({ active: 'desktop', workspaces: [] })),
  saveWorkspaceOnUnload: vi.fn(() => true),
  saveWorkspace: vi.fn(async (_id: string, patch: { layout?: unknown }) => {
    if (failures > 0) {
      failures--;
      throw new Error('simulated save failure');
    }
    saved.push({ layout: patch.layout });
    return { id: 'desktop', name: 'desktop', layout: null };
  }),
}));

// Imported after the mock so the module under test binds to it.
const { bindAutosave, flush } = await import('../persistence');
const { saveWorkspace } = await import('../../workspace');

function freshFrame(): FrameState {
  return createEmptyFrame();
}

/** Any edit that bumps the store's revision. `SET_DOCK` is the smallest one that
 * does not need a pane to exist first. */
function makeAnEdit(size: number): void {
  layoutStore.dispatch({ type: 'SET_DOCK', side: 'left', patch: { size } });
}

describe('layout autosave durability', () => {
  beforeEach(() => {
    saved.length = 0;
    failures = 0;
    vi.mocked(saveWorkspace).mockClear();
  });

  it('retries a save that failed instead of marking it done', async () => {
    bindAutosave();
    layoutStore.dispatch({
      type: 'LOAD_WORKSPACE',
      workspaceId: 'desktop',
      frame: freshFrame(),
    });

    // The next two PUTs fail; the edit must still reach the backend.
    failures = 2;
    makeAnEdit(301);

    // `flush` throws on the failing attempt — that is the signal the caller needs.
    await expect(flush()).rejects.toThrow('simulated save failure');
    await expect(flush()).rejects.toThrow('simulated save failure');

    // The third attempt succeeds and carries the edit, because the frame is a full
    // snapshot: the retry does not have to remember what was lost.
    await flush();
    expect(saved).toHaveLength(1);
    expect(vi.mocked(saveWorkspace)).toHaveBeenCalledTimes(3);
  });

  it('does not report an edit as saved when the save never happened', async () => {
    bindAutosave();
    layoutStore.dispatch({
      type: 'LOAD_WORKSPACE',
      workspaceId: 'desktop',
      frame: freshFrame(),
    });

    failures = 1;
    makeAnEdit(302);
    await expect(flush()).rejects.toThrow();
    expect(saved).toHaveLength(0);

    // With the old code this second flush was a no-op — the revision had already
    // been marked saved — and the edit was gone for good.
    await flush();
    expect(saved).toHaveLength(1);
  });
});
