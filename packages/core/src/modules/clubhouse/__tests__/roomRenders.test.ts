/**
 * What reaches room state, and so re-renders the whole room pane.
 *
 * Agora reports every uid's level five times a second. Publishing those raw made
 * the 4,000-line room pane re-render five times a second in any room with sound,
 * plus once per 600 ms sweep that rebuilt the same empty object. These pin that a
 * change nobody can see notifies nobody.
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({ leaveClubhouseChannel: vi.fn().mockResolvedValue({}) }));
vi.mock('agora-rtc-sdk-ng', () => ({ default: { createCustomAudioTrack: vi.fn() } }));

import { createRoomSession } from '../roomSession';
import { displayVolumes, sameVolumes } from '../volumes';

describe('displayVolumes', () => {
  it('drops quiet uids and buckets the rest', () => {
    expect(displayVolumes({ 1: 0, 2: 5, 3: 6, 4: 31, 5: 99 })).toEqual({ 3: 6, 4: 30, 5: 60 });
  });

  it('keeps the speaking threshold the stage uses (> 8)', () => {
    const shown = displayVolumes({ 1: 8, 2: 9 });
    expect(shown[1]).toBeLessThanOrEqual(8);
    expect(shown[2]).toBeGreaterThan(8);
  });

  it('maps small level jitter to the same picture', () => {
    expect(sameVolumes(displayVolumes({ 7: 40 }), displayVolumes({ 7: 41 }))).toBe(true);
    expect(sameVolumes(displayVolumes({ 7: 70 }), displayVolumes({ 7: 95 }))).toBe(true);
    expect(sameVolumes(displayVolumes({ 7: 20 }), displayVolumes({ 7: 40 }))).toBe(false);
  });
});

describe('sameVolumes', () => {
  it('compares uids and levels', () => {
    expect(sameVolumes({}, {})).toBe(true);
    expect(sameVolumes({ 1: 6 }, { 1: 6 })).toBe(true);
    expect(sameVolumes({ 1: 6 }, { 1: 12 })).toBe(false);
    expect(sameVolumes({ 1: 6 }, { 2: 6 })).toBe(false);
    expect(sameVolumes({ 1: 6 }, {})).toBe(false);
  });
});

describe('session.update', () => {
  it('does not notify when the updater changes nothing', () => {
    const session = createRoomSession();
    const listener = vi.fn();
    session.subscribe(listener);
    session.update('speakingVolumes', (prev) => prev);
    expect(listener).not.toHaveBeenCalled();
    session.update('speakingVolumes', () => ({ 1: 12 }));
    expect(listener).toHaveBeenCalledTimes(1);
  });
});

describe('room details', () => {
  it('outlive a remount and are cleared when the room is left', async () => {
    const session = createRoomSession();
    session.patch({ activeChannel: 'room-x', joined: true });
    session.roomDetails = { channel: 'room-x', users: [] } as unknown as typeof session.roomDetails;
    session.rawVolumes = { 1: 40 };
    await session.teardown();
    expect(session.roomDetails).toBeNull();
    expect(session.rawVolumes).toEqual({});
  });
});
