/**
 * A declared region must always leave a way back to itself.
 *
 * `Region` used to render `null` whenever a strip was closed, and `regionsFor`
 * opens a strip only where some view declares `defaultOpen` — which most do not.
 * The training notebook declares six (Metrics, Architecture, Rollout, Manim,
 * Peers, Projects) and none of them sets it, so every one of them started closed
 * and drew nothing at all. They were reachable from the command palette and from
 * a keychord, and from nowhere on screen, which from the user's side is
 * indistinguishable from a feature that was never built.
 *
 * So the rule is: **closed draws the rail, the same as collapsed.** Putting a
 * strip away may never remove the way back.
 */
import { describe, expect, it } from 'vitest';

import { regionAt, regionDisplay } from '../layout/controller';
import type { PaneState, RegionState } from '../layout/types';

function pane(region: Partial<RegionState>): PaneState {
  return {
    instanceId: 'p#1',
    viewId: 'nothing.registered',
    regions: {
      right: {
        open: false,
        size: 280,
        collapsed: false,
        views: ['a.view', 'b.view'],
        activeView: 'a.view',
        ...region,
      },
    },
  };
}

describe('region display', () => {
  it('draws a rail for a closed region, never nothing', () => {
    expect(regionDisplay(pane({ open: false }), 'right')).toBe('rail');
  });

  it('draws a rail for a collapsed one too — the states look the same', () => {
    expect(regionDisplay(pane({ open: true, collapsed: true }), 'right')).toBe('rail');
  });

  it('draws the full strip when it is open and not collapsed', () => {
    expect(regionDisplay(pane({ open: true, collapsed: false }), 'right')).toBe('strip');
  });

  it('draws nothing where the view declares no region', () => {
    expect(regionDisplay(pane({}), 'bottom')).toBe('none');
    expect(regionAt(pane({}), 'bottom')).toBeNull();
  });

  it('draws nothing for a region that holds no views', () => {
    // Not the same as "closed": there is no way back to open, so a rail would be
    // a control that does nothing.
    expect(regionDisplay(pane({ views: [] }), 'right')).toBe('none');
  });

  it('falls back to the declaration for a pane that predates the region', () => {
    // `regions` is written when a pane is *opened*, so a region added to a view
    // later is absent from every pane already in the saved layout. Reading the
    // instance alone made those panes permanently railless.
    const stale: PaneState = { instanceId: 'p#2', viewId: 'nothing.registered' };
    expect(regionAt(stale, 'right')).toBeNull(); // unregistered view: nothing to fall back to
    expect(regionDisplay(stale, 'right')).toBe('none');
  });
});
