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
});

describe('region views', () => {
  // A region strip renders its view under `${host}:${position}:${viewId}`. The agent
  // chat's docked browser is one, holding a native webview: closing the chat must
  // close it, or the page keeps painting over the desktop with nothing to hide it.
  const host = 'agent.chat#1';
  const dock = `${host}:right:browser.view`;

  it('closes with its host pane', () => {
    const dispose = vi.fn();
    paneSession(paneSessionKey('w', dock), () => ({}), dispose);
    closePaneSession(paneSessionKey('w', host));
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  it('does not close a different pane that shares a prefix', () => {
    const dispose = vi.fn();
    paneSession(paneSessionKey('w', 'agent.chat#10:right:browser.view'), () => ({}), dispose);
    closePaneSession(paneSessionKey('w', host));
    expect(dispose).not.toHaveBeenCalled();
  });
});
