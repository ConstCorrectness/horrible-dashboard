import { describe, expect, it, vi } from 'vitest';

import { startPlayback, watchFirstFrame, type FrameSource } from '../playback';

/**
 * The mirror pane's video, minus the DOM.
 *
 * Both halves fail as the same symptom — a black box — which is why they are
 * pinned: a swallowed autoplay refusal leaves a paused element, and treating the
 * track as the picture declares "live" over a connection that never decodes.
 */

function playable(refuse: Array<'sound' | 'muted'>) {
  const el = {
    muted: false,
    plays: [] as boolean[],
    play: vi.fn(async () => {
      el.plays.push(el.muted);
      if (refuse.includes(el.muted ? 'muted' : 'sound')) throw new Error('NotAllowedError');
    }),
  };
  return el;
}

describe('startPlayback', () => {
  it('plays with sound when the engine allows it', async () => {
    const el = playable([]);
    expect(await startPlayback(el)).toBe('sound');
    expect(el.muted).toBe(false);
  });

  it('falls back to muted when sound is refused', async () => {
    const el = playable(['sound']);
    expect(await startPlayback(el)).toBe('muted');
    expect(el.plays).toEqual([false, true]);
    expect(el.muted).toBe(true);
  });

  it('reports blocked when even muted playback is refused', async () => {
    const el = playable(['sound', 'muted']);
    expect(await startPlayback(el)).toBe('blocked');
  });

  it('leaves the element alone once a newer attempt has superseded it', async () => {
    // Found live: React's double effect run swaps `srcObject`, which aborts the
    // first `play()`. The first attempt then muted the element under the second,
    // which had started with sound — muted audio and no Unmute to say so.
    let current = true;
    const el = {
      muted: false,
      play: vi.fn(async () => {
        current = false;
        throw new Error('AbortError: interrupted by a new load request');
      }),
    };
    expect(await startPlayback(el, () => current)).toBeNull();
    expect(el.muted).toBe(false);
    expect(el.play).toHaveBeenCalledTimes(1);
  });

  it('starts unmuted even on an element left muted by a previous stream', async () => {
    const el = playable([]);
    el.muted = true;
    expect(await startPlayback(el)).toBe('sound');
    expect(el.plays).toEqual([false]);
  });
});

function frameSource(opts: { readyState?: number; rvfc?: boolean } = {}) {
  const listeners = new Set<() => void>();
  const callbacks = new Map<number, () => void>();
  let next = 1;
  const el: FrameSource & {
    fireLoaded(): void;
    firePainted(): void;
    listeners: Set<() => void>;
    callbacks: Map<number, () => void>;
  } = {
    readyState: opts.readyState ?? 0,
    addEventListener: (_type, l) => listeners.add(l),
    removeEventListener: (_type, l) => listeners.delete(l),
    listeners,
    callbacks,
    fireLoaded: () => [...listeners].forEach((l) => l()),
    firePainted: () => {
      const cbs = [...callbacks.values()];
      callbacks.clear();
      cbs.forEach((cb) => cb());
    },
  };
  if (opts.rvfc !== false) {
    el.requestVideoFrameCallback = (cb) => {
      callbacks.set(next, cb);
      return next++;
    };
    el.cancelVideoFrameCallback = (h) => callbacks.delete(h);
  }
  return el;
}

describe('watchFirstFrame', () => {
  it('waits for a frame rather than firing on attach', () => {
    const el = frameSource();
    const onFrame = vi.fn();
    watchFirstFrame(el, onFrame);
    expect(onFrame).not.toHaveBeenCalled();
  });

  it('fires once on loadeddata, which a backgrounded tab still delivers', () => {
    const el = frameSource({ rvfc: false });
    const onFrame = vi.fn();
    watchFirstFrame(el, onFrame);
    el.fireLoaded();
    el.fireLoaded();
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(el.listeners.size).toBe(0);
  });

  it('fires on the first painted frame and drops the other signal', () => {
    const el = frameSource();
    const onFrame = vi.fn();
    watchFirstFrame(el, onFrame);
    el.firePainted();
    el.fireLoaded();
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(el.listeners.size).toBe(0);
  });

  it('cancels the frame callback when loadeddata wins', () => {
    const el = frameSource();
    watchFirstFrame(el, () => {});
    expect(el.callbacks.size).toBe(1);
    el.fireLoaded();
    expect(el.callbacks.size).toBe(0);
  });

  it('fires at once for an element that already holds a frame', () => {
    // Neither event fires again for a frame already decoded.
    const el = frameSource({ readyState: 2 });
    const onFrame = vi.fn();
    watchFirstFrame(el, onFrame);
    expect(onFrame).toHaveBeenCalledTimes(1);
    expect(el.listeners.size).toBe(0);
  });

  it('never fires after it is cancelled', () => {
    const el = frameSource();
    const onFrame = vi.fn();
    const cancel = watchFirstFrame(el, onFrame);
    cancel();
    el.fireLoaded();
    el.firePainted();
    expect(onFrame).not.toHaveBeenCalled();
    expect(el.listeners.size).toBe(0);
    expect(el.callbacks.size).toBe(0);
  });
});
