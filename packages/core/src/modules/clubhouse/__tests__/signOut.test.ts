/**
 * Signing out leaves the room first.
 *
 * The regression: Disconnect deleted the session while the app-wide room stayed
 * up — Agora kept playing, PubNub stayed subscribed, and the leave that a later
 * teardown sent was refused as "not connected" because its token was gone.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const order: string[] = [];
vi.mock('../../../api', () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiDelete: vi.fn(async (path: string) => {
    order.push(`DELETE ${path}`);
    return { connected: false };
  }),
}));

import { disconnectClubhouse } from '../api';
import { resetClubhousePresenceForTests, setClubhouseLeaver } from '../presence';

beforeEach(() => {
  order.length = 0;
  resetClubhousePresenceForTests();
});

describe('disconnectClubhouse', () => {
  it('leaves the room before the session is deleted', async () => {
    setClubhouseLeaver(async () => {
      order.push('leave');
    });

    const status = await disconnectClubhouse();

    expect(order).toEqual(['leave', 'DELETE /clubhouse/auth']);
    expect(status.connected).toBe(false);
  });

  it('still signs out when leaving fails', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    setClubhouseLeaver(async () => {
      throw new Error('agora gone');
    });

    await disconnectClubhouse();

    expect(order).toEqual(['DELETE /clubhouse/auth']);
    warn.mockRestore();
  });

  it('signs out without a room ever having loaded', async () => {
    await disconnectClubhouse();
    expect(order).toEqual(['DELETE /clubhouse/auth']);
  });
});
