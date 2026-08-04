/**
 * Pane resources outlive unmount, and die only on a real close.
 *
 * The bug this guards: only an area's active tab renders, and a workspace switch
 * replaces the whole frame — so panes unmount constantly. Components could not
 * tell that from a close, and assumed the destructive reading: switching workspace
 * tabs killed your terminal's PTY and released the browser's Chromium engine.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  closePaneSession,
  closeWorkspaceSessions,
  hasPaneSession,
  paneSession,
  paneSessionKey,
  resetPaneSessionsForTests,
} from '../pane-lifetime';

beforeEach(() => {
  resetPaneSessionsForTests();
});

describe('paneSession', () => {
  it('creates once and hands the same resource to every later mount', () => {
    const create = vi.fn(() => ({ pty: 'shell-1' }));
    const key = paneSessionKey('scripting', 'terminal.instance#1');

    const first = paneSession(key, create, () => {});
    const second = paneSession(key, create, () => {});

    expect(second).toBe(first);
    // Once per *pane*, not once per mount — this is what stops `initialCommand`
    // being retyped every time you come back to the workspace.
    expect(create).toHaveBeenCalledTimes(1);
  });

  it('disposes only on an explicit close', () => {
    const dispose = vi.fn();
    const key = paneSessionKey('scripting', 'terminal.instance#1');
    paneSession(key, () => ({}), dispose);

    // Simulated unmounts: nothing calls dispose, because nothing should.
    expect(dispose).not.toHaveBeenCalled();
    expect(hasPaneSession(key)).toBe(true);

    closePaneSession(key);
    expect(dispose).toHaveBeenCalledTimes(1);
    expect(hasPaneSession(key)).toBe(false);
  });

  it('is idempotent on close, so a double close cannot kill twice', () => {
    const dispose = vi.fn();
    const key = paneSessionKey('w', 'p#1');
    paneSession(key, () => ({}), dispose);
    closePaneSession(key);
    closePaneSession(key);
    expect(dispose).toHaveBeenCalledTimes(1);
  });
});

describe('workspace scoping', () => {
  it('keeps two workspaces apart despite identical instance ids', () => {
    // `paneSeq` lives on FrameState, so ids are unique only *within* a frame — two
    // workspaces really can both hold `terminal.instance#1`. A globally keyed store
    // would hand the second one the first one's shell.
    const a = paneSession(
      paneSessionKey('scripting', 'terminal.instance#1'),
      () => 'A',
      () => {},
    );
    const b = paneSession(
      paneSessionKey('research', 'terminal.instance#1'),
      () => 'B',
      () => {},
    );
    expect(a).toBe('A');
    expect(b).toBe('B');
  });

  it('closes a deleted workspace’s panes, including unmounted ones', () => {
    const gone = vi.fn();
    const kept = vi.fn();
    paneSession(paneSessionKey('scratchpad', 'terminal.instance#1'), () => ({}), gone);
    paneSession(paneSessionKey('scripting', 'terminal.instance#1'), () => ({}), kept);

    closeWorkspaceSessions('scratchpad');

    expect(gone).toHaveBeenCalledTimes(1);
    expect(kept).not.toHaveBeenCalled();
  });

  it('a reset keeps the panes the preset still declares', () => {
    const survives = vi.fn();
    const dropped = vi.fn();
    paneSession(paneSessionKey('scripting', 'editor.buffer#1'), () => ({}), survives);
    paneSession(paneSessionKey('scripting', 'terminal.instance#7'), () => ({}), dropped);

    closeWorkspaceSessions('scripting', new Set(['editor.buffer#1']));

    expect(survives).not.toHaveBeenCalled();
    expect(dropped).toHaveBeenCalledTimes(1);
  });

  it('does not treat one workspace id as a prefix of another', () => {
    const dispose = vi.fn();
    paneSession(paneSessionKey('scripting-two', 'p#1'), () => ({}), dispose);
    closeWorkspaceSessions('scripting');
    expect(dispose).not.toHaveBeenCalled();
  });
});
