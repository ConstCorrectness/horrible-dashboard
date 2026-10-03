/**
 * The composer's X counter. The cases are the backend's (`test_x_weighted_length`,
 * `test_x_link_goes_in_a_reply_and_the_cost_counts_it` in test_scrive_outbox.py), so
 * what the composer shows while typing is what approval enforces.
 */
import { describe, expect, it } from 'vitest';

import type { XPayload } from '../api';
import { POST_URL, xCost, xLength, xThread } from '../social/xcount';

describe('xLength', () => {
  it('weighs text the way X does', () => {
    expect(xLength('a'.repeat(280))).toBe(280);
    expect(xLength('日本語')).toBe(6);
    expect(xLength('go https://example.com/a/very/long/path?q=1 now')).toBe(3 + 23 + 4);
    expect(xLength('👍🏽')).toBe(2);
    expect(xLength(POST_URL)).toBe(23);
    expect(xLength('café')).toBe(4);
  });
});

describe('xThread and xCost', () => {
  const payload: XPayload = {
    posts: [
      { text: 'one', media: [] },
      { text: 'two', media: [] },
      { text: '  ', media: [] },
    ],
    link: POST_URL,
    link_in_reply: null,
  };

  it('puts the link in a reply by default, inline when asked', () => {
    expect(xThread(payload).map((p) => p.text)).toEqual(['one', 'two', POST_URL]);
    expect(xThread({ ...payload, link_in_reply: false })[0].text).toBe(`one\n\n${POST_URL}`);
    expect(xThread(payload, false)[0].text).toBe(`one\n\n${POST_URL}`);
  });

  it('prices each post, a post with a link at the URL rate', () => {
    expect(xCost(payload)).toEqual({ usd: 0.23, posts: 3, withUrl: 1 });
    expect(xCost({ ...payload, link: '' })).toEqual({ usd: 0.03, posts: 2, withUrl: 0 });
  });
});
