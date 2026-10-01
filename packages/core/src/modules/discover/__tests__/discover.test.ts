import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DiscoverPage } from '../api';

const listItems = vi.fn();
const getSources = vi.fn();
const getDetail = vi.fn();

vi.mock('../api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../api')>();
  return {
    ...real,
    listItems: (...args: unknown[]) => listItems(...args),
    getSources: (...args: unknown[]) => getSources(...args),
    getDetail: (...args: unknown[]) => getDetail(...args),
  };
});

const { discoverModule } = await import('../index');
const { SECTIONS, cleanReadme, compact, formatMetric, rowMetrics, sectionConfig } =
  await import('../format');
const { listQuery } = await import('../api');
const store = await import('../store');

function page(label: string, ids: string[], over: Partial<DiscoverPage> = {}): DiscoverPage {
  return {
    source: 'hf',
    kind: 'model',
    items: ids.map((id) => ({
      source: 'hf',
      kind: 'model',
      id,
      title: id,
      subtitle: '',
      description: '',
      url: null,
      author: null,
      created_at: null,
      updated_at: null,
      thumbnail: null,
      tags: [],
      badges: [],
      metrics: [],
      facts: [],
    })),
    cursor_next: null,
    total: null,
    feed_label: label,
    fetched_at: 0,
    stale: false,
    status: 'ok',
    message: null,
    retry_after: null,
    ...over,
  };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe('discover module', () => {
  it('declares one singleton document pane with a section per catalog', () => {
    const pane = discoverModule.panels?.[0];
    expect(pane?.id).toBe('discover.browse');
    expect(pane?.role).toBe('document');
    expect(pane?.singleton).toBe(true);
    expect(pane?.sections?.map((s) => s.id)).toEqual(SECTIONS.map((s) => s.id));
    // Section keys must be unique and avoid the region toggles t/n/b.
    const keys = pane?.sections?.map((s) => s.key) ?? [];
    expect(new Set(keys).size).toBe(keys.length);
    for (const k of keys) expect(['t', 'n', 'b']).not.toContain(k);
  });

  it('declares open/search commands, one opener per section', () => {
    const ids = (discoverModule.commands ?? []).map((c) => c.id);
    expect(ids).toContain('discover.open');
    expect(ids).toContain('discover.search');
    for (const s of SECTIONS) expect(ids).toContain(`discover.open.${s.id}`);
  });
});

describe('format', () => {
  it('never renders an unknown metric as zero', () => {
    expect(formatMetric({ value: null, unit: 'count' })).toBe('—');
    expect(formatMetric({ value: 0, unit: 'count' })).toBe('0');
  });

  it('formats counts, bytes and fractions', () => {
    expect(compact(950)).toBe('950');
    expect(compact(27_228)).toBe('27.2k');
    expect(compact(5_400_000)).toBe('5.4M');
    expect(formatMetric({ value: 30_104, unit: 'bytes' })).toBe('29.4 KB');
    expect(formatMetric({ value: 0.875, unit: 'percent' })).toBe('88%');
  });

  it('keeps only metrics with values in a row', () => {
    const m = (key: string, value: number | null) => ({
      key,
      label: key,
      value,
      unit: 'count' as const,
    });
    expect(
      rowMetrics([m('a', null), m('b', 1), m('c', 2), m('d', 3), m('e', 4)]).map((x) => x.key),
    ).toEqual(['b', 'c', 'd']);
  });

  it('cleans README markup the renderer cannot draw, but not inside code', () => {
    const src = [
      '<div align="center">',
      '[![License](https://img.shields.io/x.svg)](https://example.com/LICENSE)',
      '![logo](logo.png) <b>Bold</b> text',
      '</div>',
      '```html',
      '<div>keep</div>',
      '```',
    ].join('\n');
    expect(cleanReadme(src)).toBe(
      [
        '[License](https://example.com/LICENSE)',
        ' Bold text',
        '',
        '```html',
        '<div>keep</div>',
        '```',
      ].join('\n'),
    );
  });

  it('falls back to the first section for an unknown id', () => {
    expect(sectionConfig('nope').id).toBe('models');
    expect(sectionConfig('kaggle').kinds).toContain('benchmark');
  });

  it('sends an explicitly emptied filter, which means "any"', () => {
    const qs = new URLSearchParams(listQuery('repo', { filters: { topic: '', language: 'Rust' } }));
    expect(qs.get('f.topic')).toBe('');
    expect(qs.get('f.language')).toBe('Rust');
    expect(qs.has('q')).toBe(false);
  });
});

describe('store', () => {
  beforeEach(() => {
    store.resetDiscover();
    listItems.mockReset();
    getSources.mockReset();
    getDetail.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it('drops a reply that a newer request has overtaken', async () => {
    const slow = deferred<DiscoverPage>();
    const fast = deferred<DiscoverPage>();
    listItems.mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise);

    const first = store.load('hf', 'model');
    const second = store.load('hf', 'model');
    fast.resolve(page('new', ['b']));
    await second;
    slow.resolve(page('old', ['a']));
    await first;

    const view = store.viewOf(store.getState(), 'hf', 'model');
    expect(view.page?.feed_label).toBe('new');
    expect(view.items.map((i) => i.id)).toEqual(['b']);
  });

  it('keeps each view independent, so switching sections loses nothing', async () => {
    listItems
      .mockResolvedValueOnce(page('models', ['m1']))
      .mockResolvedValueOnce(page('repos', ['r1']));
    await store.load('hf', 'model');
    await store.load('github', 'repo');
    await store.select('hf', 'model', 'm1');
    const s = store.getState();
    expect(store.viewOf(s, 'hf', 'model').items[0].id).toBe('m1');
    expect(store.viewOf(s, 'hf', 'model').selected).toBe('m1');
    expect(store.viewOf(s, 'github', 'repo').items[0].id).toBe('r1');
    // Coming back is free: ensureLoaded doesn't refetch a loaded view.
    store.ensureLoaded('hf', 'model');
    expect(listItems).toHaveBeenCalledTimes(2);
  });

  it('appends the next page and de-duplicates by id', async () => {
    listItems
      .mockResolvedValueOnce(page('p', ['a', 'b'], { cursor_next: 'c2' }))
      .mockResolvedValueOnce(page('p', ['b', 'c']));
    await store.load('hf', 'model');
    await store.load('hf', 'model', { append: true });
    expect(listItems.mock.calls[1][2].cursor).toBe('c2');
    expect(store.viewOf(store.getState(), 'hf', 'model').items.map((i) => i.id)).toEqual([
      'a',
      'b',
      'c',
    ]);
  });

  it('debounces typing and searches once', async () => {
    vi.useFakeTimers();
    listItems.mockResolvedValue(page('q', []));
    store.setDraft('hf', 'model', 'q');
    store.setDraft('hf', 'model', 'qw');
    store.setDraft('hf', 'model', 'qwen');
    expect(listItems).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(store.SEARCH_DEBOUNCE_MS + 10);
    expect(listItems).toHaveBeenCalledTimes(1);
    expect(listItems.mock.calls[0][2].q).toBe('qwen');
  });

  it('shows spec defaults for untouched filters and the user choice once set', async () => {
    getSources.mockResolvedValue({
      sources: [
        {
          id: 'github',
          label: 'GitHub',
          requires_auth: false,
          connected: false,
          auth_hint: null,
          kinds: [
            {
              id: 'repo',
              label: 'Repositories',
              sorts: [{ value: 'stars', label: 'Most-starred' }],
              default_sort: 'stars',
              filters: [{ id: 'topic', label: 'Topic', options: [], default: 'machine-learning' }],
              searchable: true,
              search_placeholder: '',
            },
          ],
        },
      ],
    });
    listItems.mockResolvedValue(page('x', []));
    await store.ensureSpecs();
    const s = () => store.getState();
    expect(store.effectiveSort(s(), 'github', 'repo')).toBe('stars');
    expect(store.effectiveFilter(s(), 'github', 'repo', 'topic')).toBe('machine-learning');
    store.setFilter('github', 'repo', 'topic', '');
    expect(store.effectiveFilter(s(), 'github', 'repo', 'topic')).toBe('');
    expect(listItems.mock.calls.at(-1)?.[2].filters).toEqual({ topic: '' });
  });
});
