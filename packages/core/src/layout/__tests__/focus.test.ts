import { beforeEach, describe, expect, it } from 'vitest';

import { layoutStore } from '../store';
import { listPanes } from '../model';
import { current, groupOf, loadFrame, windowsFrame } from './fixtures';

const frame = () => windowsFrame([['editor.buffer', 'scratch.note'], 'files.tree']);

/** Instance id of the one pane of `viewId`. */
function instanceOf(viewId: string): string {
  const hit = listPanes(current()).find((p) => p.pane.viewId === viewId);
  if (!hit) throw new Error(`no pane for ${viewId}`);
  return hit.pane.instanceId;
}

beforeEach(() => {
  layoutStore.resetForTests();
  loadFrame(frame(), 'test');
});

describe('FOCUS_PANE', () => {
  it('records the focused pane', () => {
    const editor = instanceOf('editor.buffer');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor });
    expect(current().focusedInstanceId).toBe(editor);
  });

  it('ignores an unknown instance id', () => {
    const before = layoutStore.getSnapshot();
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: 'nope#9' });
    expect(layoutStore.getSnapshot()).toBe(before);
  });

  it('CLEAR_FOCUS clears the focused pane', () => {
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: instanceOf('editor.buffer') });
    layoutStore.dispatch({ type: 'CLEAR_FOCUS' });
    expect(current().focusedInstanceId).toBeNull();
  });

  it('re-focusing the same pane is a no-op snapshot', () => {
    const editor = instanceOf('editor.buffer');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor });
    const snap = layoutStore.getSnapshot();
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor });
    expect(layoutStore.getSnapshot()).toBe(snap);
  });
});

describe('focus is never left dangling', () => {
  it('closing the focused tab hands focus to the tab that takes its place', () => {
    const editor = instanceOf('editor.buffer');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: editor });
    layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: editor });
    expect(current().focusedInstanceId).toBe(instanceOf('scratch.note'));
  });

  it('closing the last pane of the focused group moves focus to another group', () => {
    const tool = instanceOf('files.tree');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: tool });
    layoutStore.dispatch({ type: 'REMOVE_PANE', instanceId: tool });
    expect(current().focusedInstanceId).toBe(instanceOf('editor.buffer'));
    expect(current().focusedAreaId).toBe(groupOf(instanceOf('editor.buffer'))!.id);
  });

  it('a pane split into its own group keeps focus', () => {
    const note = instanceOf('scratch.note');
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: note });
    layoutStore.dispatch({
      type: 'MOVE_PANE',
      instanceId: note,
      target: { kind: 'split', areaId: groupOf(note)!.id, side: 'right' },
    });
    expect(current().focusedInstanceId).toBe(note);
  });

  it('loading a desktop drops a focus id it does not contain', () => {
    layoutStore.dispatch({ type: 'FOCUS_PANE', instanceId: instanceOf('editor.buffer') });
    loadFrame({ ...frame(), focusedInstanceId: 'ghost.pane#42' }, 'other');
    expect(current().focusedInstanceId).not.toBe('ghost.pane#42');
  });
});
