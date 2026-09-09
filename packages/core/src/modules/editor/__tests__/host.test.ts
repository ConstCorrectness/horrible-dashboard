/**
 * The buffer-host seam.
 *
 * This is what stops the IDE workbench splitting off a competing
 * `editor.buffer` pane every time a file is clicked in its own Explorer strip —
 * two live CodeMirror views registered under one source URI, where saving is
 * last-writer-wins. The failure is silent: you get a second editor beside the
 * one you are using and lose whichever save lands first.
 *
 * `openBuffer` itself cannot be imported here (its module opens a WebSocket at
 * module scope, and there is no jsdom in this workspace), which is exactly why
 * the seam is a leaf file. What is pinned is the routing decision; that
 * `openBuffer`'s first line asks it is verified in the running app.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { adoptByHost, hasBufferHost, routeOpenToHost, setBufferHost } from '../host';

afterEach(() => setBufferHost(null));

describe('with no host installed', () => {
  it('claims nothing, so every caller falls through to pane routing', () => {
    expect(hasBufferHost()).toBe(false);
    expect(routeOpenToHost('workspace-file:/a.ts')).toBe(false);
    expect(adoptByHost('editor.buffer#1', 'workspace-file:/a.ts')).toBe(false);
  });
});

describe('with a host installed', () => {
  it('routes the open to it and passes the options through', () => {
    const open = vi.fn(() => true);
    setBufferHost({ open, adopt: () => false });

    expect(routeOpenToHost('note:1', { language: 'python' })).toBe(true);
    expect(open).toHaveBeenCalledWith('note:1', { language: 'python' });
  });

  it('falls through when the host declines', () => {
    // A workbench that is registered but has no pane in the frame declines, so
    // an ordinary editor pane opens — which is the right answer for a user who
    // never asked for an IDE.
    setBufferHost({ open: () => false, adopt: () => false });
    expect(routeOpenToHost('note:1')).toBe(false);
  });

  it('offers a rename to the host before the pane is retargeted', () => {
    const adopt = vi.fn(() => true);
    setBufferHost({ open: () => true, adopt });
    expect(adoptByHost('ide.workbench#1:buffer:untitled', 'workspace-file:/a.ts')).toBe(true);
    expect(adopt).toHaveBeenCalledWith('ide.workbench#1:buffer:untitled', 'workspace-file:/a.ts');
  });

  it('never asks the host about a buffer with no instance id', () => {
    const adopt = vi.fn(() => true);
    setBufferHost({ open: () => true, adopt });
    expect(adoptByHost(null, 'workspace-file:/a.ts')).toBe(false);
    expect(adopt).not.toHaveBeenCalled();
  });

  it('stops claiming once cleared', () => {
    setBufferHost({ open: () => true, adopt: () => true });
    setBufferHost(null);
    expect(hasBufferHost()).toBe(false);
    expect(routeOpenToHost('note:1')).toBe(false);
  });
});
