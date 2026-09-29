import { describe, expect, it } from 'vitest';

import { followTarget, setFollowTarget, setTraceDrawer, traceDrawer } from '../chat-state';

describe('trace drawer', () => {
  it('opens, closes and keeps its width without storage', () => {
    // No DOM here: localStorage is absent, and the store must still work.
    setTraceDrawer({ open: true, width: 640 });
    expect(traceDrawer()).toEqual({ open: true, width: 640 });
    const same = traceDrawer();
    setTraceDrawer({ open: true });
    expect(traceDrawer()).toBe(same); // no-op patches keep the snapshot stable
    setTraceDrawer({ open: false });
    expect(traceDrawer()).toEqual({ open: false, width: 640 });
  });
});

describe('follow target', () => {
  it('follows the agent last used, keeping a pin on the same agent', () => {
    setFollowTarget('main');
    expect(followTarget()).toEqual({ agentId: 'main', pinnedTurnId: null });

    setFollowTarget('main', 't1');
    expect(followTarget()?.pinnedTurnId).toBe('t1');

    // Focusing the same chat again must not drop the pin.
    setFollowTarget('main');
    expect(followTarget()?.pinnedTurnId).toBe('t1');

    // Another agent's chat starts unpinned.
    setFollowTarget('coder');
    expect(followTarget()).toEqual({ agentId: 'coder', pinnedTurnId: null });

    setFollowTarget('coder', 't9');
    setFollowTarget('coder', null);
    expect(followTarget()?.pinnedTurnId).toBeNull();
  });
});
