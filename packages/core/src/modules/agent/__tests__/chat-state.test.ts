/**
 * The chat store exists so a pane remount cannot lose a conversation, so these
 * pin the two properties that make that true: state outlives any one reader, and
 * agents do not share it.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import { chatState, resetChatStates, updateChat } from '../chat-state';

describe('agent chat state', () => {
  beforeEach(() => resetChatStates());

  it('starts empty and unloaded, so the first mount still reads the node', () => {
    const s = chatState('main');
    expect(s.turns).toEqual([]);
    expect(s.activeId).toBeNull();
    expect(s.loaded).toBe(false);
  });

  it('survives the reader: a turn written now is there for the next mount', () => {
    updateChat('main', { activeId: 's1', loaded: true });
    updateChat('main', (prev) => ({ turns: [...prev.turns, { role: 'user', text: 'hi' }] }));
    // Nothing re-reads or re-mounts here on purpose — this IS the remount, from
    // the store's point of view.
    expect(chatState('main').turns).toEqual([{ role: 'user', text: 'hi' }]);
    expect(chatState('main').activeId).toBe('s1');
  });

  it('appends against the live value, not a captured one', () => {
    // Two writers that both read `prev` — the streaming callbacks do exactly this,
    // and a `{turns: [...captured, x]}` patch would drop the first.
    updateChat('main', (p) => ({ turns: [...p.turns, { role: 'user', text: 'a' }] }));
    updateChat('main', (p) => ({ turns: [...p.turns, { role: 'assistant', text: 'b' }] }));
    expect(chatState('main').turns.map((t) => t.text)).toEqual(['a', 'b']);
  });

  it('keys by agent: the coder does not see the orchestrator conversation', () => {
    updateChat('main', { activeId: 'm1', turns: [{ role: 'user', text: 'main' }] });
    updateChat('coder', { activeId: 'c1', turns: [{ role: 'user', text: 'coder' }] });
    expect(chatState('main').turns[0].text).toBe('main');
    expect(chatState('coder').turns[0].text).toBe('coder');
    expect(chatState('coder').activeId).toBe('c1');
  });

  it('leaves untouched fields alone', () => {
    updateChat('main', { turns: [{ role: 'user', text: 'x' }], busy: true });
    updateChat('main', { prompt: 'draft' });
    const s = chatState('main');
    expect(s.turns).toHaveLength(1);
    expect(s.busy).toBe(true);
    expect(s.prompt).toBe('draft');
  });
});
