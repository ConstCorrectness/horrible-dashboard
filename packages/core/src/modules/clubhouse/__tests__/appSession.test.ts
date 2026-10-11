/**
 * One Clubhouse room for the whole app, which any Clubhouse pane's close leaves.
 *
 * The regression: the room was pane-scoped and `singleton` means one per
 * *desktop*, so Clubhouse opened on a second desktop got its own empty session.
 * Closing that window tore down a room that had never joined anything while the
 * first desktop's call kept playing — out of sight, with nothing on screen able to
 * stop it short of closing the app.
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
import {
  getClubhousePresence,
  leaveClubhouse,
  resetClubhousePresenceForTests,
  subscribeClubhousePresence,
} from '../presence';
import {
  clubhouseSession,
  leaveClubhouseRoom,
  resetClubhouseSessionForTests,
} from '../roomSession';

beforeEach(() => {
  leaveSpy.mockClear();
  resetPaneSessionsForTests();
  resetClubhouseSessionForTests();
  resetClubhousePresenceForTests();
});

function join(): void {
  clubhouseSession().patch({ activeChannel: 'room-x', joined: true });
}

/** What `useClubhouseVoice` registers for each Clubhouse pane it renders in. */
function mountPane(workspace: string, instance: string): string {
  const key = paneSessionKey(workspace, instance);
  paneSession(
    key,
    () => true,
    () => void leaveClubhouseRoom(),
  );
  return key;
}

describe('the app has one Clubhouse room', () => {
  it('is the same session on every desktop', () => {
    expect(clubhouseSession()).toBe(clubhouseSession());
  });

  it('closing a Clubhouse pane on another desktop leaves the room', async () => {
    mountPane('desk1', 'clubhouse.account#1');
    join();
    const second = mountPane('desk2', 'clubhouse.account#1');

    closePaneSession(second);
    await vi.waitFor(() => expect(leaveSpy).toHaveBeenCalledWith('room-x'));
    expect(clubhouseSession().state.joined).toBe(false);
  });

  it('a desktop or tab switch (unmount, no close) does not leave', async () => {
    mountPane('desk1', 'clubhouse.account#1');
    join();
    // Remounting asks for the same pane session again; nothing is disposed.
    mountPane('desk1', 'clubhouse.account#1');
    await Promise.resolve();
    expect(leaveSpy).not.toHaveBeenCalled();
  });

  it('closing an idle pane changes nothing', async () => {
    const key = mountPane('desk1', 'clubhouse.account#1');
    closePaneSession(key);
    await leaveClubhouseRoom();
    expect(leaveSpy).not.toHaveBeenCalled();
    expect(clubhouseSession().state.loading).toBe(false);
  });
});

describe('the shell can see and end the room without loading it', () => {
  it('publishes presence as the room changes', () => {
    const seen = vi.fn();
    subscribeClubhousePresence(seen);
    join();
    clubhouseSession().updateLiveUser(7, { isSpeaker: true });
    expect(getClubhousePresence()).toMatchObject({ joined: true, people: 1, muted: false });
    expect(seen).toHaveBeenCalled();
  });

  it('does not re-notify for changes the indicator does not show', () => {
    join();
    const seen = vi.fn();
    subscribeClubhousePresence(seen);
    // The volume meter churns this many times a second.
    clubhouseSession().patch({ speakingVolumes: { 7: 0.4 } });
    expect(seen).not.toHaveBeenCalled();
  });

  it("the indicator's Leave leaves and clears presence", async () => {
    join();
    await leaveClubhouse();
    expect(leaveSpy).toHaveBeenCalledWith('room-x');
    expect(getClubhousePresence().joined).toBe(false);
  });

  it('Leave before Clubhouse ever loaded is a no-op', async () => {
    await expect(leaveClubhouse()).resolves.toBeUndefined();
  });
});
