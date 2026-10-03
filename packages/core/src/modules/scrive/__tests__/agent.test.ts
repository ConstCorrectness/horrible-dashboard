/**
 * The agent's side of a Scrive page: what its buttons say to the agent, which
 * blocks an agent edit changed, and the chat hand-off those buttons use.
 */
import { describe, expect, it } from 'vitest';

import { claimChatSend, hasChatSend, sendInChat } from '../../agent/openSession';
import type { Outline } from '../api';
import { blockChanges } from '../myst/changes';
import { fenceFor, fillPrompt, generatePrompt, pendingPrompt, selectionPrompt } from '../prompts';

const PAGE = `---
title: T
---

Lead.

## One

:::{pending}
Explain one.
:::

## Two

Two body.
`;

describe('blockChanges', () => {
  it('marks the blocks a fill wrote, and nothing else', () => {
    const filled = PAGE.replace(':::{pending}\nExplain one.\n:::', 'First para.\n\nSecond para.');
    const changes = blockChanges(PAGE, filled);
    // The two new paragraphs, at their lines in the new file.
    expect(changes.lines).toEqual([9, 11]);
    expect(changes.changed).toBe(2);
    expect(changes.removed).toBe(0);
    expect(changes.frontmatter).toBe(false);
  });

  it('does not count a block that only moved', () => {
    const moved = PAGE.replace('Lead.\n\n', '') + '\nLead.\n';
    expect(blockChanges(PAGE, moved).changed).toBe(0);
  });

  it('counts removals beyond the blocks that replaced them, and frontmatter apart', () => {
    const cut = PAGE.replace('## Two\n\nTwo body.\n', '').replace('title: T', 'title: U');
    const changes = blockChanges(PAGE, cut);
    expect(changes.changed).toBe(0);
    expect(changes.removed).toBe(2);
    expect(changes.frontmatter).toBe(true);
  });
});

describe('prompts', () => {
  const outline: Outline = {
    id: 'abc123abc123',
    site: 'blog',
    title: 'Priors',
    kind: 'post',
    template: 'deep-dive',
    inputs: {},
    prompt: 'explain priors',
    lead: '',
    description: '',
    tags: [],
    sections: [],
    status: 'approved',
    page: 'posts/p.md',
    created_at: 0,
    updated_at: 0,
  };

  it('asks for an outline and a stop, never for a page', () => {
    const text = generatePrompt({
      site: 'blog',
      request: 'priors',
      kind: 'post',
      template: 'deep-dive',
    });
    expect(text).toContain('scrive.proposeOutline');
    expect(text).toContain('stop');
    expect(text).not.toMatch(/scrive\.(createPage|fillSection)/);
  });

  it('tells the fill turn which page, which template guidance, and to review', () => {
    const text = fillPrompt(outline, 'posts/p.md');
    expect(text).toContain('blog/posts/p.md');
    expect(text).toContain('scrive.fillSection');
    expect(text).toContain('template "deep-dive"');
    expect(text).toContain('scrive.critiquePage');
    expect(text).toContain('explain priors');
  });

  it('quotes a selection in a fence it cannot break out of', () => {
    expect(fenceFor('plain')).toBe('```');
    expect(fenceFor('has ```` four')).toBe('`````');
    const text = selectionPrompt({
      site: 'blog',
      path: 'posts/p.md',
      selection: '```python\nx\n```',
      excerpt: 'x',
      instruction: 'rename x',
    });
    expect(text).toContain('````myst\n```python\nx\n```\n````');
    expect(text).toContain('The selected words: "x"');
    expect(text).toContain('keep everything it does not mention');
  });

  it('never asks the agent to publish, post or send', () => {
    const all = [
      generatePrompt({ site: 's', request: 'r', kind: 'page' }),
      fillPrompt(outline, 'posts/p.md'),
      selectionPrompt({ site: 's', path: 'p.md', selection: 'a', instruction: 'b' }),
      pendingPrompt('s', 'p.md', 'intent'),
    ].join('\n');
    expect(all).not.toMatch(/\b(publish|tweet|send it|post it)\b/i);
  });
});

describe('sendInChat', () => {
  it('queues sends in order, and each is taken once', () => {
    while (claimChatSend()) {
      // drain anything a previous test left
    }
    sendInChat('first');
    sendInChat('second');
    expect(hasChatSend()).toBe(true);
    expect(claimChatSend()).toBe('first');
    expect(claimChatSend()).toBe('second');
    expect(claimChatSend()).toBeNull();
    expect(hasChatSend()).toBe(false);
  });
});
