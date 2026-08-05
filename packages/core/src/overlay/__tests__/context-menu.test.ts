/**
 * The contribution model: a right-click reports *what* was clicked, and the menu
 * is assembled from whoever registered for that kind. The behaviours worth pinning
 * are the ones that decide whether a menu is usable rather than merely present —
 * grouping, a provider that declines, and a provider that throws.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  addContextMenuProvider,
  closeContextMenu,
  contextMenuStore,
  itemsForTarget,
  openContextMenu,
  resetContextMenuProviders,
} from '../context-menu';

const noop = () => {};

beforeEach(() => {
  resetContextMenuProviders();
  closeContextMenu();
});

describe('itemsForTarget', () => {
  it('returns one group per contributing provider, in order', () => {
    addContextMenuProvider({
      kind: 'files.node',
      order: 1,
      items: () => [{ id: 'b', label: 'Second', run: noop }],
    });
    addContextMenuProvider({
      kind: 'files.node',
      order: 0,
      items: () => [{ id: 'a', label: 'First', run: noop }],
    });
    const groups = itemsForTarget({ kind: 'files.node' });
    expect(groups.map((g) => g.map((i) => i.id))).toEqual([['a'], ['b']]);
  });

  it('keeps registration order within an order tier', () => {
    addContextMenuProvider({ kind: 'k', items: () => [{ id: 'first', label: '1', run: noop }] });
    addContextMenuProvider({ kind: 'k', items: () => [{ id: 'second', label: '2', run: noop }] });
    expect(
      itemsForTarget({ kind: 'k' })
        .flat()
        .map((i) => i.id),
    ).toEqual(['first', 'second']);
  });

  it('drops a provider that declines, leaving no empty group', () => {
    // An empty group would render as a stray separator — a divider with nothing
    // on one side of it.
    addContextMenuProvider({ kind: 'k', items: () => [{ id: 'a', label: 'A', run: noop }] });
    addContextMenuProvider({ kind: 'k', items: () => [] });
    expect(itemsForTarget({ kind: 'k' })).toHaveLength(1);
  });

  it('ignores providers registered for another kind', () => {
    addContextMenuProvider({ kind: 'rail', items: () => [{ id: 'a', label: 'A', run: noop }] });
    expect(itemsForTarget({ kind: 'files.node' })).toEqual([]);
  });

  it('accepts a provider registered for several kinds', () => {
    addContextMenuProvider({
      kind: ['rail', 'rail.glyph'],
      items: () => [{ id: 'a', label: 'A', run: noop }],
    });
    expect(itemsForTarget({ kind: 'rail' })).toHaveLength(1);
    expect(itemsForTarget({ kind: 'rail.glyph' })).toHaveLength(1);
  });

  it('passes the target through so items can vary by what was clicked', () => {
    addContextMenuProvider({
      kind: 'files.node',
      items: (t) =>
        t.nodeKind === 'dir' ? [] : [{ id: 'open', label: `Open ${t.name}`, run: noop }],
    });
    expect(itemsForTarget({ kind: 'files.node', nodeKind: 'dir' })).toEqual([]);
    expect(itemsForTarget({ kind: 'files.node', nodeKind: 'file', name: 'a.py' })[0][0].label).toBe(
      'Open a.py',
    );
  });

  it('survives a provider that throws, keeping everyone else’s items', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(noop);
    addContextMenuProvider({
      kind: 'k',
      items: () => {
        throw new Error('bad provider');
      },
    });
    addContextMenuProvider({ kind: 'k', items: () => [{ id: 'ok', label: 'OK', run: noop }] });
    expect(
      itemsForTarget({ kind: 'k' })
        .flat()
        .map((i) => i.id),
    ).toEqual(['ok']);
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it('drops an item whose submenu came back empty', () => {
    addContextMenuProvider({
      kind: 'k',
      items: () => [
        { id: 'with', label: 'With', run: noop, submenu: [{ id: 's', label: 'S', run: noop }] },
        { id: 'without', label: 'Without', run: noop, submenu: [] },
      ],
    });
    expect(
      itemsForTarget({ kind: 'k' })
        .flat()
        .map((i) => i.id),
    ).toEqual(['with']);
  });
});

describe('openContextMenu', () => {
  it('opens with the items resolved at click time', () => {
    let label = 'before';
    addContextMenuProvider({ kind: 'k', items: () => [{ id: 'a', label, run: noop }] });
    expect(openContextMenu({ clientX: 10, clientY: 20 }, { kind: 'k' })).toBe(true);
    label = 'after';
    // Resolved on open, so the menu keeps describing the state that was clicked.
    expect(contextMenuStore.getSnapshot()?.groups[0][0].label).toBe('before');
    expect(contextMenuStore.getSnapshot()).toMatchObject({ x: 10, y: 20 });
  });

  it('reports false and opens nothing when no provider offers an item', () => {
    // The caller uses this to let the browser's own menu through rather than
    // swallowing the gesture and showing an empty box.
    expect(openContextMenu({ clientX: 0, clientY: 0 }, { kind: 'unknown' })).toBe(false);
    expect(contextMenuStore.getSnapshot()).toBeNull();
  });

  it('notifies subscribers on open and close', () => {
    addContextMenuProvider({ kind: 'k', items: () => [{ id: 'a', label: 'A', run: noop }] });
    const seen: (string | null)[] = [];
    const unsub = contextMenuStore.subscribe(() =>
      seen.push(contextMenuStore.getSnapshot()?.target.kind ?? null),
    );
    openContextMenu({ clientX: 1, clientY: 1 }, { kind: 'k' });
    closeContextMenu();
    unsub();
    expect(seen).toEqual(['k', null]);
  });
});
