/**
 * The avatar pool: a scene is built once and handed from mount to mount.
 *
 * The bug these guard: every open of the agent pane built the scene from nothing
 * (fetch, parse, texture upload, shader compile) and blocked the page for over a
 * second, because unmounting destroyed it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  acquireAvatar,
  AVATAR_PARK_MS,
  avatarKey,
  releaseAvatar,
  setAvatarBuilderForTests,
  shrinkFactor,
  type AvatarInstance,
  type AvatarOptions,
} from '../avatar-scene';

const OPTS: AvatarOptions = {
  size: 120,
  modelUrl: '/a.glb',
  moods: { happy: '/h.glb', error: '/e.glb' },
  fallbackMood: 'happy',
};

interface Fake extends AvatarInstance {
  log: string[];
}

let built: Fake[] = [];

function fake(): Fake {
  const log: string[] = [];
  return {
    log,
    canvas: { remove: () => log.push('remove') } as unknown as HTMLCanvasElement,
    setMood: (mood) => void log.push(`mood:${mood}`),
    start: () => void log.push('start'),
    stop: () => void log.push('stop'),
    dispose: () => void log.push('dispose'),
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  built = [];
  setAvatarBuilderForTests(async () => {
    const instance = fake();
    built.push(instance);
    return instance;
  });
});

afterEach(() => {
  setAvatarBuilderForTests(null);
  vi.useRealTimers();
});

describe('avatar pool', () => {
  it('hands a released scene to the next mount instead of building another', async () => {
    const first = acquireAvatar(OPTS);
    (await first).start();
    releaseAvatar(OPTS, first);
    const second = acquireAvatar(OPTS);
    expect(await second).toBe(await first);
    expect(built).toHaveLength(1);
    expect(built[0].log).not.toContain('dispose');
  });

  it('builds once across a mount, unmount, mount in the same tick (StrictMode)', async () => {
    const first = acquireAvatar(OPTS);
    releaseAvatar(OPTS, first);
    const second = acquireAvatar(OPTS);
    second.then((instance) => instance.start());
    await second;
    await Promise.resolve();
    expect(built).toHaveLength(1);
    // The release's stop lands before the adopting mount's start.
    expect(built[0].log).toEqual(['stop', 'remove', 'start']);
  });

  it('keeps one parked scene per key and disposes a second', async () => {
    const a = acquireAvatar(OPTS);
    const b = acquireAvatar(OPTS);
    releaseAvatar(OPTS, a);
    releaseAvatar(OPTS, b);
    await a;
    await b;
    await Promise.resolve();
    expect(built[0].log).not.toContain('dispose');
    expect(built[1].log).toContain('dispose');
  });

  it('gives the context back after the park time', async () => {
    const a = acquireAvatar(OPTS);
    releaseAvatar(OPTS, a);
    await a;
    await vi.advanceTimersByTimeAsync(AVATAR_PARK_MS);
    expect(built[0].log).toContain('dispose');
    await acquireAvatar(OPTS);
    expect(built).toHaveLength(2);
  });

  it('does not share a scene between different avatars', async () => {
    const a = acquireAvatar(OPTS);
    releaseAvatar(OPTS, a);
    await acquireAvatar({ ...OPTS, size: 180 });
    expect(built).toHaveLength(2);
  });
});

describe('avatarKey', () => {
  it('compares moods by content, not identity or order', () => {
    const reordered = { ...OPTS, moods: { error: '/e.glb', happy: '/h.glb' } };
    expect(avatarKey(reordered)).toBe(avatarKey(OPTS));
    expect(avatarKey({ ...OPTS, moods: { happy: '/other.glb' } })).not.toBe(avatarKey(OPTS));
  });
});

describe('shrinkFactor', () => {
  it('scales the longest edge down to the cap and never up', () => {
    expect(shrinkFactor(1024, 1024, 512)).toBe(0.5);
    expect(shrinkFactor(2048, 1024, 512)).toBe(0.25);
    expect(shrinkFactor(512, 256, 512)).toBe(1);
    expect(shrinkFactor(0, 0, 512)).toBe(1);
  });
});
