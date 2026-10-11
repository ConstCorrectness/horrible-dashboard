/**
 * The match belongs to the pane, not the mounted component.
 *
 * The regression: `HorribleAssaultPanel` called `session.disconnect()` in its mount
 * cleanup, and a pane unmounts on every tab switch, desktop switch and split. That
 * sent `leave` — glancing at another tab dropped you out of the match and the
 * lobby, hung up lobby voice, and brought you back to the main menu.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const sent: Array<{ event: string; data: unknown }> = [];
vi.mock('../../../ws', () => ({
  sendChannel: (_channel: string, event: string, data: unknown) => sent.push({ event, data }),
  subscribeChannel: () => () => {},
}));
vi.mock('../invite-notify', () => ({ clearInviteNotification: () => {} }));
const voiceStop = vi.fn();
vi.mock('../lobby-voice', () => ({
  lobbyVoice: {
    stop: () => voiceStop(),
    sendSignal: () => {},
    announce: () => {},
  },
}));

import {
  closePaneSession,
  paneSession,
  paneSessionKey,
  resetPaneSessionsForTests,
} from '../../../layout/pane-lifetime';
import { createPaneMatch, disposePaneMatch, resumedMatch } from '../pane-match';
import type { MatchSession } from '../session';

const KEY = paneSessionKey('desk', 'hassault.play#1');

function mount(): MatchSession {
  return paneSession(KEY, createPaneMatch, disposePaneMatch);
}

/** Put the session in a match the way the server's welcome would. */
function inMatch(session: MatchSession): void {
  session.join('hd_bank', 'rob');
  session.state.status = 'joined';
  session.state.room = 'r1';
}

beforeEach(() => {
  sent.length = 0;
  voiceStop.mockClear();
  resetPaneSessionsForTests();
});

describe('the hassault match session', () => {
  it('survives a tab switch: the remount gets the same live session', () => {
    const first = mount();
    inMatch(first);

    const again = mount();
    expect(again).toBe(first);
    expect(again.state.status).toBe('joined');
    expect(sent.some((m) => m.event === 'leave')).toBe(false);
    expect(voiceStop).not.toHaveBeenCalled();
  });

  it('lands a remount back in the match, on its map', () => {
    const session = mount();
    expect(resumedMatch(session)).toBeNull();
    inMatch(session);
    expect(resumedMatch(mount())?.map).toBe('hd_bank');
  });

  it('a real close leaves the match and hangs up voice', () => {
    const session = mount();
    inMatch(session);

    closePaneSession(KEY);
    expect(sent.some((m) => m.event === 'leave')).toBe(true);
    expect(voiceStop).toHaveBeenCalledTimes(1);
    expect(session.state.status).toBe('idle');
  });

  it('a close detaches the UI callbacks, so a dead pane is never called', () => {
    const session = mount();
    const onChange = vi.fn();
    session.onChange = onChange;
    closePaneSession(KEY);
    onChange.mockClear();
    session.leave();
    session.join('hd_bank', 'rob');
    expect(onChange).not.toHaveBeenCalled();
  });

  it('snapshot hands out fresh arrays, so React re-renders on a remount', () => {
    const session = mount();
    const a = session.snapshot();
    const b = session.snapshot();
    expect(a).not.toBe(b);
    expect(a.peers).not.toBe(session.state.peers);
    expect(a).toEqual(b);
  });
});
