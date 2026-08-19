/**
 * The boot sequence's decisions, with no renderer and no DOM.
 *
 * Imports `../boot` directly rather than the module manifest — the manifest pulls
 * in React panels, and a core vitest run has no jsdom, so importing it dies at
 * module scope (same note as world.test.ts).
 */
import { describe, expect, it } from 'vitest';

import {
  BOOT_STAGES,
  EMPTY_PROGRESS,
  SIGNED_OUT,
  acceptsGameInput,
  advance,
  bootPhase,
  bootProgress,
  currentStage,
  formatBytes,
  isLoaded,
  statusLine,
  type BootProgress,
  type HassaultSession,
} from '../boot';

const DONE: BootProgress = {
  renderer: 1,
  install: 1,
  map: 1,
  mesh: 1,
  reveal: 1,
};

const ENLISTED: HassaultSession = {
  signed_in: true,
  account_id: 'local:1',
  display_name: 'ada',
  username: 'ada-prime',
  enlisted: true,
};

describe('progress', () => {
  it('runs from 0 to 1 across the stages', () => {
    expect(bootProgress(EMPTY_PROGRESS)).toBe(0);
    expect(bootProgress(DONE)).toBe(1);
  });

  it('weights the map download heaviest — it is the stage that actually varies', () => {
    const weights = Object.fromEntries(BOOT_STAGES.map((s) => [s.id, s.weight]));
    expect(weights.map).toBeGreaterThan(weights.renderer);
    expect(weights.map).toBeGreaterThan(weights.mesh);
  });

  it('never runs backwards', () => {
    const half = advance(EMPTY_PROGRESS, { renderer: 1, map: 0.8 });
    // A re-fetch (HTTP cache hit, map switch) reporting less must not rewind the
    // bar — a bar that jumps back reads as a fault even when nothing is wrong.
    const later = advance(half, { map: 0.2 });
    expect(later.map).toBe(0.8);
    expect(bootProgress(later)).toBe(bootProgress(half));
  });

  it('clamps nonsense instead of poisoning the total', () => {
    // A zero-length map yields loaded/total = NaN; without clamping the whole
    // weighted sum becomes NaN and the bar freezes at "NaN%".
    const bad = advance(EMPTY_PROGRESS, { map: Number.NaN, mesh: 5, renderer: -1 });
    expect(bad.map).toBe(0);
    expect(bad.mesh).toBe(1);
    expect(bad.renderer).toBe(0);
    expect(Number.isFinite(bootProgress(bad))).toBe(true);
  });
});

describe('status line', () => {
  it('names the stage actually in flight', () => {
    expect(currentStage(EMPTY_PROGRESS)?.id).toBe('renderer');
    expect(statusLine(EMPTY_PROGRESS)).toBe('LOADING RENDERER');

    const meshing = advance(EMPTY_PROGRESS, { renderer: 1, install: 1, map: 1 });
    expect(currentStage(meshing)?.id).toBe('mesh');
    expect(statusLine(meshing)).toBe('COMPILING GEOMETRY');
  });

  it('shows an error over the stage', () => {
    expect(statusLine(EMPTY_PROGRESS, 'no map named ac_nope')).toBe('NO MAP NAMED AC_NOPE');
  });

  it('is done only when every stage is', () => {
    expect(isLoaded(EMPTY_PROGRESS)).toBe(false);
    expect(isLoaded(advance(DONE, { reveal: 0.99 }))).toBe(true); // monotonic
    expect(isLoaded({ ...DONE, reveal: 0.99 })).toBe(false);
  });
});

describe('phases', () => {
  it('decides nothing about the player until the world is standing', () => {
    // The sign-in screen is meant to sit over the finished map, so a signed-out
    // user still watches the whole build first.
    expect(bootPhase(EMPTY_PROGRESS, SIGNED_OUT, false)).toBe('loading');
    expect(bootPhase(EMPTY_PROGRESS, ENLISTED, true)).toBe('loading');
  });

  it('asks for an account, then a username', () => {
    expect(bootPhase(DONE, SIGNED_OUT, false)).toBe('signin');
    // Signed in but no username: sign-up is not finished, and the backend refuses
    // the join too (channel._signed_in_username).
    const noUsername: HassaultSession = {
      signed_in: true,
      username: null,
      enlisted: false,
    };
    expect(bootPhase(DONE, noUsername, false)).toBe('enlist');
    expect(bootPhase(DONE, ENLISTED, false)).toBe('menu');
    expect(bootPhase(DONE, ENLISTED, true)).toBe('playing');
  });

  it('returns to the menu when a deployed player leaves the world', () => {
    // `deployed` is a flag, not a latch: Escape → Exit to menu clears it, and a
    // game you can only enter is a game you have to close the pane to leave.
    expect(bootPhase(DONE, ENLISTED, true)).toBe('playing');
    expect(bootPhase(DONE, ENLISTED, false)).toBe('menu');
  });

  it('accepts game input only while playing', () => {
    // Pointer lock is requested nowhere else, so a click on an email field can
    // never be swallowed by it.
    expect(acceptsGameInput('playing')).toBe(true);
    for (const phase of ['loading', 'signin', 'enlist', 'menu'] as const) {
      expect(acceptsGameInput(phase)).toBe(false);
    }
  });
});

describe('formatBytes', () => {
  it('reads as a download', () => {
    expect(formatBytes(1_048_576, 3_984_588)).toBe('1.0 / 3.8 MB');
  });

  it('uses KB for a real map — nine 16 KB planes is the common case', () => {
    // A 128-cube map is 128*128*9 = 147 456 bytes. In MB the entire load reads
    // "0.0 / 0.1", which tells you nothing.
    expect(formatBytes(98_304, 147_456)).toBe('96 / 144 KB');
  });

  it('picks the unit from the total so it cannot switch mid-download', () => {
    // Early in a large download `loaded` is small, but the unit must already be
    // the one the finished number will use.
    expect(formatBytes(1024, 4_194_304)).toBe('0.0 / 4.0 MB');
  });

  it('drops the total when the response did not say', () => {
    expect(formatBytes(1_048_576, null)).toBe('1.0 MB');
  });
});
