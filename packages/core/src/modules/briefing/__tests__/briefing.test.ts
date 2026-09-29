/**
 * The briefing manifest, and the store's two rules that fail quietly: a fresh
 * section is not re-fetched on every Spotlight open, and a failed refresh keeps
 * the items it already had rather than blanking the list.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({
  fetchPapers: vi.fn(),
  fetchNews: vi.fn(),
}));

import { fetchNews, fetchPapers } from '../api';
import { briefingModule, BRIEFING_IN_SPOTLIGHT_KEY } from '../index';
import { FRESH_MS, getBriefing, loadBriefing, resetBriefingForTests } from '../store';

const paper = {
  arxiv_id: '2609.00001',
  title: 'A paper',
  summary: '',
  authors: [],
  organization: null,
  upvotes: 12,
  comments: 0,
  github_url: null,
  github_stars: null,
  thumbnail: null,
  submitted_at: '2026-09-26T00:00:00+00:00',
};

const story = {
  id: '1',
  title: 'A story',
  url: 'https://example.com',
  domain: 'example.com',
  points: 40,
  comments: 3,
  created_at: 1_790_000_000,
  discussion_url: 'https://news.ycombinator.com/item?id=1',
};

beforeEach(() => {
  resetBriefingForTests();
  vi.mocked(fetchPapers).mockResolvedValue({ papers: [paper], fetched_at: 1000, stale: false });
  vi.mocked(fetchNews).mockResolvedValue({ stories: [story], fetched_at: 1000, stale: false });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe('briefing manifest', () => {
  it('contributes no pane, namespaced commands, and the Spotlight toggle', () => {
    expect(briefingModule.panels ?? []).toHaveLength(0);
    const ids = (briefingModule.commands ?? []).map((c) => c.id);
    expect(ids).toEqual(['briefing.show', 'briefing.refresh']);
    const setting = briefingModule.settings?.find((s) => s.key === BRIEFING_IN_SPOTLIGHT_KEY);
    expect(setting?.default).toBe(true);
  });
});

describe('briefing store', () => {
  it('loads both sections and converts fetched_at to milliseconds', async () => {
    await loadBriefing();
    const { papers, news } = getBriefing();
    expect(papers.status).toBe('ready');
    expect(papers.items).toEqual([paper]);
    expect(papers.fetchedAt).toBe(1_000_000);
    expect(news.items).toEqual([story]);
  });

  it('does not re-fetch a fresh section, but does once it ages or is forced', async () => {
    vi.useFakeTimers();
    await loadBriefing();
    await loadBriefing();
    expect(fetchPapers).toHaveBeenCalledTimes(1);

    await loadBriefing({ force: true });
    expect(fetchPapers).toHaveBeenCalledTimes(2);
    expect(vi.mocked(fetchPapers).mock.calls[1][0]).toBe(true);

    vi.advanceTimersByTime(FRESH_MS + 1);
    await loadBriefing();
    expect(fetchPapers).toHaveBeenCalledTimes(3);
  });

  it('keeps the old items when a refresh fails, and one source failing spares the other', async () => {
    await loadBriefing();
    vi.mocked(fetchPapers).mockRejectedValueOnce(new Error('502 upstream'));
    await loadBriefing({ force: true });
    const { papers, news } = getBriefing();
    expect(papers.status).toBe('error');
    expect(papers.error).toContain('502');
    expect(papers.items).toEqual([paper]);
    expect(news.status).toBe('ready');
  });
});
