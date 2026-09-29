import { describe, expect, it } from 'vitest';

import { grenadeArcAllowed } from '../menu-panels';

describe('grenadeArcAllowed', () => {
  it('draws in Training, where there is no room at all', () => {
    expect(grenadeArcAllowed(false, '', false)).toBe(true);
  });

  it('draws in a room we host ourselves', () => {
    expect(grenadeArcAllowed(true, '', false)).toBe(true);
  });

  it('never draws in ranked or on someone else’s node', () => {
    expect(grenadeArcAllowed(true, '', true)).toBe(false);
    expect(grenadeArcAllowed(true, 'friend-node', false)).toBe(false);
  });
});
