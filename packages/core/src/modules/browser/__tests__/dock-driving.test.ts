import { beforeEach, describe, expect, it, vi } from 'vitest';

// The agent channel, captured so a test can play the backend's `done`.
const ws = vi.hoisted(() => ({
  handlers: new Map<string, (msg: { event: string; data?: unknown }) => void>(),
  sent: [] as { channel: string; event: string; data?: unknown }[],
}));
vi.mock('../../../ws', () => ({
  subscribeChannel: (channel: string, fn: (msg: { event: string; data?: unknown }) => void) => {
    ws.handlers.set(channel, fn);
    return () => ws.handlers.delete(channel);
  },
  sendChannel: (channel: string, event: string, data?: unknown) =>
    ws.sent.push({ channel, event, data }),
}));

// Region strips: only a chat that exists has one to open.
const opened = vi.hoisted(() => [] as [string, string][]);
vi.mock('../../../layout/controller', () => ({
  setRegionView: (instanceId: string, viewId: string) => {
    if (!instanceId.startsWith('chat')) return false;
    opened.push([instanceId, viewId]);
    return true;
  },
  focusInstance: () => {},
}));

import { runInTurn, relayTurn } from '../../../agent-turn';
import { isDock, onDockRequest, resetDockForTests, sendToDock, takeDockRequest } from '../dock';
import { drivingTurn, markDriving, resetDrivingForTests, stopDriving } from '../driving';

const DOCK = 'chat-1:right:browser.view';

beforeEach(() => {
  resetDockForTests();
  resetDrivingForTests();
  opened.length = 0;
  ws.sent.length = 0;
});

describe('chat dock hand-off', () => {
  it('opens the strip and hands the URL to that chat’s dock, once', () => {
    expect(sendToDock('chat-1', 'https://example.com')).toBe(true);
    expect(opened).toEqual([['chat-1', 'browser.view']]);
    expect(takeDockRequest('chat-2:right:browser.view')).toBeNull();
    expect(takeDockRequest(DOCK)).toBe('https://example.com');
    expect(takeDockRequest(DOCK)).toBeNull();
  });

  it('a plain browser pane is not a dock and takes nothing', () => {
    sendToDock('chat-1', 'https://example.com');
    expect(isDock('browser.view:https://example.com')).toBe(false);
    expect(isDock(DOCK)).toBe(true);
    expect(takeDockRequest('chat-1')).toBeNull();
  });

  it('refuses a host with no dock, so browser.open can fall back to a pane', () => {
    expect(sendToDock('notes-1', 'https://example.com')).toBe(false);
    expect(takeDockRequest('notes-1:right:browser.view')).toBeNull();
  });

  it('notifies a mounted dock; opening without a URL only opens the strip', () => {
    const heard = vi.fn();
    const off = onDockRequest(heard);
    sendToDock('chat-1', null);
    expect(opened).toHaveLength(1);
    expect(heard).not.toHaveBeenCalled();
    sendToDock('chat-1', 'https://example.com');
    expect(heard).toHaveBeenCalledTimes(1);
    off();
  });
});

describe('calling turn', () => {
  it('is known synchronously inside runInTurn and gone after', () => {
    let seen: string | null = 'unset';
    runInTurn('t1', () => {
      seen = relayTurn();
    });
    expect(seen).toBe('t1');
    expect(relayTurn()).toBeNull();
  });
});

describe('driving marks', () => {
  it('a person (no turn) marks nothing', () => {
    markDriving('page', null);
    expect(drivingTurn('page')).toBeNull();
  });

  it('lasts until that turn’s done, and only that turn’s', () => {
    markDriving('page-a', 't1');
    markDriving('page-b', 't2');
    ws.handlers.get('agent')?.({ event: 'done', data: { turnId: 't1' } });
    expect(drivingTurn('page-a')).toBeNull();
    expect(drivingTurn('page-b')).toBe('t2');
  });

  it('Stop cancels exactly the driving turn and hands the page back', () => {
    markDriving('page', 't3');
    stopDriving('page');
    expect(ws.sent).toEqual([{ channel: 'agent', event: 'cancel', data: { turnId: 't3' } }]);
    expect(drivingTurn('page')).toBeNull();
  });

  it('expires when a turn goes silent without its done', () => {
    vi.useFakeTimers();
    try {
      markDriving('page', 't4');
      vi.advanceTimersByTime(91_000);
      expect(drivingTurn('page')).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
