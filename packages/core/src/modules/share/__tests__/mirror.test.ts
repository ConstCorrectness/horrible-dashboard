import { describe, expect, it } from 'vitest';

import { createEmptyFrame } from '../../../layout/model';
import type { AreaNode, FrameState, PaneState } from '../../../layout/types';
import { mirrorChanged, mirrorPanes, redactFrame, type ViewLookup } from '../mirror';

/**
 * The redactor is the security boundary of the semantic mirror, so these tests
 * are written adversarially: the question is never "does a shared pane appear",
 * it is "can anything about an *unshared* pane be recovered from the payload".
 *
 * The strongest assertion in here is the serialized-blob search. Field-by-field
 * checks only cover the fields somebody thought to check, and the failure this
 * guards against is a future `PaneState` field being spread into the projection
 * by accident.
 */

function pane(viewId: string, n = 1, params?: Record<string, unknown>): PaneState {
  return { instanceId: `${viewId}#${n}`, viewId, params };
}

function area(id: string, tabs: PaneState[], activeTab = 0): AreaNode {
  return { kind: 'area', id, tabs, activeTab };
}

function frameWith(center: FrameState['center']): FrameState {
  return { ...createEmptyFrame(), center };
}

/** A registry where `shared.*` may be seen and `secret.*` never declared. */
const lookup: ViewLookup = (viewId) => {
  if (viewId === 'shared.note') return { title: 'Scratch', share: { mode: 'collab' } };
  if (viewId === 'shared.term') return { title: 'Terminal', share: { mode: 'mirror' } };
  if (viewId === 'shared.game') return { title: 'Game', share: { mode: 'pixels' } };
  if (viewId === 'secret.settings') return { title: 'Settings' };
  if (viewId === 'secret.connectors') return { title: 'Connectors' };
  return undefined;
};

