/**
 * `isPaneShowing`: the layout's answer to "can the user see this pane". The
 * native browser view depends on it to yield its OS surface, because the
 * workbench hides covered panes with `visibility`, which no DOM observer sees.
 */
import { describe, expect, it } from 'vitest';

import type { PaneState } from '../../types';
import {
  createEmptyDockFrame,
  findPane,
  insertPane,
  isPaneShowing,
  setDockVisible,
  toggleMaximized,
} from '../frame';
import type { DockFrame, PlaceTarget } from '../types';

const pane = (id: string): PaneState => ({ instanceId: id, viewId: id.split('#')[0] });

function build(...steps: [string, PlaceTarget][]): DockFrame {
  return steps.reduce(
    (frame, [id, target]) => insertPane(frame, pane(id), target)!,
    createEmptyDockFrame(),
  );
}

describe('isPaneShowing', () => {
  const base = () =>
    build(
      ['web#1', { kind: 'center' }],
      ['doc#2', { kind: 'center' }],
      ['tool#3', { kind: 'dock', side: 'right' }],
    );

  it('only the showing tab of a group', () => {
    const frame = base();
    expect(isPaneShowing(frame, 'doc#2')).toBe(true);
    expect(isPaneShowing(frame, 'web#1')).toBe(false);
  });

  it('not in a hidden dock', () => {
    const frame = base();
    expect(isPaneShowing(frame, 'tool#3')).toBe(true);
    expect(isPaneShowing(setDockVisible(frame, 'right', false), 'tool#3')).toBe(false);
  });

  it('not behind a maximized group', () => {
    const frame = base();
    const maxed = toggleMaximized(frame, findPane(frame, 'doc#2')!.location.areaId);
    expect(isPaneShowing(maxed, 'doc#2')).toBe(true);
    expect(isPaneShowing(maxed, 'tool#3')).toBe(false);
  });

  it('only the presented pane while one is presented', () => {
    const frame = { ...base(), presentedInstanceId: 'tool#3' };
    expect(isPaneShowing(frame, 'tool#3')).toBe(true);
    expect(isPaneShowing(frame, 'doc#2')).toBe(false);
  });

  it('unknown panes are not showing', () => {
    expect(isPaneShowing(base(), 'nope#9')).toBe(false);
  });
});
