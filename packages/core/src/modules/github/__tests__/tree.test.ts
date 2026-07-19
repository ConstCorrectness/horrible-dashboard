import { describe, expect, it } from 'vitest';

import { ancestorsOf, buildIndex, visibleRows } from '../tree';
import type { TreeEntry } from '../api';

const file = (path: string): TreeEntry => ({ path, kind: 'file', size: 1 });
const dir = (path: string): TreeEntry => ({ path, kind: 'dir', size: null });

describe('buildIndex', () => {
  it('groups a flat list by parent directory', () => {
    const index = buildIndex([dir('src'), file('src/app.py'), file('README.md')]);
    expect(index.get('')?.map((e) => e.path)).toEqual(['src', 'README.md']);
    expect(index.get('src')?.map((e) => e.path)).toEqual(['src/app.py']);
  });

  it('sorts directories before files, then alphabetically', () => {
    const index = buildIndex([file('b.txt'), dir('z-dir'), file('a.txt'), dir('a-dir')]);
    expect(index.get('')?.map((e) => e.path)).toEqual(['a-dir', 'z-dir', 'a.txt', 'b.txt']);
  });

  it('synthesizes missing intermediate directories', () => {
    // GitHub can list a deep blob without naming its parents; without synthesis the
    // file would be unreachable in the UI.
    const index = buildIndex([file('a/b/c.py')]);
    expect(index.get('')?.map((e) => e.path)).toEqual(['a']);
    expect(index.get('a')?.map((e) => e.path)).toEqual(['a/b']);
    expect(index.get('a/b')?.map((e) => e.path)).toEqual(['a/b/c.py']);
  });

  it('does not duplicate a directory that is also listed explicitly', () => {
    const index = buildIndex([dir('a'), file('a/b.py')]);
    expect(index.get('')).toHaveLength(1);
  });
});

describe('visibleRows', () => {
  const index = buildIndex([dir('src'), file('src/app.py'), dir('src/lib'), file('src/lib/x.py')]);

  it('shows only top-level rows when nothing is expanded', () => {
    expect(visibleRows(index, new Set()).map((r) => r.path)).toEqual(['src']);
  });

  it('reveals children of an expanded directory, with depth', () => {
    const rows = visibleRows(index, new Set(['src']));
    expect(rows.map((r) => [r.path, r.depth])).toEqual([
      ['src', 0],
      ['src/lib', 1],
      ['src/app.py', 1],
    ]);
  });

  it('nests deeply expanded directories', () => {
    const rows = visibleRows(index, new Set(['src', 'src/lib']));
    expect(rows.map((r) => r.path)).toEqual(['src', 'src/lib', 'src/lib/x.py', 'src/app.py']);
    expect(rows.find((r) => r.path === 'src/lib/x.py')?.depth).toBe(2);
  });

  it('uses the basename as the display name', () => {
    const rows = visibleRows(index, new Set(['src']));
    expect(rows.find((r) => r.path === 'src/app.py')?.name).toBe('app.py');
  });
});

describe('ancestorsOf', () => {
  it('lists ancestor directories outermost first', () => {
    expect(ancestorsOf('a/b/c.py')).toEqual(['a', 'a/b']);
  });

  it('is empty for a top-level path', () => {
    expect(ancestorsOf('README.md')).toEqual([]);
  });
});
