/**
 * Sends handed to a chat from another pane, addressed or not.
 *
 * With the interpretability suite's Ask strip mounted beside the Agent pane, two
 * chats listen for sends. An "ask about this" from the suite must reach the
 * `interp` agent; a Scrive "fill this outline" must not be answered by it.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../registry', () => ({ registry: { openPanel: vi.fn() } }));

import { registry } from '../../../registry';
import { claimChatSend, hasChatSend, sendInChat } from '../openSession';

afterEach(() => {
  // Drain whatever a failing test left behind.
  while (claimChatSend('interp', true) ?? claimChatSend()) {
    /* drain */
  }
  vi.mocked(registry.openPanel).mockClear();
});

describe('targeted sends', () => {
  it('reaches only the chat pinned to its agent', () => {
    sendInChat('explain layer 17', { agentId: 'interp', open: false });
    expect(hasChatSend('main', false)).toBe(false);
    expect(claimChatSend('main', false)).toBeNull();
    expect(claimChatSend('interp', true)).toBe('explain layer 17');
  });

  it('keeps an unaddressed send away from a pinned chat', () => {
    sendInChat('fill the outline');
    expect(hasChatSend('interp', true)).toBe(false);
    expect(claimChatSend('main', false)).toBe('fill the outline');
  });

  it('lets a general chat take a send addressed to its own agent', () => {
    sendInChat('for main', { agentId: 'main' });
    expect(claimChatSend('main', false)).toBe('for main');
  });

  it('opens the Agent pane unless told the caller hosts its own chat', () => {
    sendInChat('a');
    sendInChat('b', { agentId: 'interp', open: false });
    expect(registry.openPanel).toHaveBeenCalledTimes(1);
  });

  it('skips a send it may not take and claims the next one it may', () => {
    sendInChat('for interp', { agentId: 'interp', open: false });
    sendInChat('for anyone');
    expect(claimChatSend('main', false)).toBe('for anyone');
    expect(claimChatSend('interp', true)).toBe('for interp');
  });
});