describe('redactFrame', () => {
  it('keeps a declared pane, with its view id', () => {
    const out = redactFrame(frameWith(area('a1', [pane('shared.note')])), lookup);
    const [p] = mirrorPanes(out);
    expect(p.mode).toBe('collab');
    expect(p.viewId).toBe('shared.note');
    expect(p.title).toBe('Scratch');
  });

  it('redacts an undeclared pane and drops its view id entirely', () => {
    const out = redactFrame(frameWith(area('a1', [pane('secret.settings')])), lookup);
    const [p] = mirrorPanes(out);
    expect(p.mode).toBe('redacted');
    expect(p.viewId).toBeUndefined();
    expect(out.redactedCount).toBe(1);
  });

  it('gives a redacted pane the module title, never an instance title', () => {
    // The generic name keeps the map legible; an instance title could be a
    // filename, a URL or a row id.
    const out = redactFrame(frameWith(area('a1', [pane('secret.settings')])), lookup);
    expect(mirrorPanes(out)[0].title).toBe('Settings');
  });

  it('redacts a pane whose view is not registered at all', () => {
    // A workspace can hold a pane from a module that has since been removed.
    // "I cannot tell whether this may be shared" must resolve to "no".
    const out = redactFrame(frameWith(area('a1', [pane('ghost.gone')])), lookup);
    const [p] = mirrorPanes(out);
    expect(p.mode).toBe('redacted');
    expect(p.viewId).toBeUndefined();
  });

  it('never lets an undeclared pane leak anything into the payload', () => {
    const frame = frameWith(
      area('a1', [
        pane('secret.settings', 1, {
          token: 'ghp_REALLYSECRET',
          path: '/home/me/.aws/credentials',
        }),
        pane('shared.note'),
      ]),
    );
    const blob = JSON.stringify(redactFrame(frame, lookup));
    expect(blob).not.toContain('ghp_REALLYSECRET');
    expect(blob).not.toContain('.aws/credentials');
    expect(blob).not.toContain('secret.settings');
    // The one thing that does survive is the generic module name.
    expect(blob).toContain('Settings');
  });

  it('strips params from a declared pane that allowlisted nothing', () => {
    // Deny-by-default, one level down: declaring a pane shareable is not
    // declaring its params shareable.
    const frame = frameWith(area('a1', [pane('shared.term', 1, { cwd: '/home/me/secrets' })]));
    const out = redactFrame(frame, lookup);
    expect(mirrorPanes(out)[0].params).toBeUndefined();
    expect(JSON.stringify(out)).not.toContain('/home/me/secrets');
  });

  it('passes through only the allowlisted params keys', () => {
    const allowing: ViewLookup = () => ({
      title: 'Browser',
      share: { mode: 'mirror', params: ['url'] },
    });
    const frame = frameWith(
      area('a1', [pane('x.view', 1, { url: 'https://example.com', cookie: 'SECRET' })]),
    );
    const out = redactFrame(frame, allowing);
    expect(mirrorPanes(out)[0].params).toEqual({ url: 'https://example.com' });
    expect(JSON.stringify(out)).not.toContain('SECRET');
  });

  it('redacts panes in docks and windows, not just the centre tree', () => {
    // Three places hold panes. Covering only the centre tree is the obvious
    // half-fix, and a docked settings pane is a very ordinary thing to have open.
    const frame = createEmptyFrame();
    frame.docks.right.tools = [pane('secret.connectors')];
    frame.windows = [
      {
        id: 'w1',
        area: area('wa1', [pane('secret.settings')]),
        rect: { x: 0, y: 0, w: 100, h: 100 },
        mode: 'normal',
        z: 1,
      },
    ];
    const out = redactFrame(frame, lookup);
    expect(out.redactedCount).toBe(2);
    const blob = JSON.stringify(out);
    expect(blob).not.toContain('secret.connectors');
    expect(blob).not.toContain('secret.settings');
  });

  it('preserves the split geometry so both sides mean the same thing by "on the left"', () => {
    const frame = frameWith({
      kind: 'split',
      id: 's1',
      orientation: 'row',
      children: [area('a1', [pane('shared.note')]), area('a2', [pane('secret.settings')])],
      sizes: [0.7, 0.3],
    });
    const out = redactFrame(frame, lookup);
    expect(out.center.kind).toBe('split');
    if (out.center.kind !== 'split') throw new Error('unreachable');
    expect(out.center.sizes).toEqual([0.7, 0.3]);
    expect(out.center.children).toHaveLength(2);
  });

  it('reports how many panes were withheld', () => {
    const frame = frameWith(
      area('a1', [pane('secret.settings'), pane('secret.connectors'), pane('shared.note')]),
    );
    // Told to the guest as a fact rather than hidden — three unexplained gaps in
    // a layout is worse than "3 hidden".
    expect(redactFrame(frame, lookup).redactedCount).toBe(2);
  });

  it('carries the host focus for follow mode', () => {
    const frame = frameWith(area('a1', [pane('shared.note')]));
    frame.focusedInstanceId = 'shared.note#1';
    expect(redactFrame(frame, lookup).focusedInstanceId).toBe('shared.note#1');
  });

  it('drops the backdrop, which can carry a wallpaper path', () => {
    const frame = frameWith(area('a1', [pane('shared.note')]));
    frame.backdrop = { id: 'image', params: { src: '/home/me/private.png' } };
    expect(JSON.stringify(redactFrame(frame, lookup))).not.toContain('private.png');
  });
});

describe('mirrorChanged', () => {
  it('is true for the first projection', () => {
    const out = redactFrame(frameWith(area('a1', [pane('shared.note')])), lookup);
    expect(mirrorChanged(null, out)).toBe(true);
  });

  it('is false when nothing a guest can see has changed', () => {
    // The common case: the host working inside a pane. No traffic at all.
    const frame = frameWith(area('a1', [pane('shared.note')]));
    expect(mirrorChanged(redactFrame(frame, lookup), redactFrame(frame, lookup))).toBe(false);
  });

  it('is false when only a redacted pane changed', () => {
    const before = redactFrame(frameWith(area('a1', [pane('secret.settings')])), lookup);
    const after = redactFrame(
      frameWith(area('a1', [pane('secret.settings', 1, { tab: 'tokens' })])),
      lookup,
    );
    expect(mirrorChanged(before, after)).toBe(false);
  });

  it('is true when the host focuses a different pane', () => {
    const a = frameWith(area('a1', [pane('shared.note'), pane('shared.term')]));
    const b = { ...a, focusedInstanceId: 'shared.term#1' };
    expect(mirrorChanged(redactFrame(a, lookup), redactFrame(b, lookup))).toBe(true);
  });
});
