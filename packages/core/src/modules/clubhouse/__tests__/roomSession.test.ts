/**
 * The room is owned by the pane, not by a mounted component.
 *
 * The regression these guard: `useClubhouseVoice` tore the connection down in an
 * unmount cleanup, and a pane unmounts on every tab switch, split and workspace
 * change. That called `leaveClubhouseChannel()` against the real API, so the whole
 * room saw you leave because you looked at another tab.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const leaveSpy = vi.fn().mockResolvedValue({});
vi.mock('../api', () => ({ leaveClubhouseChannel: (c: string) => leaveSpy(c) }));
vi.mock('agora-rtc-sdk-ng', () => ({ default: { createCustomAudioTrack: vi.fn() } }));

import {
  closePaneSession,
  paneSession,
  paneSessionKey,
  resetPaneSessionsForTests,
} from '../../../layout/pane-lifetime';
import { createRoomSession, disposeRoomSession, type ClubhouseRoomSession } from '../roomSession';

const KEY = paneSessionKey('ws1', 'clubhouse.account#1');

function open(): ClubhouseRoomSession {
  const session = paneSession(KEY, createRoomSession, disposeRoomSession);
  session.patch({ activeChannel: 'room-x', joined: true });
  return session;
}

describe('the room survives unmounts', () => {
  beforeEach(() => {
    leaveSpy.mockClear();
    resetPaneSessionsForTests();
  });

  it('hands every later mount the same live room', () => {
    const first = open();
    // A remount asks for the session again; it must not create a second connection.
    const second = paneSession(KEY, createRoomSession, disposeRoomSession);
    expect(second).toBe(first);
    expect(second.state.activeChannel).toBe('room-x');
    expect(leaveSpy).not.toHaveBeenCalled();
  });

  it('keeps room state across a remount', () => {
    const session = open();
    session.set('comments', [
      { id: '1', userName: 'Ada', userPhoto: null, text: 'hi', timestamp: 0 },
    ]);
    session.updateLiveUser(7, { isSpeaker: true });

    // Whatever the component held is gone; the session is what a remount reads.
    const remounted = paneSession(KEY, createRoomSession, disposeRoomSession);
    expect(remounted.state.comments).toHaveLength(1);
    expect(remounted.state.liveUsers).toEqual([
      { userId: 7, handRaised: false, isSpeaker: true, isMuted: false },
    ]);
  });

  it('leaves the Clubhouse room only when the pane actually closes', async () => {
    open();
    expect(leaveSpy).not.toHaveBeenCalled();

    closePaneSession(KEY);
    await vi.waitFor(() => expect(leaveSpy).toHaveBeenCalledWith('room-x'));
  });

  it('leaves exactly once', async () => {
    /** The old `[activeChannel]` dep re-ran the cleanup against a stale closure
     *  after a normal leave had already nulled it, so the API was hit twice. */
    const session = open();
    await session.teardown();
    await session.teardown();
    expect(leaveSpy).toHaveBeenCalledTimes(1);
  });

  it('does not call the API for a room it never joined', async () => {
    const session = paneSession(KEY, createRoomSession, disposeRoomSession);
    await session.teardown();
    expect(leaveSpy).not.toHaveBeenCalled();
  });

  it('resets to an empty room after teardown', async () => {
    const session = open();
    await session.teardown();
    expect(session.state.joined).toBe(false);
    expect(session.state.activeChannel).toBeNull();
  });

  it('notifies subscribers on change so any mount can render it', () => {
    const session = paneSession(KEY, createRoomSession, disposeRoomSession);
    const seen = vi.fn();
    const unsubscribe = session.subscribe(seen);
    session.patch({ joined: true });
    expect(seen).toHaveBeenCalledTimes(1);
    // Identity must change, or useSyncExternalStore will not re-render.
    const before = session.state;
    session.patch({ isMuted: true });
    expect(session.state).not.toBe(before);
    unsubscribe();
    session.patch({ handRaised: true });
    expect(seen).toHaveBeenCalledTimes(2);
  });

  it('reports each distinct speech failure once', () => {
    /** The STT path fires per audio chunk; without this the same missing-extra
     *  error would raise an alarm every few seconds. */
    const session = paneSession(KEY, createRoomSession, disposeRoomSession);
    const notify = vi.fn();
    session.reportVoiceError('no whisper', notify);
    session.reportVoiceError('no whisper', notify);
    session.reportVoiceError('no ffmpeg', notify);
    expect(notify).toHaveBeenCalledTimes(2);
  });
});

/**
 * Switching rooms is teardown-then-build, and both halves await.
 *
 * The regression: `joinRoom` built straight over a live connection, so the room you
 * left kept its Agora client, PubNub subscription, ping/volume intervals, recorder
 * and VAD loop. The VAD loop mutates `session.sttRecorder` / `session.sttChunk`,
 * which by then belong to the *new* room, and its idle branch fires on the silence
 * of the old one — so it marked the new room's audio `discard` every ~1.5s and
 * nothing was ever transcribed. One extra ghost per switch.
 */
describe('a switch cannot leave the previous room running', () => {
  beforeEach(() => {
    leaveSpy.mockClear();
    resetPaneSessionsForTests();
  });

  it('clears every interval handle, so no loop outlives the room', async () => {
    const session = open();
    session.pingInterval = setInterval(() => {}, 1000) as unknown as ReturnType<
      typeof setInterval
    >;
    session.volumeInterval = setInterval(() => {}, 1000) as unknown as ReturnType<
      typeof setInterval
    >;
    session.vadInterval = setInterval(() => {}, 1000) as unknown as ReturnType<typeof setInterval>;

    await session.teardown();

    expect(session.pingInterval).toBeNull();
    expect(session.volumeInterval).toBeNull();
    // The one that silently ate the next room's audio.
    expect(session.vadInterval).toBeNull();
  });

  it('runs serialized work one at a time, so two switches cannot interleave', async () => {
    const session = open();
    const events: string[] = [];
    const slow = async (tag: string) => {
      events.push(`${tag}:start`);
      await new Promise((r) => setTimeout(r, 10));
      events.push(`${tag}:end`);
    };

    await Promise.all([
      session.serialize(() => slow('first')),
      session.serialize(() => slow('second')),
    ]);

    expect(events).toEqual(['first:start', 'first:end', 'second:start', 'second:end']);
  });

  it('does not wedge the queue when a join fails', async () => {
    const session = open();
    await expect(
      session.serialize(() => Promise.reject(new Error('join blew up'))),
    ).rejects.toThrow('join blew up');
    // The next switch must still run — a failed join that jammed the queue would
    // make the pane permanently unable to enter another room.
    await expect(session.serialize(async () => 'ok')).resolves.toBe('ok');
  });
});
