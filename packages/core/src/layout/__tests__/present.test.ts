/**
 * Presenting a pane: showing it over the whole shell, taskbar included.
 *
 * The rules worth pinning are the ones that fail *silently* — a flag left pointing
 * at a pane that is gone paints a screen with no chrome and no way out.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { isPresenting, presentFocusedPane, presentPane } from '../controller';
import { deserializeDock, serializeDock } from '../dock/serialize';
import { layoutStore } from '../store';
import type { FrameState } from '../types';
import { current, loadFrame, windowsFrame } from './fixtures';

const KNOWN = new Set(['scratch.note', 'editor.buffer']);

beforeEach(() => {
  layoutStore.resetForTests();
  loadFrame(windowsFrame(['editor.buffer', 'scratch.note']));
});

const first = () => 'editor.buffer#4';

describe('SET_PRESENTED', () => {
  it('presents an open pane', () => {
    expect(
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: first() }).frame
        .presentedInstanceId,
    ).toBe(first());
  });

  it('refuses an instance id that names nothing', () => {
    expect(
      layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: 'nope' }).frame.presentedInstanceId,
    ).toBeNull();
  });

  it('clears when the presented pane closes', () => {
    const id = first();
    layoutStore.dispatch({ type: 'SET_PRESENTED', instanceId: id });
    // Stale here is not a mis-aimed keybinding: presentation hides the taskbar, so
    // a dangling id is a blank screen with no chrome.
    expect(
      layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: id }).frame.presentedInstanceId,
    ).toBeNull();
  });
});

describe('presentFocusedPane', () => {
  it('toggles the focused pane', () => {
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: first() });
    presentFocusedPane();
    expect(current().presentedInstanceId).toBe(first());
    expect(isPresenting()).toBe(true);
    presentFocusedPane();
    expect(isPresenting()).toBe(false);
  });

  it('does nothing with no pane focused', () => {
    layoutStore.dispatch({ type: 'CLEAR_FOCUS' });
    presentFocusedPane();
    expect(isPresenting()).toBe(false);
  });

  it('unpresents through presentPane(null)', () => {
    presentPane(first());
    presentPane(null);
    expect(current().presentedInstanceId).toBeNull();
  });
});

describe('persistence', () => {
  it('is never written and never restored', () => {
    // A momentary way of looking at a pane, not a property of the desktop.
    // Restoring one hands the user a full-screen pane with the chrome hidden and
    // no memory of having asked for it.
    const presented: FrameState = { ...current(), presentedInstanceId: first() };
    const blob = serializeDock(presented);
    expect((blob.frame as Record<string, unknown>).presentedInstanceId).toBeUndefined();
    expect(
      deserializeDock(blob, KNOWN, { roleOf: () => 'document' })?.presentedInstanceId,
    ).toBeNull();
  });
});
